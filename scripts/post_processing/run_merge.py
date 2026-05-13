#!/usr/bin/env python3
"""
Convert repository metadata + merge with extractions.

Phase 1: Convert raw Zenodo/Figshare metadata -> schema format
Phase 2: Merge extractions with converted metadata (extraction is gospel)
Phase 3: Copy extraction-only files (no metadata match)
Phase 4a: Pre-warm API caches (batch ROR/ORCID/funder lookups)
Phase 4b: Parallel _postprocess_json cleanup

Usage:
    python run_merge.py                  # incremental (skip already done)
    python run_merge.py --force          # reconvert + remerge everything
    python run_merge.py --postprocess-only  # skip phases 1-3, just run phase 4
"""
import argparse
import json
import logging
import os
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

sys.path.insert(0, "/home/james/poster-repo-to-json/src")
from poster_to_json.schema_converter import SchemaConverter
from poster_to_json.merger import MetadataMerger

sys.path.insert(0, "/home/james/poster2json")
from poster2json.extract import _postprocess_json

METADATA_DIR = Path("/home/james/metadata")
EXTRACTION_DIR = Path("/home/james/corpus_output/extractions")
RAW_TEXT_DIR = Path("/home/james/corpus_output/extractions/_raw_text")
CONVERTED_DIR = Path("/home/james/corpus_output/converted")
MERGED_DIR = Path("/home/james/corpus_output/merged")

NUM_WORKERS = min(os.cpu_count() or 4, 12)


def extract_record_id(stem: str, source: str) -> str | None:
    prefix = f"{source}_"
    if not stem.startswith(prefix):
        return None
    remainder = stem[len(prefix):]
    rec_id = remainder.split("_")[0]
    return rec_id if rec_id else None


def _find_raw_text_path(merged_path: Path, ext_index: dict) -> str:
    """Find the raw text cache file for a merged output file."""
    stem = merged_path.stem.replace("_complete", "").replace("_extracted", "")
    raw_cache = RAW_TEXT_DIR / f"{stem}.json"
    if raw_cache.exists():
        return str(raw_cache)
    for source in ("zenodo", "figshare"):
        for ext_stem in ext_index:
            if extract_record_id(ext_stem, source) == stem:
                candidate = RAW_TEXT_DIR / f"{ext_stem}.json"
                if candidate.exists():
                    return str(candidate)
    return ""


# ---- Pre-warm: batch-populate API caches before parallel processing ----

