#!/usr/bin/env python3
"""
Backfill deposit `version` onto merged records from raw metadata.

The converter never read the deposit version, so version today comes only from
the LLM (or is absent). This reads the deposit's version (Zenodo
metadata.version / Figshare record.version) and sets it deposit-authoritative.

Usage:
    python add_version.py --merged-dir <m> --metadata-dir <meta> --dry-run [--show N]
"""
import argparse
import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def _deposit_version(src, raw):
    if src == "zenodo":
        v = (raw.get("metadata") or {}).get("version")
    else:
        v = raw.get("version")
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def run(merged_dir, metadata_dir, dry_run, limit, show):
    stats = {"scanned": 0, "set": 0, "changed": 0, "no_deposit_version": 0, "errors": 0}
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
            rid = f.stem.replace("_complete", "")
            mp = Path(metadata_dir) / src / f"{rid}.json"
            if not mp.exists():
                continue
            try:
                raw = json.loads(mp.read_text(encoding="utf-8"))
            except Exception:
                stats["errors"] += 1
                continue
            dv = _deposit_version(src, raw)
            if not dv:
                stats["no_deposit_version"] += 1
                continue
            stats["set"] += 1
            if d.get("version") != dv:
                if show and len(samples) < show:
                    samples.append(f"{src}/{rid}: {d.get('version')!r} -> {dv!r}")
                d["version"] = dv
                stats["changed"] += 1
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
    logger.info(f"[{mode}] backfill deposit version  merged={args.merged_dir}")
    stats, samples = run(args.merged_dir, args.metadata_dir, args.dry_run, args.limit, args.show)
    for s in samples:
        logger.info(f"  e.g. {s}")
    for k in ("scanned", "set", "changed", "no_deposit_version", "errors"):
        logger.info(f"  {k:20s} {stats[k]}")


if __name__ == "__main__":
    main()
