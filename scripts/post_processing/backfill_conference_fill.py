#!/usr/bin/env python3
"""Backfill: fill the conference object from the Zenodo deposit `meeting`.

"Zenodo conference wins": the old backfill_conference_from_meeting.py gated the whole
restore on a parseable ISO start date, so 937 records with a meeting title but no usable
date got NO conferenceName (and 286 no location). This fills each conference sub-field
from the meeting independently (no-clobber: only missing/placeholder fields), then, per
the schema's required [conferenceName, conferenceYear], backfills a missing conferenceYear
from the (capped) publicationYear. Runs on the already-sanitized corpus so deposit dates
fill the gap sanitize_conference_dates opened. Idempotent.

Usage:
    python backfill_conference_fill.py --merged-dir <m> --metadata-dir <meta> --dry-run [--show N] [--limit N]
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
    fill_conference_from_meeting, clean_conference_junk)

_MAX_YEAR = 2026


def _year_fallback(record):
    """Schema requires [conferenceName, conferenceYear]. If a filled conference has a name
    but no year, use the (capped) publicationYear -- the deposit-reconciled best estimate."""
    conf = record.get("conference")
    if not (isinstance(conf, dict) and conf.get("conferenceName") and not conf.get("conferenceYear")):
        return False
    try:
        py = int(record.get("publicationYear"))
    except (TypeError, ValueError):
        return False
    if 1900 <= py <= _MAX_YEAR:
        conf["conferenceYear"] = py
        return True
    return False


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
        meeting = (raw.get("metadata") or {}).get("meeting") or {}
        had = (d.get("conference") or {}).get("conferenceName") if isinstance(d.get("conference"), dict) else None
        changed = fill_conference_from_meeting(d, meeting)
        changed = _year_fallback(d) or changed
        if changed:
            clean_conference_junk(d)                    # never leave a junk name behind
            stats["changed"] += 1
            if show and len(samples) < show and not had:
                nm = (d.get("conference") or {}).get("conferenceName")
                samples.append(f"{rid}: name={str(nm)[:45]!r}")
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
    logger.info(f"[{'DRY-RUN' if args.dry_run else 'LIVE'}] conference fill  merged={args.merged_dir}")
    stats, samples = run(args.merged_dir, args.metadata_dir, args.dry_run, args.limit, args.show)
    for s in samples:
        logger.info(f"  e.g. {s}")
    for k in ("scanned", "changed", "no_raw", "errors"):
        logger.info(f"  {k:10s} {stats[k]}")


if __name__ == "__main__":
    main()
