#!/usr/bin/env python3
"""
Convert repository metadata + merge with extractions.

Phase 1: Convert raw Zenodo/Figshare metadata -> schema format (fixed SchemaConverter)
Phase 2: Merge extractions with converted metadata (extraction is gospel)
Phase 3: Copy extraction-only files (no metadata match)

Usage:
    python run_merge.py                  # incremental (skip already done)
    python run_merge.py --force          # reconvert + remerge everything
"""
import argparse
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

METADATA_DIR = Path("/home/james/metadata")
EXTRACTION_DIR = Path("/home/james/corpus_output/extractions")
CONVERTED_DIR = Path("/home/james/corpus_output/converted")
MERGED_DIR = Path("/home/james/corpus_output/merged")


def extract_record_id(stem: str, source: str) -> str | None:
    """Extract numeric record ID from a poster stem like 'zenodo_12345' or 'figshare_67890'."""
    prefix = f"{source}_"
    if not stem.startswith(prefix):
        return None
    remainder = stem[len(prefix):]
    rec_id = remainder.split("_")[0]
    return rec_id if rec_id else None


def main():
    parser = argparse.ArgumentParser(description="Convert metadata + merge with extractions")
    parser.add_argument("--force", action="store_true",
                        help="Force reconversion and remerge of all files")
    args = parser.parse_args()

    for d in (CONVERTED_DIR, MERGED_DIR):
        d.mkdir(parents=True, exist_ok=True)

    # ========== Phase 1: Convert metadata ==========
    logger.info("Phase 1: Converting repository metadata to schema format")
    converter = SchemaConverter()

    for source in ("zenodo", "figshare"):
        src_dir = METADATA_DIR / source
        if not src_dir.exists():
            logger.info(f"  {source}: metadata dir not found, skipping")
            continue

        out_dir = CONVERTED_DIR / source
        out_dir.mkdir(exist_ok=True)

        meta_files = list(src_dir.glob("*.json"))

        if args.force:
            to_convert = meta_files
            logger.info(f"  {source}: force-reconverting all {len(to_convert)} files")
        else:
            already = {f.stem for f in out_dir.glob("*.json")}
            to_convert = [f for f in meta_files if f.stem not in already]
            if not to_convert:
                logger.info(f"  {source}: {len(already)} already converted, nothing new")
                continue
            logger.info(f"  {source}: converting {len(to_convert)} ({len(already)} already done)")

        ok, err = 0, 0
        for f in tqdm(to_convert, desc=f"Converting {source}"):
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
                converted = converter.convert(raw, source=source)
                (out_dir / f.name).write_text(
                    json.dumps(converted, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                ok += 1
            except Exception as e:
                logger.error(f"  Error converting {f.name}: {e}")
                err += 1
        logger.info(f"  {source}: {ok} converted, {err} errors")

    # ========== Phase 2: Merge ==========
    logger.info("Phase 2: Merging extractions with metadata")
    merger = MetadataMerger()

    ext_index = {}
    skipped_errors = 0
    for f in EXTRACTION_DIR.glob("*_extracted.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if "error" in data:
                skipped_errors += 1
                continue
            stem = f.stem.replace("_extracted", "")
            ext_index[stem] = f
        except Exception:
            pass

    logger.info(f"  {len(ext_index)} successful extractions ({skipped_errors} error files skipped)")

    merged_stems = set()
    total_merged = 0

    for source in ("zenodo", "figshare"):
        meta_dir = CONVERTED_DIR / source
        if not meta_dir.exists():
            continue

        merge_out = MERGED_DIR / source
        merge_out.mkdir(exist_ok=True)

        if args.force:
            already_merged = set()
        else:
            already_merged = {f.stem.replace("_complete", "") for f in merge_out.glob("*.json")}

        meta_files = {f.stem: f for f in meta_dir.glob("*.json")}

        matches = []
        for ext_stem, ext_file in ext_index.items():
            rec_id = extract_record_id(ext_stem, source)
            if rec_id and rec_id in meta_files and rec_id not in already_merged:
                matches.append((rec_id, ext_stem, ext_file, meta_files[rec_id]))

        if not matches:
            logger.info(f"  {source}: nothing new to merge ({len(already_merged)} already done)")
            continue

        logger.info(f"  {source}: merging {len(matches)} ({len(already_merged)} already done)")
        ok, err = 0, 0
        for rec_id, ext_stem, ext_file, meta_file in tqdm(matches, desc=f"Merging {source}"):
            try:
                merger.merge_files(
                    str(ext_file), str(meta_file),
                    str(merge_out / f"{rec_id}_complete.json"),
                )
                merged_stems.add(ext_stem)
                ok += 1
            except Exception as e:
                logger.error(f"  Error merging {rec_id} ({ext_stem}): {e}")
                err += 1
        total_merged += ok
        logger.info(f"  {source}: {ok} merged, {err} errors")

    # Also track previously merged stems (for incremental runs)
    if not args.force:
        for source in ("zenodo", "figshare"):
            merge_dir = MERGED_DIR / source
            if merge_dir.exists():
                for f in merge_dir.glob("*_complete.json"):
                    rec_id = f.stem.replace("_complete", "")
                    for ext_stem in ext_index:
                        if extract_record_id(ext_stem, source) == rec_id:
                            merged_stems.add(ext_stem)

    # ========== Phase 3: Extraction-only (no metadata match) ==========
    logger.info("Phase 3: Copying extraction-only files (no metadata)")
    no_meta_dir = MERGED_DIR / "extraction_only"
    no_meta_dir.mkdir(exist_ok=True)

    if args.force:
        for f in no_meta_dir.glob("*.json"):
            f.unlink()

    unmatched = 0
    for stem, ext_file in ext_index.items():
        if stem in merged_stems:
            continue
        dst = no_meta_dir / f"{stem}.json"
        if args.force or not dst.exists():
            shutil.copy(str(ext_file), str(dst))
            unmatched += 1

    # ========== Summary ==========
    merged_total = sum(1 for _ in MERGED_DIR.rglob("*.json"))
    logger.info(f"\nDone: {merged_total} total output records")
    logger.info(f"  Merged with metadata: {total_merged}")
    logger.info(f"  Extraction-only (no metadata): {unmatched}")


if __name__ == "__main__":
    main()
