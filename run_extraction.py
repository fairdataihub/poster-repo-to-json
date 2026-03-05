#!/usr/bin/env python3
"""
Batch extraction script for the poster-to-json pipeline.

Runs poster2json on all classified poster PDFs, then enriches each
extraction with Zenodo/Figshare repository metadata. Supports resume
after crash — already-extracted files are skipped automatically.

Default paths assume the corpus data lives in the repo working directory:
    poster-repo-to-json/
    ├── posters/              # Classified poster PDFs (from poster-qc)
    │   ├── zenodo/
    │   └── figshare/
    ├── metadata/             # Per-record JSON metadata (from poster-scraper)
    │   ├── zenodo/
    │   └── figshare/
    └── output/               # Created by this script
        ├── extractions/      # poster2json raw output
        ├── converted/        # Metadata converted to schema
        └── merged/           # Final merged records

Usage:
    python run_extraction.py
    python run_extraction.py --posters /path/to/pdfs --metadata /path/to/meta
    python run_extraction.py --max 100         # Process first 100 only
    python run_extraction.py --dry-run         # Show what would be processed
"""

import argparse
import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

REPO_DIR = Path(__file__).parent

# Default paths (relative to repo root)
DEFAULT_POSTERS = REPO_DIR / "posters"
DEFAULT_METADATA = REPO_DIR / "metadata"
DEFAULT_OUTPUT = REPO_DIR / "output"


def find_poster_files(posters_dir: Path) -> list[Path]:
    """Find all poster PDF/image files across zenodo/ and figshare/ subdirs."""
    files = []
    for ext in ("*.pdf", "*.png", "*.jpg", "*.jpeg"):
        files.extend(posters_dir.rglob(ext))
    return sorted(files)


def find_completed(extraction_dir: Path) -> set[str]:
    """Find stems of already-extracted files (for resume)."""
    completed = set()
    for f in extraction_dir.glob("*_extracted.json"):
        try:
            with open(f) as fh:
                data = json.load(fh)
            # Only count as complete if it has actual content (not just an error)
            if "error" not in data:
                completed.add(f.stem.replace("_extracted", ""))
        except (json.JSONDecodeError, OSError):
            pass  # Corrupt file — will re-extract
    return completed


