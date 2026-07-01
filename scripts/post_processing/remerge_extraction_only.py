#!/usr/bin/env python3
"""
Re-merge extraction_only records that actually have deposit metadata.

Some posters were extracted but never paired with their Zenodo/Figshare
metadata, so they landed in merged/extraction_only/ with no DOI and no deposit
fields — even though the metadata (with a DOI) is on disk. For each such record
whose metadata exists, convert the raw metadata and merge it with the
extraction (which applies the full current pipeline: dates, conference,
publisher, license, subjects split, creator union, etc.), write the result to
merged/<source>/<id>_complete.json, and remove the extraction_only copy.

Usage:
    python remerge_extraction_only.py --root <corpus> --metadata-dir <meta> --dry-run
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

from poster_to_json.schema_converter import SchemaConverter  # noqa: E402
from poster_to_json.merger import MetadataMerger  # noqa: E402

_conv = SchemaConverter()
_merger = MetadataMerger()


def _has_doi(record):
    return any(isinstance(i, dict) and i.get("identifierType") == "DOI"
               and i.get("identifier")
               for i in (record.get("identifiers") or []))


def run(root, metadata_dir, dry_run, limit):
    eo_dir = Path(root) / "merged" / "extraction_only"
    merged_dir = Path(root) / "merged"
    meta = Path(metadata_dir)
    stats = {"scanned": 0, "remerged": 0, "recovered_doi": 0, "no_metadata": 0,
             "still_no_doi": 0, "errors": 0}

    for f in sorted(eo_dir.glob("*.json")):
        if limit and stats["scanned"] >= limit:
            break
        stats["scanned"] += 1
        parts = f.stem.split("_")
        if len(parts) < 2 or parts[0] not in ("zenodo", "figshare"):
            continue
        src, rid = parts[0], parts[1]
        mp = meta / src / f"{rid}.json"
        if not mp.exists():
            stats["no_metadata"] += 1
            continue
        try:
            extraction = json.loads(f.read_text(encoding="utf-8"))
            raw = json.loads(mp.read_text(encoding="utf-8"))
            converted = _conv.convert(raw, source=src)
            merged = _merger.merge(extraction, converted)
        except Exception as e:
            logger.error(f"  {rid}: {e}")
            stats["errors"] += 1
            continue

        if _has_doi(merged):
            stats["recovered_doi"] += 1
        else:
            stats["still_no_doi"] += 1
        stats["remerged"] += 1

        if not dry_run:
            out = merged_dir / src / f"{rid}_complete.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            try:
                out.write_text(json.dumps(merged, indent=2, ensure_ascii=False),
                               encoding="utf-8")
                f.unlink()  # remove from extraction_only now that it's merged
            except Exception as e:
                logger.error(f"  write {rid}: {e}")
                stats["errors"] += 1
    return stats


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True)
    ap.add_argument("--metadata-dir", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    logger.info(f"[{mode}] remerge extraction_only  root={args.root}")
    stats = run(args.root, args.metadata_dir, args.dry_run, args.limit)
    logger.info("Done:")
    for k in ("scanned", "remerged", "recovered_doi", "still_no_doi",
              "no_metadata", "errors"):
        logger.info(f"  {k:14s} {stats[k]}")


if __name__ == "__main__":
    main()
