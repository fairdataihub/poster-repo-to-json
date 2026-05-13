#!/usr/bin/env python3
"""
Re-merge the ~740 Zenodo records that failed due to string conference values.
Then run _postprocess_json cleanup on just those files.
"""
import json
import logging
import shutil
import sys
from pathlib import Path

from tqdm import tqdm

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

sys.path.insert(0, "/home/james/poster-repo-to-json/src")
from poster_to_json.schema_converter import SchemaConverter
from poster_to_json.merger import MetadataMerger

sys.path.insert(0, "/home/james/poster2json")
from poster2json.extract import _postprocess_json

EXTRACTION_DIR = Path("/home/james/corpus_output/extractions")
RAW_TEXT_DIR = Path("/home/james/corpus_output/extractions/_raw_text")
CONVERTED_DIR = Path("/home/james/corpus_output/converted")
MERGED_DIR = Path("/home/james/corpus_output/merged")


def extract_record_id(stem: str, source: str):
    prefix = f"{source}_"
    if not stem.startswith(prefix):
        return None
    remainder = stem[len(prefix):]
    rec_id = remainder.split("_")[0]
    return rec_id if rec_id else None


def main():
    merger = MetadataMerger()

    ext_index = {}
    for f in EXTRACTION_DIR.glob("*_extracted.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if "error" in data:
                continue
            stem = f.stem.replace("_extracted", "")
            ext_index[stem] = f
        except Exception:
            pass

    logger.info(f"Loaded {len(ext_index)} extraction index entries")

    source = "zenodo"
    meta_dir = CONVERTED_DIR / source
    merge_out = MERGED_DIR / source
    merge_out.mkdir(exist_ok=True)

    already_merged = {f.stem.replace("_complete", "") for f in merge_out.glob("*.json")}
    meta_files = {f.stem: f for f in meta_dir.glob("*.json")}

    matches = []
    for ext_stem, ext_file in ext_index.items():
        rec_id = extract_record_id(ext_stem, source)
        if rec_id and rec_id in meta_files and rec_id not in already_merged:
            matches.append((rec_id, ext_stem, ext_file, meta_files[rec_id]))

    logger.info(f"Found {len(matches)} unmerged Zenodo records to retry")

    if not matches:
        logger.info("Nothing to do!")
        return

    ok, err = 0, 0
    newly_merged = []
    for rec_id, ext_stem, ext_file, meta_file in tqdm(matches, desc="Re-merging"):
        try:
            out_path = merge_out / f"{rec_id}_complete.json"
            merger.merge_files(str(ext_file), str(meta_file), str(out_path))
            newly_merged.append((rec_id, ext_stem, out_path))
            ok += 1
        except Exception as e:
            logger.error(f"Still failing {rec_id} ({ext_stem}): {e}")
            err += 1
    logger.info(f"Re-merge: {ok} succeeded, {err} still failing")

    # Remove successfully merged files from extraction_only
    no_meta_dir = MERGED_DIR / "extraction_only"
    removed = 0
    for rec_id, ext_stem, _ in newly_merged:
        eo_file = no_meta_dir / f"{ext_stem}.json"
        if eo_file.exists():
            eo_file.unlink()
            removed += 1
    logger.info(f"Removed {removed} files from extraction_only")

    # Run _postprocess_json on newly merged files
    logger.info(f"Running _postprocess_json on {len(newly_merged)} newly merged files")
    cleaned, clean_err = 0, 0
    for rec_id, ext_stem, out_path in tqdm(newly_merged, desc="Post-processing"):
        try:
            data = json.loads(out_path.read_text(encoding="utf-8"))
            raw_text = ""
            raw_cache = RAW_TEXT_DIR / f"{ext_stem}.json"
            if raw_cache.exists():
                try:
                    cached = json.loads(raw_cache.read_text())
                    raw_text = cached.get("text", "")
                except Exception:
                    pass
            data = _postprocess_json(data, raw_text=raw_text)
            out_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            cleaned += 1
        except Exception as e:
            clean_err += 1
            logger.error(f"Error cleaning {out_path.name}: {e}")
    logger.info(f"Post-processing: {cleaned} cleaned, {clean_err} errors")

    # Final counts
    zen_count = sum(1 for _ in (MERGED_DIR / "zenodo").glob("*.json"))
    fig_count = sum(1 for _ in (MERGED_DIR / "figshare").glob("*.json"))
    eo_count = sum(1 for _ in no_meta_dir.glob("*.json")) if no_meta_dir.exists() else 0
    logger.info(f"\nFinal counts: Zenodo={zen_count}, Figshare={fig_count}, Extraction-only={eo_count}, Total={zen_count+fig_count+eo_count}")


if __name__ == "__main__":
    main()
