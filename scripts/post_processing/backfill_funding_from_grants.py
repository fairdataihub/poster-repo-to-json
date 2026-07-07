#!/usr/bin/env python3
"""Backfill: make fundingReferences DEPOSIT-AUTHORITATIVE from Zenodo grants[].

"Zenodo funding wins": for each zenodo merged record whose deposit metadata has
grants[], rebuild fundingReferences from the grants (funder name + Crossref id,
award number/title/uri), replacing any LLM/DataCite funding. A resolved
funderIdentifier already present is carried over when a grant lacks funder.doi.
Records with no usable grants keep their existing funding. Idempotent.

Usage:
    python backfill_funding_from_grants.py --merged-dir <m> --metadata-dir <meta> --dry-run [--show N] [--limit N]
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
from poster_to_json.field_normalize import funding_from_grants, conform_to_schema  # noqa: E402


def run(merged_dir, metadata_dir, dry_run, limit, show):
    stats = {"scanned": 0, "changed": 0, "no_raw": 0, "errors": 0}
    samples = []
    md = Path(merged_dir) / "zenodo"
    if not md.exists():
        return stats, samples
    for f in sorted(md.glob("*_complete.json")):
        if limit and stats["scanned"] >= limit:
            break
        stats["scanned"] += 1
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            stats["errors"] += 1
            continue
        rid = f.stem.replace("_complete", "")
        rp = Path(metadata_dir) / "zenodo" / f"{rid}.json"
        if not rp.exists():
            stats["no_raw"] += 1
            continue
        try:
            raw = json.loads(rp.read_text(encoding="utf-8"))
        except Exception:
            stats["errors"] += 1
            continue
        grants = (raw.get("metadata") or {}).get("grants") or []
        old = len(d.get("fundingReferences") or [])
        if funding_from_grants(d, grants):
            conform_to_schema(d)                       # keep schema-clean after rewrite
            stats["changed"] += 1
            if show and len(samples) < show:
                new = len(d.get("fundingReferences") or [])
                samples.append(f"{rid}: {old} -> {new} entries")
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
    logger.info(f"[{'DRY-RUN' if args.dry_run else 'LIVE'}] funding from grants  merged={args.merged_dir}")
    stats, samples = run(args.merged_dir, args.metadata_dir, args.dry_run, args.limit, args.show)
    for s in samples:
        logger.info(f"  e.g. {s}")
    for k in ("scanned", "changed", "no_raw", "errors"):
        logger.info(f"  {k:10s} {stats[k]}")


if __name__ == "__main__":
    main()
