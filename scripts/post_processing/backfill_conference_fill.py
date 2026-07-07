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
    fill_conference_from_meeting, clean_conference_junk,
    _meeting_date_parts, _meeting_iso_or_none)
from poster_to_json.date_normalize import normalize_date_value  # noqa: E402

_MAX_YEAR = 2026


def _buggy_meeting_date_parts(dates_str):
    """The v0.35.0 PRE-FIX parse (unconditional ' - ' -> '/'), used only to detect a
    conferenceStartDate the buggy fill wrote so this pass can correct exactly those."""
    if not isinstance(dates_str, str) or not dates_str.strip():
        return None, None
    src = dates_str.replace(" - ", "/") if " - " in dates_str else dates_str
    parsed = normalize_date_value(src)
    start = end = None
    if parsed and "/" in parsed:
        start, end = parsed.split("/", 1)
    elif parsed:
        start = parsed
    return _meeting_iso_or_none(start), _meeting_iso_or_none(end)


def _correct_buggy_dates(record, meeting):
    """Re-derive the meeting dates with the FIXED parser and correct a conferenceStartDate
    that the earlier buggy parser wrote (stranded leading day -> last day as start).
    Surgical: only touches a date equal to the buggy parse, never a real LLM date."""
    conf = record.get("conference")
    if not isinstance(conf, dict):
        return False
    ds = meeting.get("dates")
    bstart, bend = _buggy_meeting_date_parts(ds)
    fstart, fend, _ = _meeting_date_parts(ds)
    if bstart and conf.get("conferenceStartDate") == bstart and fstart and fstart != bstart:
        conf["conferenceStartDate"] = fstart
        if fend:
            conf["conferenceEndDate"] = fend
        elif conf.get("conferenceEndDate") == bend:
            conf.pop("conferenceEndDate", None)
        return True
    return False


def _drop_nameless_conference(record):
    """Schema requires conferenceName; drop a conference object that has none."""
    conf = record.get("conference")
    if isinstance(conf, dict) and not conf.get("conferenceName"):
        record.pop("conference", None)
        return True
    return False


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
        c_fill = fill_conference_from_meeting(d, meeting)     # fill gaps (fixed parser)
        c_date = _correct_buggy_dates(d, meeting)             # correct buggy-parsed start dates
        c_junk = clean_conference_junk(d)                     # strip junk name/acronym
        c_name = _drop_nameless_conference(d)                 # drop a now-nameless conference
        c_year = _year_fallback(d)                            # add year if named but yearless
        if c_fill or c_date or c_junk or c_name or c_year:
            stats["changed"] += 1
            if show and len(samples) < show:
                tag = ("date" if c_date else "dropped-nameless" if c_name
                       else "filled" if c_fill else "cleaned")
                samples.append(f"{rid}: {tag}")
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
