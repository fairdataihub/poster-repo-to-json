#!/usr/bin/env python3
"""
Batch normalize rightsList across all merged poster JSONs.

Applies the enhanced normalize_rights_list from poster2json which:
- Collapses CC license variants to canonical SPDX dicts
- Resolves CC URLs to SPDX identifiers
- Converts bare strings to proper dict entries
- Strips non-license junk (funding disclaimers, poster templates, etc.)
- Preserves valid non-SPDX terms (In Copyright, All Rights Reserved, etc.)

Usage:
    python normalize_licenses.py [--dry-run]
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/home/james/poster2json")
from poster2json.normalize import normalize_rights_list

MERGED_DIR = Path("/home/james/corpus_output/merged")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    total = 0
    changed = 0
    removed_entries = Counter()
    before_ids = Counter()
    after_ids = Counter()

    for f in sorted(MERGED_DIR.rglob("*.json")):
        total += 1
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        rights = data.get("rightsList")
        if not rights:
            continue

        for r in (rights if isinstance(rights, list) else [rights]):
            if isinstance(r, dict):
                before_ids[r.get("rightsIdentifier") or r.get("rights") or "empty"] += 1
            elif isinstance(r, str):
                before_ids[f"STR:{r[:60]}"] += 1

        normalized = normalize_rights_list(rights)

        for r in normalized:
            if isinstance(r, dict):
                after_ids[r.get("rightsIdentifier") or r.get("rights") or "empty"] += 1

        old_canonical = json.dumps(rights, sort_keys=True)
        new_canonical = json.dumps(normalized, sort_keys=True)

        if old_canonical != new_canonical:
            changed += 1
            old_count = len(rights) if isinstance(rights, list) else 1
            new_count = len(normalized)
            if old_count > new_count:
                removed_entries["junk_removed"] += old_count - new_count
            if not args.dry_run:
                data["rightsList"] = normalized
                f.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

        if total % 5000 == 0:
            print(f"  ... {total} processed, {changed} changed", file=sys.stderr)

    print(f"\nTotal: {total}")
    print(f"Changed: {changed}")
    print(f"Junk entries removed: {removed_entries.get('junk_removed', 0)}")
    if args.dry_run:
        print("(dry run -- no files modified)")

    print("\n=== After normalization: license distribution ===")
    for lic, count in after_ids.most_common(30):
        print(f"  {count:>6}  {lic}")


if __name__ == "__main__":
    main()