def _collect_uncached_keys(all_merged: list):
    """Scan merged files and collect unique API lookup keys not yet cached."""
    from poster2json.ror import get_default_client as get_ror, _normalize_query as ror_norm
    from poster2json.orcid import get_default_client as get_orcid, _normalize
    from poster2json.funders import get_default_client as get_funder, _normalize_query as funder_norm

    ror = get_ror()
    orcid = get_orcid()
    funder = get_funder()

    ror_keys = set()
    orcid_keys = []
    orcid_seen = set()
    funder_keys = set()

    for f in tqdm(all_merged, desc="Scanning for uncached keys"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if "error" in data:
            continue

        for person_field in ("creators", "contributors"):
            for p in data.get(person_field, []):
                if not isinstance(p, dict):
                    continue
                for aff in p.get("affiliation", []):
                    name = None
                    if isinstance(aff, str) and aff.strip():
                        name = aff.strip()
                    elif isinstance(aff, dict) and aff.get("name") and not aff.get("affiliationIdentifier"):
                        name = aff["name"]
                    if name:
                        key = ror_norm(name)
                        if key and key not in ror._cache:
                            ror_keys.add(name)

                given = p.get("givenName", "")
                family = p.get("familyName", "")
                if given and family:
                    has_orcid = any(
                        isinstance(ni, dict) and ni.get("nameIdentifierScheme") == "ORCID"
                        for ni in p.get("nameIdentifiers", [])
                    )
                    if not has_orcid:
                        aff_name = None
                        for aff in p.get("affiliation", []):
                            if isinstance(aff, str) and aff.strip():
                                aff_name = aff.strip()
                                break
                            if isinstance(aff, dict) and aff.get("name"):
                                aff_name = aff["name"]
                                break
                        if aff_name:
                            cache_key = f"{_normalize(given).lower()}|{_normalize(family).lower()}|{_normalize(aff_name).lower()}"
                            if cache_key not in orcid._cache and cache_key not in orcid_seen:
                                orcid_seen.add(cache_key)
                                orcid_keys.append((given, family, aff_name))

        pub = data.get("publisher")
        if isinstance(pub, dict) and pub.get("name") and not pub.get("publisherIdentifier"):
            key = ror_norm(pub["name"])
            if key and key not in ror._cache:
                ror_keys.add(pub["name"])

        for fr in data.get("fundingReferences", []):
            if isinstance(fr, dict) and fr.get("funderName") and not fr.get("funderIdentifier"):
                key = funder_norm(fr["funderName"])
                if key and key not in funder._cache:
                    funder_keys.add(fr["funderName"])

    return ror_keys, orcid_keys, funder_keys


def prewarm_caches(all_merged: list):
    """Batch-query ROR/ORCID/funder APIs for all uncached keys."""
    ror_keys, orcid_keys, funder_keys = _collect_uncached_keys(all_merged)

    logger.info(f"  Uncached: {len(ror_keys)} ROR, {len(orcid_keys)} ORCID, {len(funder_keys)} funder")

    if ror_keys:
        from poster2json.ror import get_default_client as get_ror
        ror = get_ror()
        ok = 0
        for name in tqdm(sorted(ror_keys), desc="Pre-warm ROR"):
            result = ror.lookup(name)
            if result:
                ok += 1
            if not ror.enabled:
                logger.warning("  ROR disabled mid-prewarm, stopping")
                break
        logger.info(f"  ROR: {ok} resolved out of {len(ror_keys)} queried")

    if orcid_keys:
        from poster2json.orcid import get_default_client as get_orcid
        orcid = get_orcid()
        ok = 0
        for given, family, aff in tqdm(orcid_keys, desc="Pre-warm ORCID"):
            result = orcid.lookup(given, family, aff)
            if result:
                ok += 1
            if not orcid.enabled:
                logger.warning("  ORCID disabled mid-prewarm, stopping")
                break
        logger.info(f"  ORCID: {ok} resolved out of {len(orcid_keys)} queried")

    if funder_keys:
        from poster2json.funders import get_default_client as get_funder
        funder = get_funder()
        ok = 0
        for name in tqdm(sorted(funder_keys), desc="Pre-warm funders"):
            result = funder.lookup(name)
            if result:
                ok += 1
            if not funder.enabled:
                logger.warning("  Funder disabled mid-prewarm, stopping")
                break
        logger.info(f"  Funders: {ok} resolved out of {len(funder_keys)} queried")


def _init_singletons():
    """Initialize all singletons so forked children inherit them."""
    from poster2json.ror import get_default_client as get_ror
    from poster2json.orcid import get_default_client as get_orcid
    from poster2json.funders import get_default_client as get_funder
    from poster2json.language import _build_detector, _get_lang_cache
    from poster2json.normalize import normalize_rights_list, normalize_subjects  # noqa: F401
    from poster2json.normalize import normalize_funding_references  # noqa: F401

    get_ror()
    get_orcid()
    get_funder()
    _build_detector()
    _get_lang_cache()
    logger.info("  All singletons initialized (ROR, ORCID, funder, lingua, lang cache)")


# ---- Parallel worker ----

def _postprocess_worker(args):
    """Worker for parallel Phase 4. Runs in forked child process."""
    file_path, raw_text_path = args
    f = Path(file_path)
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        if "error" in data:
            return "skip"

        raw_text = ""
        if raw_text_path:
            try:
                cached = json.loads(Path(raw_text_path).read_text())
                raw_text = cached.get("text", "")
            except Exception:
                pass

        data = _postprocess_json(data, raw_text=raw_text)
        f.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return "ok"
    except Exception as e:
        return f"err:{f.name}:{e}"


def main():
    parser = argparse.ArgumentParser(description="Convert metadata + merge with extractions")
    parser.add_argument("--force", action="store_true",
                        help="Force reconversion and remerge of all files")
    parser.add_argument("--postprocess-only", action="store_true",
                        help="Skip phases 1-3, only run post-processing (phase 4)")
    parser.add_argument("--workers", type=int, default=NUM_WORKERS,
                        help=f"Number of parallel workers for phase 4 (default: {NUM_WORKERS})")
    args = parser.parse_args()

    for d in (CONVERTED_DIR, MERGED_DIR):
        d.mkdir(parents=True, exist_ok=True)

    ext_index = {}
    total_merged = 0
    unmatched = 0

    if not args.postprocess_only:
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

        for stem, ext_file in ext_index.items():
            if stem in merged_stems:
                continue
            dst = no_meta_dir / f"{stem}.json"
            if args.force or not dst.exists():
                shutil.copy(str(ext_file), str(dst))
                unmatched += 1

    # ========== Phase 4: Post-merge cleanup (optimized) ==========
    # Build ext_index if we skipped phases 1-3
    if not ext_index:
        for f in EXTRACTION_DIR.glob("*_extracted.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if "error" not in data:
                    ext_index[f.stem.replace("_extracted", "")] = f
            except Exception:
                pass

    all_merged = list(MERGED_DIR.rglob("*.json"))
    logger.info(f"Phase 4: Post-processing {len(all_merged)} files with {args.workers} workers")

    # Phase 4a: Pre-warm API caches
    logger.info("Phase 4a: Pre-warming API caches")
    prewarm_caches(all_merged)

    # Phase 4b: Initialize singletons for fork inheritance
    logger.info("Phase 4b: Initializing singletons for parallel workers")
    _init_singletons()

    # Phase 4c: Build work list with pre-resolved raw text paths
    logger.info("Phase 4c: Building work list")
    work_items = []
    for f in all_merged:
        raw_path = _find_raw_text_path(f, ext_index)
        work_items.append((str(f), raw_path))

    # Phase 4d: Parallel post-processing
    logger.info(f"Phase 4d: Parallel cleanup ({args.workers} workers, {len(work_items)} files)")
    cleaned, clean_err, skipped = 0, 0, 0

    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_postprocess_worker, item): item for item in work_items}
        for future in tqdm(as_completed(futures), total=len(futures), desc="Post-merge cleanup"):
            result = future.result()
            if result == "ok":
                cleaned += 1
            elif result == "skip":
                skipped += 1
            elif result.startswith("err:"):
                clean_err += 1
                logger.error(f"  {result}")

    # Save language cache from parent process
    from poster2json.language import save_lang_cache
    save_lang_cache()

    logger.info(f"  {cleaned} cleaned, {skipped} skipped, {clean_err} errors")

    # ========== Summary ==========
    merged_total = sum(1 for _ in MERGED_DIR.rglob("*.json"))
    logger.info(f"\nDone: {merged_total} total output records")
    if not args.postprocess_only:
        logger.info(f"  Merged with metadata: {total_merged}")
        logger.info(f"  Extraction-only (no metadata): {unmatched}")
    logger.info(f"  Post-merge cleanup: {cleaned} cleaned, {clean_err} errors")


if __name__ == "__main__":
    main()
