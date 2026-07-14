#!/usr/bin/env python3
"""
Restore extracted content to records that were wrongly stripped.

When the old _postprocess_json deleted a record's rightsList, the record looked
unlicensed and enforce_license_policy stripped its extracted content. The license
backfill has since recovered the real deposit license. For records that are now
ALLOWED but still carry _license_blocked (content stripped), re-attach the
content from the original extraction and clear the flag. Records whose recovered
license is blocked/unknown are left stripped (correct).

Re-attaches: content, imageCaptions, tableCaptions, researchField, and rebuilds
descriptions as deposit-Abstract + LLM-summary("Other"). All other fields
(creators with resolved ORCID/ROR, identifiers, license, etc.) are left intact.

Layout (per corpus root):
    <root>/extractions/<source>_<rec_id>...._extracted.json
    <root>/converted/{zenodo,figshare}/<rec_id>.json
    <root>/merged/{zenodo,figshare}/<rec_id>_complete.json

Usage:
    python restore_blocked_content.py --root /home/james/corpus_output --dry-run
    python restore_blocked_content.py --root /home/james/corpus_output
"""
import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

_SRC = Path(__file__).resolve().parents[2] / "src"
if _SRC.exists():
    sys.path.insert(0, str(_SRC))
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from poster_to_json.merger import MetadataMerger  # noqa: E402
from poster_to_json.field_normalize import align_schema  # noqa: E402
from license_policy import classify_license  # noqa: E402

_merger = MetadataMerger()
_CONTENT_FIELDS = ["content", "imageCaptions", "tableCaptions", "researchField"]


def build_extraction_index(ext_dir):
    """Map (source, rec_id) -> extraction file path."""
    idx = {}
    for f in ext_dir.glob("*_extracted.json"):
        stem = f.stem.replace("_extracted", "")
        parts = stem.split("_")
        if len(parts) >= 2 and parts[0] in ("zenodo", "figshare"):
            idx[(parts[0], parts[1])] = f
    return idx


def _has_content(d):
    c = d.get("content")
    return isinstance(c, dict) and bool(c.get("sections"))


def restore_record(merged, extraction, converted):
    changed = False
    for fld in _CONTENT_FIELDS:
        val = extraction.get(fld)
        if val and not merged.get(fld):
            merged[fld] = val
            changed = True

    if not merged.get("descriptions"):
        dep_desc = (converted or {}).get("descriptions")
        ext_desc = extraction.get("descriptions")
        if dep_desc:
            new_desc = _merger._merge_descriptions(ext_desc, dep_desc)
        else:
            new_desc = ext_desc or []
        if new_desc:
            merged["descriptions"] = new_desc
            changed = True

    if merged.get("_license_blocked"):
        del merged["_license_blocked"]
        changed = True

    if changed:
        # re-conform: the re-attached researchField is raw extraction, so lift it to an
        # OpenAlex domain and re-mirror `domain` (align_schema is idempotent).
        align_schema(merged)

    return changed


def run(root, dry_run, limit):
    merged_dir = root / "merged"
    conv_dir = root / "converted"
    ext_dir = root / "extractions"
    if not ext_dir.exists():
        logger.error(f"no extractions dir at {ext_dir}")
        return {}

    logger.info("indexing extractions...")
    ext_index = build_extraction_index(ext_dir)
    logger.info(f"  {len(ext_index)} extractions indexed")

    stats = {"scanned": 0, "candidates": 0, "restored": 0, "no_extraction": 0,
             "still_empty": 0, "errors": 0}

    for src in ("zenodo", "figshare"):
        md = merged_dir / src
        if not md.exists():
            continue
        for f in sorted(md.glob("*_complete.json")):
            if limit and stats["scanned"] >= limit:
                break
            stats["scanned"] += 1
            rid = f.stem.replace("_complete", "")
            try:
                merged = json.loads(f.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error(f"  read error {rid}: {e}")
                stats["errors"] += 1
                continue
            if not isinstance(merged, dict):
                continue

            # only records that are now allowed but stripped
            if not merged.get("_license_blocked"):
                continue
            if classify_license(merged.get("rightsList")) != "allowed":
                continue
            stats["candidates"] += 1

            ext_file = ext_index.get((src, rid))
            if not ext_file:
                stats["no_extraction"] += 1
                continue
            try:
                extraction = json.loads(ext_file.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error(f"  extraction read error {rid}: {e}")
                stats["errors"] += 1
                continue

            converted = None
            cf = conv_dir / src / f"{rid}.json"
            if cf.exists():
                try:
                    converted = json.loads(cf.read_text(encoding="utf-8"))
                except Exception:
                    pass

            if restore_record(merged, extraction, converted):
                if not _has_content(merged):
                    stats["still_empty"] += 1
                stats["restored"] += 1
                if not dry_run:
                    try:
                        f.write_text(json.dumps(merged, indent=2, ensure_ascii=False),
                                     encoding="utf-8")
                    except Exception as e:
                        logger.error(f"  write error {rid}: {e}")
                        stats["errors"] += 1

    return stats


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    logger.info(f"[{mode}] restore blocked content  root={args.root}")
    stats = run(args.root, args.dry_run, args.limit)
    logger.info("Done:")
    for k in ("scanned", "candidates", "restored", "no_extraction",
              "still_empty", "errors"):
        logger.info(f"  {k:14s} {stats.get(k, 0)}")


if __name__ == "__main__":
    main()
