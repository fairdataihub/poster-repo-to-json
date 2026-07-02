#!/usr/bin/env python3
"""
Add missing Handle identifiers to merged records from their raw metadata.

Institutional Figshare portals (Leicester, Loughborough, Sheffield, Middlebury,
...) mint a Handle instead of a DOI; the original conversion dropped it, leaving
those posters with no persistent identifier. This reads the raw metadata handle
and adds it as an identifierType "Handle" wherever it's present and missing.

Usage:
    python add_handle_identifiers.py --merged-dir <m> --metadata-dir <meta> --dry-run [--show N]
"""
import argparse
import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def run(merged_dir, metadata_dir, dry_run, limit, show):
    stats = {"scanned": 0, "added": 0, "already_has_handle": 0, "no_handle": 0, "errors": 0}
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
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                stats["errors"] += 1
                continue
            if not isinstance(d, dict):
                continue
            ids = d.get("identifiers") or []
            if any(isinstance(i, dict) and i.get("identifierType") == "Handle" for i in ids):
                stats["already_has_handle"] += 1
                continue
            rid = f.stem.replace("_complete", "")
            mp = Path(metadata_dir) / src / f"{rid}.json"
            if not mp.exists():
                continue
            try:
                r = json.loads(mp.read_text(encoding="utf-8"))
            except Exception:
                stats["errors"] += 1
                continue
            handle = r.get("handle") or r.get("metadata", {}).get("handle")
            handle = str(handle).strip() if handle else ""
            if not handle:
                stats["no_handle"] += 1
                continue
            ids.append({"identifier": handle, "identifierType": "Handle"})
            d["identifiers"] = ids
            stats["added"] += 1
            if show and len(samples) < show:
                samples.append(f"{src}/{rid}: +Handle {handle}")
            if not dry_run:
                try:
                    f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
                except Exception as e:
                    logger.error(f"  write {rid}: {e}")
                    stats["errors"] += 1
    return stats, samples


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merged-dir", required=True)
    ap.add_argument("--metadata-dir", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--show", type=int, default=0)
    args = ap.parse_args()

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    logger.info(f"[{mode}] add handle identifiers  merged={args.merged_dir}")
    stats, samples = run(args.merged_dir, args.metadata_dir, args.dry_run, args.limit, args.show)
    for s in samples:
        logger.info(f"  e.g. {s}")
    logger.info("Done:")
    for k in ("scanned", "added", "already_has_handle", "no_handle", "errors"):
        logger.info(f"  {k:20s} {stats[k]}")


if __name__ == "__main__":
    main()
