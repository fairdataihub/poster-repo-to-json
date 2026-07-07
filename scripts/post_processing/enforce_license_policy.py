#!/usr/bin/env python3
"""
Enforce license policy on all merged poster JSONs.

For posters with blocked licenses (ND, All Rights Reserved, In Copyright,
etc.), strips poster2json-derived content and keeps only repository metadata.

Usage:
    python enforce_license_policy.py [--dry-run] [--merged-dir PATH]
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from license_policy import classify_license, strip_extracted_content


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--merged-dir",
        default="/home/james/corpus_output/merged",
        help="Path to merged JSONs directory",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    merged_dir = Path(args.merged_dir)
    files = sorted(merged_dir.rglob("*.json"))
    total = len(files)
    print(f"Found {total} merged JSONs in {merged_dir}")

    classifications = Counter()
    stripped = 0
    already_stripped = 0
    errors = 0
    blocked_licenses = Counter()

    for i, f in enumerate(files):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            errors += 1
            continue

        rights = data.get("rightsList", [])
        verdict = classify_license(rights)
        classifications[verdict] += 1

        if verdict in ("blocked", "unknown"):   # unknown treated as blocked (per policy)
            for entry in (rights if isinstance(rights, list) else [rights]):
                if isinstance(entry, dict):
                    label = entry.get("rightsIdentifier") or entry.get("rights") or "empty"
                elif isinstance(entry, str):
                    label = entry
                else:
                    label = "empty"
                blocked_licenses[label] += 1

            if data.get("_license_blocked"):
                already_stripped += 1
                continue

            if not args.dry_run:
                cleaned = strip_extracted_content(data)
                f.write_text(
                    json.dumps(cleaned, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            stripped += 1

        if (i + 1) % 5000 == 0:
            print(f"  ... {i + 1}/{total} processed, {stripped} stripped")

    print(f"\nDone: {total} processed, {errors} errors")
    print(f"\nLicense classification:")
    for verdict, count in classifications.most_common():
        print(f"  {verdict}: {count}")
    print(f"\nStripped this run: {stripped}")
    print(f"Already stripped: {already_stripped}")
    if args.dry_run:
        print("(dry run -- no files modified)")

    print(f"\nBlocked license breakdown:")
    for lic, count in blocked_licenses.most_common(20):
        print(f"  {count:>6}  {lic}")


if __name__ == "__main__":
    main()
