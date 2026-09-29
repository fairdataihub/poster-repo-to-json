#!/usr/bin/env python3
"""
Add References relations for citations that carry an identifier (backfill).

New records get these from the pipeline itself (schema_converter reads the
Zenodo reference list; the merger links the references poster2json parsed off
the poster). This applies the same rule to an existing corpus:

- Zenodo records: every citation in the deposit's own reference list
  (metadata.references in the raw harvest) that carries a DOI, arXiv id, PMID
  or URL becomes a References relation.
- All records: the same for the references poster2json parsed off the poster
  (the record's `references` field).

Relations are appended and deduplicated; nothing existing is changed or
removed. Idempotent: a second run changes nothing.

Usage:
    python backfill_reference_links.py --batch /storage/poster-work/pre2025 \\
        --batch /storage/poster-work/data2025 --dry-run
    python backfill_reference_links.py --batch /storage/poster-work/pre2025 \\
        --batch /storage/poster-work/data2025

A batch root holds merged/<repository>/*.json and metadata/<repository>/*.json.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from poster_to_json.reference_links import add_reference_links  # noqa: E402


def _zenodo_references(batch: Path, record_path: Path):
    rec_id = record_path.name.split("_", 1)[0].split(".", 1)[0]
    raw_path = batch / "metadata" / "zenodo" / f"{rec_id}.json"
    if not raw_path.exists():
        return []
    try:
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return []
    return (raw.get("metadata") or {}).get("references") or []


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
        for path in sorted((batch / "merged").glob("*/*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                stats["unreadable"] += 1
                continue
            if not isinstance(record, dict):
                continue
            stats["records scanned"] += 1
            before = list(record.get("relatedIdentifiers") or [])

            added_repo = 0
            if path.parent.name == "zenodo":
                added_repo = add_reference_links(record, _zenodo_references(batch, path))
            added_poster = add_reference_links(record, record.get("references") or [])
            if not (added_repo or added_poster):
                continue

            stats["records changed"] += 1
            stats["links from repository reference list"] += added_repo
            stats["links from poster-parsed references"] += added_poster
            for r in (record.get("relatedIdentifiers") or [])[len(before):]:
                stats[f"  added {r['relatedIdentifierType']}"] += 1
            if not args.dry_run:
                path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n",
                                encoding="utf-8")

    print()
    for key in sorted(stats):
        print(f"{key:42} {stats[key]}")
    print("(dry run, nothing written)" if args.dry_run else "(written in place)")


if __name__ == "__main__":
    main()
