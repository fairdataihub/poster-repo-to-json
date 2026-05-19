#!/usr/bin/env python3
"""
Add dateType "Submitted" to all merged poster JSONs from raw metadata.

Reads Zenodo `created` and Figshare `created_date` from raw metadata
and adds a Submitted entry to the dates[] array.

Usage:
    python fix_submitted_dates.py [--dry-run] [--merged-dir PATH] [--raw-dir PATH]
"""

import argparse
import json
from collections import Counter
from pathlib import Path


def _strip_timestamp(date_str: str) -> str:
    if not date_str or "T" not in date_str:
        return date_str
    return date_str.split("T")[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--merged-dir", default="/home/james/corpus_output/merged")
    parser.add_argument("--raw-dir", default="/home/james/metadata")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    merged_dir = Path(args.merged_dir)
    raw_dir = Path(args.raw_dir)

    stats = Counter()

    for source in ("zenodo", "figshare"):
        source_merged = merged_dir / source
        source_raw = raw_dir / source

        if not source_merged.exists():
            print(f"Skipping {source}: {source_merged} not found")
            continue
        if not source_raw.exists():
            print(f"Skipping {source}: {source_raw} not found")
            continue

        files = sorted(source_merged.glob("*.json"))
        print(f"\n=== {source}: {len(files)} files ===")

        for i, f in enumerate(files):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                stats["errors"] += 1
                continue

            # Check if already has a Submitted date
            dates = data.get("dates", [])
            has_submitted = any(
                isinstance(d, dict) and d.get("dateType") == "Submitted"
                for d in dates
            )
            if has_submitted:
                stats["already_has"] += 1
                continue

            record_id = f.stem.replace("_complete", "")
            raw_file = source_raw / f"{record_id}.json"

            if not raw_file.exists():
                stats["no_raw"] += 1
                continue

            try:
                raw = json.loads(raw_file.read_text(encoding="utf-8"))
            except Exception:
                stats["errors"] += 1
                continue

            if source == "zenodo":
                created = raw.get("created", "")
            else:
                created = raw.get("created_date", "")

            if not created:
                stats["no_created"] += 1
                continue

            clean_date = _strip_timestamp(created)
            dates.append({"date": clean_date, "dateType": "Submitted"})
            data["dates"] = dates

            stats["added"] += 1
            if not args.dry_run:
                f.write_text(
                    json.dumps(data, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )

            if (i + 1) % 5000 == 0:
                print(f"  ... {i + 1}/{len(files)} processed")

    print(f"\n=== Results ===")
    for key in ("added", "already_has", "no_raw", "no_created", "errors"):
        print(f"  {key}: {stats[key]}")
    if args.dry_run:
        print("  (dry run -- no files modified)")


if __name__ == "__main__":
    main()
