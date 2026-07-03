#!/usr/bin/env python3
"""
Phase-2 backfill from raw deposit metadata (SCHEMA_ALIGNMENT_PLAN.md):
  - Zenodo: fill conference start/end for free-text meeting date ranges.
  - Figshare: add relatedIdentifiers from references[] + related_materials[]
    (union with any extraction-discovered ones, deposit preferred).

Re-runs the (fixed) converter on the raw record and patches only these fields
into the delivered merged record; everything else is left as-is.

Zenodo relatedIdentifiers and all funding are intentionally out of scope here
(handled in the Phase-3 DataCite re-fetch).

Usage:
    python backfill_phase2.py --merged-dir <m> --metadata-dir <meta> --dry-run [--show N]
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

_conv = SchemaConverter()


def _union_related(fresh, existing):
    out, seen = [], set()
    for r in (fresh or []) + (existing or []):
        if not isinstance(r, dict):
            continue
        k = str(r.get("relatedIdentifier", "")).lower()
        if k and k not in seen:
            seen.add(k)
            out.append(r)
    return out


def run(merged_dir, metadata_dir, dry_run, limit, show):
    stats = {"scanned": 0, "conf_dates": 0, "fig_related": 0, "no_metadata": 0, "errors": 0}
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
                stats["no_metadata"] += 1
                continue
            try:
                raw = json.loads(mp.read_text(encoding="utf-8"))
                fresh = _conv.convert(raw, source=src)
            except Exception as e:
                logger.error(f"  {rid}: {e}")
                stats["errors"] += 1
                continue
            changed = False

            if src == "zenodo":
                mc = d.get("conference")
                fc = fresh.get("conference") or {}
                if isinstance(mc, dict):
                    for k in ("conferenceStartDate", "conferenceEndDate"):
                        if not mc.get(k) and fc.get(k):
                            mc[k] = fc[k]
                            changed = True
                    if changed and show and len(samples) < show:
                        samples.append(f"{rid}: conf {mc.get('conferenceStartDate')}..{mc.get('conferenceEndDate')}")
                if changed:
                    stats["conf_dates"] += 1
            else:
                fr = fresh.get("relatedIdentifiers")
                if fr:
                    merged_ri = d.get("relatedIdentifiers")
                    new = _union_related(fr, merged_ri)
                    if new != merged_ri:
                        d["relatedIdentifiers"] = new
                        changed = True
                        stats["fig_related"] += 1
                        if show and len([s for s in samples if s.startswith(rid)]) == 0 and len(samples) < show:
                            samples.append(f"{rid}: +{len(fr)} relatedIdentifiers")

            if changed and not dry_run:
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
    logger.info(f"[{mode}] phase-2 backfill  merged={args.merged_dir}")
    stats, samples = run(args.merged_dir, args.metadata_dir, args.dry_run, args.limit, args.show)
    for s in samples:
        logger.info(f"  e.g. {s}")
    for k in ("scanned", "conf_dates", "fig_related", "no_metadata", "errors"):
        logger.info(f"  {k:14s} {stats[k]}")


if __name__ == "__main__":
    main()
