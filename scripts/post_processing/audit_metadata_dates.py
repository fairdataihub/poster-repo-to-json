#!/usr/bin/env python3
"""
Audit metadata dates across all merged poster JSONs (read-only).

Reports publicationYear distribution, dateType breakdown, timestamp
residue, and missing-dates counts.

Usage:
    python audit_metadata_dates.py [--merged-dir PATH]
"""

import argparse
import json
from collections import Counter
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--merged-dir", default="/home/james/corpus_output/merged")
    args = parser.parse_args()

    merged_dir = Path(args.merged_dir)
    files = sorted(merged_dir.rglob("*.json"))
    print(f"Scanning {len(files)} merged JSONs in {merged_dir}\n")

    year_counts = Counter()
    date_type_counts = Counter()
    has_dates = 0
    no_dates = 0
    has_timestamp = 0
    total = 0

    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        total += 1
        year_counts[data.get("publicationYear")] += 1

        dates = data.get("dates", [])
        if dates and isinstance(dates, list):
            has_dates += 1
            for d in dates:
                if isinstance(d, dict):
                    date_type_counts[d.get("dateType", "(none)")] += 1
                    date_val = d.get("date", "")
                    if isinstance(date_val, str) and "T" in date_val:
                        has_timestamp += 1
        else:
            no_dates += 1

    print(f"Total: {total}")
    print(f"Has dates[]: {has_dates}")
    print(f"No dates[]: {no_dates}")
    print(f"Dates with timestamps (T in value): {has_timestamp}")

    print(f"\npublicationYear distribution:")
    for year, count in year_counts.most_common(25):
        marker = " <<<" if year == 2025 else ""
        print(f"  {year}: {count}{marker}")

    print(f"\ndateType breakdown:")
    for dt, count in date_type_counts.most_common():
        print(f"  {dt}: {count}")


if __name__ == "__main__":
    main()
