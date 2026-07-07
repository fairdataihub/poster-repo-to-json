#!/usr/bin/env python3
"""
Drop LLM-ADDED affiliation/organization creators (deposit-aware backfill).

Some creators are affiliation/org strings the LLM misread as authors. Where such a
creator carries an institution marker but matches NO raw deposit creator, it is an
LLM addition and is dropped; depositor-entered orgs/affiliations are KEPT (the
record-only normalize_affiliation_in_name then splits/tags them via normalize_fields).

DESTRUCTIVE, so guarded (drop_llm_affiliation_creators): never acts without deposit
evidence, uses conservative token overlap, and never empties creators. Splits lumped
names FIRST so a "A; B" person-list is separated rather than treated as an org.

Run BEFORE normalize_fields.py (which does the record-only affil split/tag + the
other creator fixes). Dry-run and review the sample before the live pass.

Usage:
    python backfill_drop_llm_affiliation.py --merged-dir <m> --metadata-dir <meta> --dry-run [--show N]
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
    drop_llm_affiliation_creators, normalize_lumped_creators,
)


def _deposit_names(src, raw):
    if src == "zenodo":
        return [c.get("name") for c in (raw.get("metadata") or {}).get("creators") or []
                if isinstance(c, dict) and c.get("name")]
    return [a.get("full_name") for a in raw.get("authors") or []
            if isinstance(a, dict) and a.get("full_name")]


def run(merged_dir, metadata_dir, dry_run, limit, show):
    stats = {"scanned": 0, "records_changed": 0, "creators_dropped": 0,
             "no_deposit": 0, "errors": 0}
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
            rid = f.stem.replace("_complete", "")
            rp = Path(metadata_dir) / src / f"{rid}.json"
            if not rp.exists():
                stats["no_deposit"] += 1
                continue
            try:
                raw = json.loads(rp.read_text(encoding="utf-8"))
            except Exception:
                stats["errors"] += 1
                continue
            names = _deposit_names(src, raw)
            if not names:
                stats["no_deposit"] += 1
                continue
            before = [c.get("name") for c in (d.get("creators") or []) if isinstance(c, dict)]
            normalize_lumped_creators(d)                       # split "A; B" lists first
            if not drop_llm_affiliation_creators(d, names):
                continue
            after = {c.get("name") for c in (d.get("creators") or []) if isinstance(c, dict)}
            dropped = [n for n in before if n and n not in after]
            stats["records_changed"] += 1
            stats["creators_dropped"] += len(dropped)
            if show and len(samples) < show:
                for n in dropped:
                    samples.append(f"{rid}: DROP {n[:90]}")
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
    logger.info(f"[{mode}] drop LLM-added affiliation creators  merged={args.merged_dir}")
    stats, samples = run(args.merged_dir, args.metadata_dir, args.dry_run, args.limit, args.show)
    for s in samples:
        logger.info(f"  e.g. {s}")
    for k in ("scanned", "records_changed", "creators_dropped", "no_deposit", "errors"):
        logger.info(f"  {k:16s} {stats[k]}")


if __name__ == "__main__":
    main()
