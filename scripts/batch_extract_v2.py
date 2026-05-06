#!/usr/bin/env python3
"""
Two-phase batch poster extraction with batched GPU inference.

Phase 1 (CPU+GPU): Extract raw text from ALL posters first
  - pdfalto for text-based PDFs (fast, CPU)
  - Qwen2-VL OCR for image-based PDFs (GPU)
  - Saves raw text to disk so it survives crashes

Phase 2 (GPU): Load Llama ONCE, process posters in batches of BATCH_SIZE
  - Batched generation = higher GPU utilization
  - Saves after each batch (crash-safe)

Usage:
    CUDA_VISIBLE_DEVICES=0 python batch_extract_v2.py --posters ./split --output ./output
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "/home/james/poster2json")

import torch

from tqdm import tqdm

BATCH_SIZE = 1  # posters per batch — fits in 7GB free VRAM


def find_poster_files(posters_dir):
    files = []
    for ext in ("*.pdf", "*.png", "*.jpg", "*.jpeg"):
        files.extend(Path(posters_dir).rglob(ext))
    return sorted(files)


def find_completed(output_dir):
    """Find stems of successfully extracted posters."""
    completed = set()
    for f in Path(output_dir).glob("*_extracted.json"):
        try:
            data = json.loads(f.read_text())
            if "error" not in data:
                completed.add(f.stem.replace("_extracted", ""))
        except Exception:
            pass
    return completed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--posters", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_text_dir = output_dir / "_raw_text"
    raw_text_dir.mkdir(exist_ok=True)

    gpu_id = os.environ.get("CUDA_VISIBLE_DEVICES", "0")
    batch_size = args.batch_size

    # Find files and skip completed
    all_files = find_poster_files(args.posters)
    completed = find_completed(output_dir)
    pending = [f for f in all_files if f.stem not in completed]

    print(f"[GPU {gpu_id}] {len(all_files)} total, {len(completed)} done, {len(pending)} pending")
    if not pending:
        print("Nothing to do.")
        return

    from poster2json.extract import (
        get_raw_text,
        load_json_model,
        unload_vision_model,
        _robust_json_parse,
        _postprocess_json,
        _normalize_raw_text_for_model,
        _is_truncated,
        EXTRACTION_PROMPT,
        FALLBACK_PROMPT,
        MAX_JSON_TOKENS,
        MAX_RETRY_TOKENS,
        log,
    )

    # ==========================================
    # PHASE 1: Extract raw text from all posters
    # ==========================================
    print(f"\n[GPU {gpu_id}] Phase 1: Extracting raw text from {len(pending)} posters...")
    raw_texts = {}  # file -> (text, source)
    phase1_errors = 0

    for f in tqdm(pending, desc=f"GPU{gpu_id} text", unit="pdf"):
        # Check if raw text already cached from a previous run
        cache_file = raw_text_dir / f"{f.stem}.json"
        if cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text())
                if cached.get("text") and len(cached["text"]) > 50:
                    raw_texts[f] = (cached["text"], cached["source"])
                    continue
            except Exception:
                pass

        try:
            resolved = str(Path(f).resolve())
            text, source = get_raw_text(resolved)
            if text and len(text) > 50:
                raw_texts[f] = (text, source)
                # Cache to disk
                cache_file.write_text(json.dumps({
                    "text": text, "source": source,
                    "file": str(f), "chars": len(text),
                }))
            else:
                # Permanent failure - save error so we don't retry
                out = output_dir / f"{f.stem}_extracted.json"
                out.write_text(json.dumps({
                    "error": "No text extracted",
                    "source": source,
                }))
                phase1_errors += 1
        except Exception as e:
            out = output_dir / f"{f.stem}_extracted.json"
            out.write_text(json.dumps({"error": str(e)}))
            phase1_errors += 1

    # Free vision model memory before loading Llama
    unload_vision_model()

    print(f"[GPU {gpu_id}] Phase 1 done: {len(raw_texts)} with text, {phase1_errors} errors")

    # ==========================================
    # PHASE 2: Load Llama ONCE, batch process
    # ==========================================
    # Filter out any that got completed during Phase 1 by another GPU
    completed_now = find_completed(output_dir)
    phase2_items = [(f, t, s) for f, (t, s) in raw_texts.items()
                    if f.stem not in completed_now]

    if not phase2_items:
        print(f"[GPU {gpu_id}] Nothing to process in Phase 2.")
        return

    print(f"\n[GPU {gpu_id}] Phase 2: Loading Llama model...")
    model, tokenizer = load_json_model()
    print(f"[GPU {gpu_id}] Model loaded. Processing {len(phase2_items)} posters in batches of {batch_size}...")

    success = 0
    errors = 0

    from poster2json.extract import extract_json_with_retry

    pbar = tqdm(phase2_items, desc=f"GPU{gpu_id} json", unit="poster")
    for poster_file, raw_text, source in pbar:
        out_file = output_dir / f"{poster_file.stem}_extracted.json"

        # Skip if completed by another GPU since we started
        if out_file.exists():
            try:
                existing = json.loads(out_file.read_text())
                if "error" not in existing:
                    continue
            except Exception:
                pass

        try:
            t0 = time.time()
            result = extract_json_with_retry(raw_text, model, tokenizer)
            result = _postprocess_json(result, raw_text=raw_text)
            elapsed = time.time() - t0

            result["_source"] = source
            result["_extraction_time_s"] = round(elapsed, 1)

            with open(out_file, "w", encoding="utf-8") as fh:
                json.dump(result, fh, indent=2, ensure_ascii=False)

            if "error" not in result:
                success += 1
            else:
                errors += 1

            pbar.set_postfix(ok=success, err=errors, last=f"{elapsed:.0f}s")

        except KeyboardInterrupt:
            print(f"\n[GPU {gpu_id}] Interrupted. {success} done. Resume safe.")
            break
        except Exception as e:
            errors += 1
            out_file.write_text(json.dumps({"error": str(e)}))
            pbar.set_postfix(ok=success, err=errors)

    print(f"\n[GPU {gpu_id}] DONE: {success} ok, {errors} errors")
    print(f"[GPU {gpu_id}] End: {datetime.now().isoformat()}")


if __name__ == "__main__":
    main()
