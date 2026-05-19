#!/usr/bin/env python3
"""
Fix metadata dates and rights across all merged poster JSONs.

Overwrites publicationYear, dates[], and rightsList with the authoritative
values from converted repository metadata (Zenodo/Figshare). Adds a
dateType "Presented" entry from conference dates when available.

Usage:
    python fix_metadata_dates.py [--dry-run] [--merged-dir PATH] [--converted-dir PATH]
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def _strip_timestamp(date_str: str) -> str:
    if not date_str or "T" not in date_str:
        return date_str
    return date_str.split("T")[0]


def _fix_dates_array(dates):
    """Strip timestamps from all date entries."""
    if not isinstance(dates, list):
        return dates
    for entry in dates:
        if isinstance(entry, dict) and isinstance(entry.get("date"), str):
            entry["date"] = _strip_timestamp(entry["date"])
    return dates


def _add_presented_date(data):
    """Add a Presented date entry from conference dates if missing."""
    conf = data.get("conference")
    if not isinstance(conf, dict):
        return False

    start = conf.get("conferenceStartDate")
    if not start or not isinstance(start, str) or not start.strip():
        return False

    dates = data.get("dates", [])
    for d in dates:
        if isinstance(d, dict) and d.get("dateType") == "Presented":
            return False

    end = conf.get("conferenceEndDate")
    presented = f"{start}/{end}" if (end and isinstance(end, str) and end.strip()) else start
    dates.append({"date": presented, "dateType": "Presented"})
    data["dates"] = dates
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--merged-dir", default="/home/james/corpus_output/merged")
    parser.add_argument("--converted-dir", default="/home/james/corpus_output/converted")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    merged_dir = Path(args.merged_dir)
    converted_dir = Path(args.converted_dir)

    stats = Counter()

    for source in ("zenodo", "figshare"):
        source_merged = merged_dir / source
        source_converted = converted_dir / source

        if not source_merged.exists():
            print(f"Skipping {source}: {source_merged} not found")
            continue

        files = sorted(source_merged.glob("*.json"))
        print(f"\n=== {source}: {len(files)} files ===")

        for i, f in enumerate(files):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                stats["errors"] += 1
                continue

            record_id = f.stem.replace("_complete", "")
            meta_file = source_converted / f"{record_id}.json"

            if not meta_file.exists():
                stats["no_metadata"] += 1
                continue

            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                stats["errors"] += 1
                continue

            changed = False

            # Fix publicationYear
            meta_year = meta.get("publicationYear")
            if isinstance(meta_year, int) and meta_year != data.get("publicationYear"):
                data["publicationYear"] = meta_year
                stats["year_fixed"] += 1
                changed = True

            # Fix dates[]
            meta_dates = meta.get("dates")
            if isinstance(meta_dates, list) and meta_dates:
                fixed_dates = _fix_dates_array(list(meta_dates))
                old_dates = data.get("dates", [])
                old_issued = [d for d in old_dates if isinstance(d, dict) and d.get("dateType") != "Presented"]
                if fixed_dates != old_issued:
                    data["dates"] = fixed_dates
                    stats["dates_fixed"] += 1
                    changed = True

            # Fix rightsList (skip license-blocked files)
            if not data.get("_license_blocked"):
                meta_rights = meta.get("rightsList")
                if isinstance(meta_rights, list) and meta_rights:
                    if meta_rights != data.get("rightsList"):
                        data["rightsList"] = meta_rights
                        stats["rights_fixed"] += 1
                        changed = True

            # Add Presented date from conference
            if _add_presented_date(data):
                stats["presented_added"] += 1
                changed = True

            if changed:
                stats["changed"] += 1
                if not args.dry_run:
                    f.write_text(
                        json.dumps(data, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
            else:
                stats["unchanged"] += 1

            if (i + 1) % 5000 == 0:
                print(f"  ... {i + 1}/{len(files)} processed")

    print(f"\n=== Results ===")
    for key in ("changed", "unchanged", "year_fixed", "dates_fixed",
                "rights_fixed", "presented_added", "no_metadata", "errors"):
        print(f"  {key}: {stats[key]}")
    if args.dry_run:
        print("  (dry run -- no files modified)")


if __name__ == "__main__":
    main()