def run_extraction(args):
    posters_dir = Path(args.posters)
    metadata_dir = Path(args.metadata)
    output_dir = Path(args.output)

    extraction_dir = output_dir / "extractions"
    converted_dir = output_dir / "converted"
    merged_dir = output_dir / "merged"

    for d in (extraction_dir, converted_dir, merged_dir):
        d.mkdir(parents=True, exist_ok=True)

    # Find all poster files
    all_files = find_poster_files(posters_dir)
    if not all_files:
        logger.error(f"No poster files found in {posters_dir}")
        sys.exit(1)

    # Resume: skip already-completed extractions
    completed = find_completed(extraction_dir)
    pending = [f for f in all_files if f.stem not in completed]

    if args.max:
        pending = pending[:args.max]

    logger.info(f"Found {len(all_files)} total posters, {len(completed)} already done, {len(pending)} to process")

    if args.dry_run:
        for f in pending[:20]:
            print(f"  would extract: {f.name}")
        if len(pending) > 20:
            print(f"  ... and {len(pending) - 20} more")
        return

    if not pending:
        logger.info("Nothing to extract — all files already processed")
    else:
        # ---- Phase 1: Extract with poster2json ----
        logger.info("=" * 60)
        logger.info("PHASE 1: Extracting poster content via poster2json")
        logger.info("=" * 60)

        from poster_to_json.extractor import PosterExtractor
        extractor = PosterExtractor()

        success = 0
        errors = 0
        error_log = []

        pbar = tqdm(pending, desc="Extracting", unit="poster",
                    dynamic_ncols=True, smoothing=0.1)
        for poster_file in pbar:
            stem = poster_file.stem
            out_file = extraction_dir / f"{stem}_extracted.json"

            try:
                t0 = time.time()
                result = extractor.extract(str(poster_file))
                elapsed = time.time() - t0

                with open(out_file, "w", encoding="utf-8") as f:
                    json.dump(result, f, indent=2, ensure_ascii=False)

                if "error" in result:
                    errors += 1
                    error_log.append({"file": str(poster_file), "error": result["error"]})
                    pbar.set_postfix(ok=success, err=errors)
                else:
                    success += 1
                    pbar.set_postfix(ok=success, err=errors,
                                     last=f"{elapsed:.0f}s")

            except KeyboardInterrupt:
                logger.info(f"\nInterrupted. {success} extracted, {errors} errors. Resume with same command.")
                sys.exit(130)
            except Exception as e:
                errors += 1
                error_log.append({"file": str(poster_file), "error": str(e),
                                  "traceback": traceback.format_exc()})
                logger.warning(f"Error on {poster_file.name}: {e}")
                pbar.set_postfix(ok=success, err=errors)

        logger.info(f"Extraction done: {success} ok, {errors} errors (of {len(pending)} attempted)")

        # Save error log
        if error_log:
            err_file = output_dir / "extraction_errors.json"
            with open(err_file, "w") as f:
                json.dump(error_log, f, indent=2)
            logger.info(f"Error log saved to {err_file}")

    # ---- Phase 2: Convert repository metadata ----
    logger.info("")
    logger.info("=" * 60)
    logger.info("PHASE 2: Converting repository metadata to schema")
    logger.info("=" * 60)

    from poster_to_json.schema_converter import SchemaConverter
    converter = SchemaConverter()

    for subdir in ("zenodo", "figshare"):
        source_dir = metadata_dir / subdir
        if not source_dir.exists():
            logger.info(f"  {subdir}: no metadata directory found, skipping")
            continue

        out_dir = converted_dir / subdir
        out_dir.mkdir(exist_ok=True)

        meta_files = list(source_dir.glob("*.json"))
        already_converted = {f.stem.replace("_converted", "") for f in out_dir.glob("*.json")}
        to_convert = [f for f in meta_files if f.stem not in already_converted]

        if not to_convert:
            logger.info(f"  {subdir}: {len(already_converted)} already converted, nothing to do")
            continue

        logger.info(f"  {subdir}: converting {len(to_convert)} records ({len(already_converted)} already done)")

        conv_ok = 0
        conv_err = 0
        for meta_file in tqdm(to_convert, desc=f"Converting {subdir}", unit="rec"):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                converted = converter.convert(raw, source=subdir)
                with open(out_dir / f"{meta_file.stem}.json", "w", encoding="utf-8") as f:
                    json.dump(converted, f, indent=2, ensure_ascii=False)
                conv_ok += 1
            except Exception as e:
                conv_err += 1
                logger.debug(f"Error converting {meta_file.name}: {e}")

        logger.info(f"  {subdir}: {conv_ok} converted, {conv_err} errors")

    # ---- Phase 3: Merge (extraction base + metadata backfill) ----
    logger.info("")
    logger.info("=" * 60)
    logger.info("PHASE 3: Merging (poster2json base + metadata backfill)")
    logger.info("=" * 60)

    from poster_to_json.merger import MetadataMerger
    merger = MetadataMerger()

    # Build extraction index: stem -> file
    ext_index = {}
    for f in extraction_dir.glob("*_extracted.json"):
        stem = f.stem.replace("_extracted", "")
        ext_index[stem] = f

    total_merged = 0
    for subdir in ("zenodo", "figshare"):
        meta_dir = converted_dir / subdir
        if not meta_dir.exists():
            continue

        merge_out = merged_dir / subdir
        merge_out.mkdir(exist_ok=True)

        already_merged = {f.stem.replace("_complete", "") for f in merge_out.glob("*.json")}

        # Match: extraction file stem often contains the record ID
        # e.g. "zenodo_10000504_Title_2023" matches metadata "10000504.json"
        meta_files = {f.stem: f for f in meta_dir.glob("*.json")}
        matches = []
        for ext_stem, ext_file in ext_index.items():
            # Try exact match
            if ext_stem in meta_files and ext_stem not in already_merged:
                matches.append((ext_stem, ext_file, meta_files[ext_stem]))
                continue
            # Try extracting record ID from filename like "zenodo_10000504_..."
            for prefix in (f"{subdir}_",):
                if ext_stem.startswith(prefix):
                    rec_id = ext_stem[len(prefix):].split("_")[0]
                    if rec_id in meta_files and rec_id not in already_merged:
                        matches.append((rec_id, ext_file, meta_files[rec_id]))
                        break

        if not matches:
            logger.info(f"  {subdir}: no new matches to merge ({len(already_merged)} already done)")
            continue

        logger.info(f"  {subdir}: merging {len(matches)} records ({len(already_merged)} already done)")

        merge_ok = 0
        for rec_id, ext_file, meta_file in tqdm(matches, desc=f"Merging {subdir}", unit="rec"):
            try:
                merger.merge_files(str(ext_file), str(meta_file),
                                   str(merge_out / f"{rec_id}_complete.json"))
                merge_ok += 1
            except Exception as e:
                logger.debug(f"Error merging {rec_id}: {e}")

        total_merged += merge_ok
        logger.info(f"  {subdir}: {merge_ok} merged")

    # Copy extraction-only files (no metadata match)
    all_merged_ids = set()
    for subdir in merged_dir.iterdir():
        if subdir.is_dir():
            all_merged_ids.update(f.stem.replace("_complete", "") for f in subdir.glob("*.json"))

    unmatched = set(ext_index.keys()) - all_merged_ids
    if unmatched:
        no_meta_dir = merged_dir / "extraction_only"
        no_meta_dir.mkdir(exist_ok=True)
        import shutil
        for stem in unmatched:
            src = ext_index[stem]
            dst = no_meta_dir / f"{stem}.json"
            if not dst.exists():
                shutil.copy(src, dst)
        logger.info(f"  extraction-only (no metadata): {len(unmatched)}")

    # Summary
    total_extractions = len(list(extraction_dir.glob("*_extracted.json")))
    total_final = sum(1 for _ in merged_dir.rglob("*.json"))

    logger.info("")
    logger.info("=" * 60)
    logger.info("COMPLETE")
    logger.info("=" * 60)
    logger.info(f"  Extractions:    {total_extractions}")
    logger.info(f"  Merged records: {total_final}")
    logger.info(f"  Output:         {output_dir}")
    logger.info("")
    logger.info("To resume after a crash, just re-run the same command.")
    logger.info("Already-completed files are skipped automatically.")


def main():
    parser = argparse.ArgumentParser(
        description="Batch poster extraction with resume support",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run with defaults (posters/ and metadata/ in repo dir)
  python run_extraction.py

  # Custom paths
  python run_extraction.py \\
      --posters /storage/poster-pdf-meta_downloads \\
      --metadata /storage/poster-pdf-meta_metadata \\
      --output ./output

  # Process only first 50 posters
  python run_extraction.py --max 50

  # See what would be processed without running
  python run_extraction.py --dry-run
""",
    )
    parser.add_argument(
        "--posters", default=str(DEFAULT_POSTERS),
        help=f"Directory with poster PDFs (default: {DEFAULT_POSTERS})",
    )
    parser.add_argument(
        "--metadata", default=str(DEFAULT_METADATA),
        help=f"Directory with per-record metadata JSONs (default: {DEFAULT_METADATA})",
    )
    parser.add_argument(
        "--output", default=str(DEFAULT_OUTPUT),
        help=f"Output directory (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--max", type=int, default=None,
        help="Maximum number of posters to process",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be processed without running extraction",
    )
    args = parser.parse_args()
    run_extraction(args)


if __name__ == "__main__":
    main()
