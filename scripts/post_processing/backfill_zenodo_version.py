#!/usr/bin/env python3
"""
Fill an empty Zenodo `version` with the repository's own number (backfill).

Figshare's API returns an integer version on every record, 1 for a lone
poster, and we copy it. Zenodo's equivalent is the record's position in its
concept, relations.version[].index (0-based), so its version is index + 1.
New records get this from convert_zenodo. This applies it to an existing
corpus: every Zenodo record whose `version` is empty and whose raw harvest has
a position gets str(index + 1), a lone poster "1".

A version the depositor supplied is never changed. Idempotent.

Usage:
    python backfill_zenodo_version.py --batch /storage/poster-work/pre2025 \\
        --batch /storage/poster-work/data2025 --dry-run
"""

import argparse
import json
from collections import Counter
from pathlib import Path


def _position(batch: Path, record_path: Path):
    rec_id = record_path.name.split("_", 1)[0].split(".", 1)[0]
    raw_path = batch / "metadata" / "zenodo" / f"{rec_id}.json"
    try:
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    relations = (raw.get("metadata") or {}).get("relations") or raw.get("relations") or {}
    vlist = relations.get("version") if isinstance(relations, dict) else None
    if isinstance(vlist, list) and vlist and isinstance(vlist[0], dict):
        idx = vlist[0].get("index")
        if isinstance(idx, int) and not isinstance(idx, bool) and idx >= 0:
            return idx
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batch", action="append", required=True,
                    help="batch root with merged/ and metadata/; repeatable")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = ap.parse_args()

    stats = Counter()
    for b in args.batch:
        batch = Path(b)
        for path in sorted((batch / "merged" / "zenodo").glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                stats["unreadable"] += 1
                continue
            if not isinstance(record, dict):
                continue
            stats["zenodo records scanned"] += 1
            current = record.get("version")
            if current not in (None, "") and str(current).strip():
                stats["already has a version (kept)"] += 1
                continue
            idx = _position(batch, path)
            if idx is None:
                stats["no position in raw harvest (left empty)"] += 1
                continue
            record["version"] = str(idx + 1)
            stats["filled"] += 1
            stats[f"  filled with {record['version'] if idx < 5 else '6+'}"] += 1
            if not args.dry_run:
                path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n",
                                encoding="utf-8")

    print()
    for key in sorted(stats):
        print(f"{key:42} {stats[key]}")
    print("(dry run, nothing written)" if args.dry_run else "(written in place)")


if __name__ == "__main__":
    main()
