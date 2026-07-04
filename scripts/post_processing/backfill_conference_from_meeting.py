#!/usr/bin/env python3
"""
Restore Zenodo conference blocks from the deposit `meeting` (authoritative).

The merge lets an LLM-extracted conference win over the deposit; during the
2025-hallucination era that meant hallucinated conference years/dates overrode
the correct deposit meeting. Where the Zenodo deposit has a meeting WITH real
date info, rebuild the conference from it (schema_converter.conference_from_meeting)
so the authoritative dates replace the LLM's. Figshare has no meeting field, so it
is untouched here (the sanitizer handles its hallucinated dates against the
deposit-authoritative publicationYear).

Run BEFORE normalize_fields (reconcile_publication_year + sanitize_conference_dates
+ ensure_presented_date) so the restored dates are the ones a Presented date is
derived from.

Usage:
    python backfill_conference_from_meeting.py --merged-dir <m> --metadata-dir <meta> --dry-run [--show N]
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
from poster_to_json.schema_converter import conference_from_meeting  # noqa: E402


def run(merged_dir, metadata_dir, dry_run, limit, show):
    md = Path(merged_dir) / "zenodo"
    stats = {"scanned": 0, "restored": 0, "no_meeting": 0, "no_raw": 0, "errors": 0}
    samples = []
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
        conf = conference_from_meeting(meeting)
        # Only override when the deposit meeting carries authoritative date info.
        if not conf or not (conf.get("conferenceStartDate") or conf.get("conferenceYear")):
            stats["no_meeting"] += 1
            continue
        if d.get("conference") == conf:
            continue
        if show and len(samples) < show:
            old = d.get("conference") or {}
            samples.append(f"{rid}: {old.get('conferenceYear')}/{old.get('conferenceStartDate')} "
                           f"-> {conf.get('conferenceYear')}/{conf.get('conferenceStartDate')}")
        d["conference"] = conf
        stats["restored"] += 1
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
    logger.info(f"[{mode}] restore conference from meeting  merged={args.merged_dir}")
    stats, samples = run(args.merged_dir, args.metadata_dir, args.dry_run, args.limit, args.show)
    for s in samples:
        logger.info(f"  e.g. {s}")
    for k in ("scanned", "restored", "no_meeting", "no_raw", "errors"):
        logger.info(f"  {k:12s} {stats[k]}")


if __name__ == "__main__":
    main()
