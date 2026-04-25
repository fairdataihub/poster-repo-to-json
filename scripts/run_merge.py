#!/usr/bin/env python3
"""
Convert repository metadata + merge with extractions.
Can be run repeatedly — only processes new/unmerged files.

Phase 1: Convert raw Zenodo/Figshare metadata -> schema format
Phase 2: Merge extractions with converted metadata (extraction is gospel)
"""
import json, sys, logging
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

for d in (CONVERTED_DIR, MERGED_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ========== Phase 1: Convert metadata ==========
logger.info("Phase 1: Converting repository metadata to schema format")
converter = SchemaConverter()

for source in ("zenodo", "figshare"):
    src_dir = METADATA_DIR / source
    out_dir = CONVERTED_DIR / source
    out_dir.mkdir(exist_ok=True)

    meta_files = list(src_dir.glob("*.json"))
    already = {f.stem for f in out_dir.glob("*.json")}
    to_convert = [f for f in meta_files if f.stem not in already]

    if not to_convert:
        logger.info(f"  {source}: {len(already)} already converted, nothing new")
        continue

    logger.info(f"  {source}: converting {len(to_convert)} ({len(already)} already done)")
    ok, err = 0, 0
    for f in tqdm(to_convert, desc=f"Converting {source}"):
        try:
            raw = json.loads(f.read_text())
            converted = converter.convert(raw, source=source)
            (out_dir / f.name).write_text(json.dumps(converted, indent=2, ensure_ascii=False))
            ok += 1
        except Exception as e:
            err += 1
    logger.info(f"  {source}: {ok} converted, {err} errors")

# ========== Phase 2: Merge ==========
logger.info("\nPhase 2: Merging extractions with metadata")
merger = MetadataMerger()

# Index extractions (only successful ones)
ext_index = {}
for f in EXTRACTION_DIR.glob("*_extracted.json"):
    try:
        data = json.loads(f.read_text())
        if "error" not in data:
            stem = f.stem.replace("_extracted", "")
            ext_index[stem] = f
    except:
        pass

logger.info(f"  {len(ext_index)} successful extractions to merge")

total_merged = 0
for source in ("zenodo", "figshare"):
    meta_dir = CONVERTED_DIR / source
    if not meta_dir.exists():
        continue

    merge_out = MERGED_DIR / source
    merge_out.mkdir(exist_ok=True)
    already_merged = {f.stem.replace("_complete", "") for f in merge_out.glob("*.json")}

    # Match extraction stems to metadata record IDs
    meta_files = {f.stem: f for f in meta_dir.glob("*.json")}
    matches = []
    for ext_stem, ext_file in ext_index.items():
        if ext_stem.startswith(f"{source}_"):
            rec_id = ext_stem[len(f"{source}_"):].split("_")[0]
            if rec_id in meta_files and rec_id not in already_merged:
                matches.append((rec_id, ext_file, meta_files[rec_id]))

    if not matches:
        logger.info(f"  {source}: nothing new to merge ({len(already_merged)} already done)")
        continue

    logger.info(f"  {source}: merging {len(matches)} ({len(already_merged)} already done)")
    ok = 0
    for rec_id, ext_file, meta_file in tqdm(matches, desc=f"Merging {source}"):
        try:
            merger.merge_files(str(ext_file), str(meta_file),
                             str(merge_out / f"{rec_id}_complete.json"))
            ok += 1
        except Exception as e:
            logger.debug(f"Error merging {rec_id}: {e}")
    total_merged += ok
    logger.info(f"  {source}: {ok} merged")

# Copy extraction-only files (no metadata match)
no_meta_dir = MERGED_DIR / "extraction_only"
no_meta_dir.mkdir(exist_ok=True)
all_merged_ids = set()
for subdir in MERGED_DIR.iterdir():
    if subdir.is_dir() and subdir.name != "extraction_only":
        all_merged_ids.update(f.stem.replace("_complete", "") for f in subdir.glob("*.json"))

import shutil
unmatched = 0
for stem, ext_file in ext_index.items():
    parts = stem.split("_")
    if len(parts) >= 2:
        rec_id = parts[1]
        if rec_id not in all_merged_ids:
            dst = no_meta_dir / f"{stem}.json"
            if not dst.exists():
                shutil.copy(str(ext_file), str(dst))
                unmatched += 1

# Summary
merged_total = sum(1 for _ in MERGED_DIR.rglob("*.json"))
logger.info(f"\nDone: {merged_total} total merged records")
logger.info(f"  Merged with metadata: {total_merged}")
logger.info(f"  Extraction-only (no metadata): {unmatched}")
