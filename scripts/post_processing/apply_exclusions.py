#!/usr/bin/env python3
"""
Remove excluded records (src/poster_to_json/excluded_records.csv) from a corpus.

The merge step never writes an excluded record. This removes ones merged before
the record was excluded, including older version files (16960815.v1_complete.json).
Idempotent.

Usage:
    python apply_exclusions.py --batch /storage/poster-work/pre2025 \\
        --batch /storage/poster-work/data2025 --dry-run
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from poster_to_json.exclusions import exclusion_reason  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batch", action="append", required=True,
                    help="batch root with merged/; repeatable")
    ap.add_argument("--dry-run", action="store_true", help="report only, delete nothing")
    args = ap.parse_args()

    removed = 0
    for b in args.batch:
        for repo_dir in sorted(p for p in (Path(b) / "merged").iterdir() if p.is_dir()):
            for path in sorted(repo_dir.glob("*_complete.json")):
                reason = exclusion_reason(repo_dir.name, path.name)
                if not reason:
                    continue
                print(f"{'would remove' if args.dry_run else 'removed'} {path} ({reason})")
                if not args.dry_run:
                    path.unlink()
                removed += 1
    print(f"\n{removed} file(s) {'to remove' if args.dry_run else 'removed'}"
          + (" (dry run)" if args.dry_run else ""))


if __name__ == "__main__":
    main()
