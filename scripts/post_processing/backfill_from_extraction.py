#!/usr/bin/env python3
"""
Backfill creators / descriptions from the LLM extraction where the merged record
has none.

The original ws209 merge dropped these when the deposit field was empty, even
though the extraction had them. This fills the gap (creators + descriptions);
run normalize_fields afterward to junk-filter, affiliation-strip and dedup the
added creators.

Usage:
    python backfill_from_extraction.py --merged-dir <m> --extraction-dir <e> --dry-run
"""
import argparse
import json
import logging
import os
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def build_idx(ext_dir):
    idx = {}
    for f in Path(ext_dir).glob("*_extracted.json"):
        parts = f.stem.replace("_extracted", "").split("_")
        if len(parts) >= 2 and parts[0] in ("zenodo", "figshare"):
            idx[(parts[0], parts[1])] = f
    return idx


def _has_desc(d):
    for x in (d.get("descriptions") or []):
        t = x.get("description") if isinstance(x, dict) else x
        if isinstance(t, str) and t.strip():
            return True
    return False


def run(merged_dir, ext_dir, dry_run, limit):
    idx = build_idx(ext_dir)
    stats = {"scanned": 0, "creators_added": 0, "descriptions_added": 0,
             "no_extraction": 0, "errors": 0}
    for src in ("zenodo", "figshare"):
        md = Path(merged_dir) / src
        if not md.exists():
            continue
        for f in sorted(md.glob("*_complete.json")):
            if limit and stats["scanned"] >= limit:
                break
            stats["scanned"] += 1
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                stats["errors"] += 1
                continue
            if not isinstance(d, dict):
                continue
            need_c = not d.get("creators")
            need_d = not _has_desc(d)
            if not (need_c or need_d):
                continue
            rid = f.stem.replace("_complete", "")
            ef = idx.get((src, rid))
            if not ef:
                stats["no_extraction"] += 1
                continue
            try:
                ext = json.loads(ef.read_text(encoding="utf-8"))
            except Exception:
                stats["errors"] += 1
                continue
            changed = False
            if need_c and isinstance(ext.get("creators"), list) and ext["creators"]:
                d["creators"] = ext["creators"]
                stats["creators_added"] += 1
                changed = True
            if need_d and isinstance(ext.get("descriptions"), list) and _has_desc(ext):
                d["descriptions"] = ext["descriptions"]
                stats["descriptions_added"] += 1
                changed = True
            if changed and not dry_run:
                try:
                    f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
                except Exception as e:
                    logger.error(f"  write {rid}: {e}")
                    stats["errors"] += 1
    return stats


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merged-dir", required=True)
    ap.add_argument("--extraction-dir", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    logger.info(f"[{mode}] backfill from extraction  merged={args.merged_dir}")
    stats = run(args.merged_dir, args.extraction_dir, args.dry_run, args.limit)
    logger.info("Done:")
    for k in ("scanned", "creators_added", "descriptions_added", "no_extraction", "errors"):
        logger.info(f"  {k:20s} {stats[k]}")


if __name__ == "__main__":
    main()
