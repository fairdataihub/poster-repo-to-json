#!/usr/bin/env python3
"""
Normalize date fields across a merged corpus, in place.

Converts publicationYear to a valid 4-digit year (dropping garbage like 9999 or
future years) and every dates[].date to ISO 8601, parsing free-text conference
dates and dropping junk entries ("Not specified", "null", "N/A", bare
fragments). Records are never removed — only junk *values* are.

Usage:
    python normalize_dates.py --merged-dir /storage/poster-work/pre2025/merged --dry-run
    python normalize_dates.py --merged-dir /storage/poster-work/pre2025/merged --show 20
    python normalize_dates.py --merged-dir /storage/poster-work/pre2025/merged
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

from poster_to_json.date_normalize import (  # noqa: E402
    normalize_publication_year, normalize_date_value,
)


def _process(rec, max_year, samples, show):
    """Normalize one record's dates; return True if changed, recording samples."""
    changed = False

    if "publicationYear" in rec:
        py = rec["publicationYear"]
        npy = normalize_publication_year(py, max_year)
        if npy != py:
            if show and len(samples) < show:
                samples.append(f"year {py!r} -> {npy!r}")
            if npy is None:
                del rec["publicationYear"]
            else:
                rec["publicationYear"] = npy
            changed = True

    dates = rec.get("dates")
    if isinstance(dates, list):
        new_dates = []
        for d in dates:
            if not isinstance(d, dict):
                continue
            old = d.get("date")
            nd = normalize_date_value(old)
            if nd is None:
                if show and len(samples) < show:
                    samples.append(f"drop {d.get('dateType')} {old!r}")
                changed = True
                continue
            if nd != old:
                if show and len(samples) < show:
                    samples.append(f"{d.get('dateType')} {old!r} -> {nd!r}")
                d = dict(d)
                d["date"] = nd
                changed = True
            new_dates.append(d)
        if new_dates != dates:
            if new_dates:
                rec["dates"] = new_dates
            else:
                rec.pop("dates", None)
            changed = True

    return changed


def run(merged_dir, dry_run, limit, show, max_year):
    files = sorted(Path(merged_dir).rglob("*.json"))
    stats = {"scanned": 0, "changed": 0, "errors": 0}
    samples = []
    for f in files:
        if limit and stats["scanned"] >= limit:
            break
        stats["scanned"] += 1
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"  read error {f.name}: {e}")
            stats["errors"] += 1
            continue
        if not isinstance(rec, dict):
            continue
        if _process(rec, max_year, samples, show):
            stats["changed"] += 1
            if not dry_run:
                try:
                    f.write_text(json.dumps(rec, indent=2, ensure_ascii=False),
                                 encoding="utf-8")
                except Exception as e:
                    logger.error(f"  write error {f.name}: {e}")
                    stats["errors"] += 1
    return stats, samples


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merged-dir", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--show", type=int, default=0,
                    help="print up to N example transformations")
    ap.add_argument("--max-year", type=int, default=2026)
    args = ap.parse_args()

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    logger.info(f"[{mode}] normalize dates  merged-dir={args.merged_dir}  max-year={args.max_year}")
    stats, samples = run(args.merged_dir, args.dry_run, args.limit, args.show, args.max_year)
    for s in samples:
        logger.info(f"  e.g. {s}")
    logger.info("Done:")
    for k in ("scanned", "changed", "errors"):
        logger.info(f"  {k:10s} {stats[k]}")


if __name__ == "__main__":
    main()
