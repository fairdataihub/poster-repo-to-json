#!/usr/bin/env python3
"""
Backfill: normalize deposit-imported fields across a merged corpus, in place.

Applies the field_normalize.py normalizers (currently: conference). Junk values
are dropped; records are never removed. Extend by adding normalizers to the
NORMALIZERS list as each field is tackled.

Usage:
    python normalize_fields.py --merged-dir <dir> --dry-run [--show N] [--limit N]
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
    normalize_conference, normalize_publisher, normalize_subjects,
    normalize_creators, normalize_formats,
)

# (name, fn) — fn(record) -> changed:bool.
NORMALIZERS = [
    ("conference", normalize_conference),
    ("publisher", normalize_publisher),
    ("subjects", normalize_subjects),
    ("creators", normalize_creators),
    ("formats", normalize_formats),
]


def run(merged_dir, dry_run, limit, show):
    files = sorted(Path(merged_dir).rglob("*.json"))
    stats = {"scanned": 0, "changed": 0, "errors": 0}
    per_field = {name: 0 for name, _ in NORMALIZERS}
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

        source = f.parent.name  # merged/<zenodo|figshare|extraction_only>/...
        rec_changed = False
        for name, fn in NORMALIZERS:
            before = json.dumps(rec.get(name), sort_keys=True, ensure_ascii=False)
            did = fn(rec, source) if name == "publisher" else fn(rec)
            if did:
                per_field[name] += 1
                rec_changed = True
                if show and len(samples) < show:
                    after = json.dumps(rec.get(name), sort_keys=True, ensure_ascii=False)
                    samples.append(f"{name}: {before[:110]} -> {after[:110]}")

        if rec_changed:
            stats["changed"] += 1
            if not dry_run:
                try:
                    f.write_text(json.dumps(rec, indent=2, ensure_ascii=False),
                                 encoding="utf-8")
                except Exception as e:
                    logger.error(f"  write error {f.name}: {e}")
                    stats["errors"] += 1
    return stats, per_field, samples


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merged-dir", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--show", type=int, default=0)
    args = ap.parse_args()

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    logger.info(f"[{mode}] normalize fields  merged-dir={args.merged_dir}")
    stats, per_field, samples = run(args.merged_dir, args.dry_run, args.limit, args.show)
    for s in samples:
        logger.info(f"  e.g. {s}")
    logger.info(f"per-field changes: {per_field}")
    logger.info("Done:")
    for k in ("scanned", "changed", "errors"):
        logger.info(f"  {k:10s} {stats[k]}")


if __name__ == "__main__":
    main()
