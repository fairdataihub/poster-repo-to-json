#!/usr/bin/env python3
"""
Replace lumped deposit creator names with poster2json's cleanly-separated
extraction authors, where the extraction is genuinely clean.

Some Zenodo depositors typed a whole author list into a single `name` field
("Gilli, G., Machado, P., ...26 authors..."). Creators are otherwise
deposit-authoritative (v0.4.0), but for these the poster2json vision/LLM
extraction usually split the byline into separate authors. This backfill swaps
in the extraction creators only when they dedup to >=2 distinct, non-lumped
names (so it never uses a duplicated/typo'd extraction).

Layout (per corpus root/dirs):
    <merged-dir>/<source>/<rec_id>_complete.json
    <extraction-dir>/<source>_<rec_id>...._extracted.json

Usage:
    python fix_lumped_creators.py --merged-dir <m> --extraction-dir <e> --dry-run [--show N]
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

from poster_to_json.field_normalize import (  # noqa: E402
    creators_are_lumped, resolve_lumped_creators,
)


def build_extraction_index(ext_dir):
    idx = {}
    for f in Path(ext_dir).glob("*_extracted.json"):
        stem = f.stem.replace("_extracted", "")
        parts = stem.split("_")
        if len(parts) >= 2 and parts[0] in ("zenodo", "figshare"):
            idx[(parts[0], parts[1])] = f
    return idx


def run(merged_dir, ext_dir, dry_run, limit, show):
    ext_index = build_extraction_index(ext_dir)
    logger.info(f"indexed {len(ext_index)} extractions")
    stats = {"scanned": 0, "lumped": 0, "replaced": 0, "kept_deposit": 0,
             "no_extraction": 0, "errors": 0}
    samples = []

    for src in ("zenodo", "figshare"):
        md = Path(merged_dir) / src
        if not md.exists():
            continue
        for f in sorted(md.glob("*_complete.json")):
            if limit and stats["scanned"] >= limit:
                break
            stats["scanned"] += 1
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error(f"  read {f.name}: {e}")
                stats["errors"] += 1
                continue
            if not isinstance(rec, dict):
                continue
            cre = rec.get("creators")
            if not creators_are_lumped(cre):
                continue
            stats["lumped"] += 1
            rid = f.stem.replace("_complete", "")
            ef = ext_index.get((src, rid))
            if not ef:
                stats["no_extraction"] += 1
                continue
            try:
                ext = json.loads(ef.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error(f"  ext {rid}: {e}")
                stats["errors"] += 1
                continue
            ext_cre = ext.get("creators") if isinstance(ext, dict) else None
            resolved = resolve_lumped_creators(cre, ext_cre)
            if resolved is cre:
                stats["kept_deposit"] += 1
                continue
            if show and len(samples) < show:
                samples.append(f"{rid}: {len(cre)}->{len(resolved)}  "
                               f"{[c.get('name') for c in cre][:1]} -> "
                               f"{[c.get('name') for c in resolved][:3]}")
            rec["creators"] = resolved
            stats["replaced"] += 1
            if not dry_run:
                try:
                    f.write_text(json.dumps(rec, indent=2, ensure_ascii=False),
                                 encoding="utf-8")
                except Exception as e:
                    logger.error(f"  write {rid}: {e}")
                    stats["errors"] += 1
    return stats, samples


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merged-dir", required=True)
    ap.add_argument("--extraction-dir", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--show", type=int, default=0)
    args = ap.parse_args()

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    logger.info(f"[{mode}] fix lumped creators  merged={args.merged_dir}")
    stats, samples = run(args.merged_dir, args.extraction_dir, args.dry_run,
                         args.limit, args.show)
    for s in samples:
        logger.info(f"  e.g. {s}")
    logger.info("Done:")
    for k in ("scanned", "lumped", "replaced", "kept_deposit", "no_extraction", "errors"):
        logger.info(f"  {k:14s} {stats[k]}")


if __name__ == "__main__":
    main()
