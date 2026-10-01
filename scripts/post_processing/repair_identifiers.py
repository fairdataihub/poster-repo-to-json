#!/usr/bin/env python3
"""
Repair record identifiers against the source deposit (one-time backfill).

Fixes two problems found in the 2026-09-30 DOI review of the blob:

1. Missing DOI. When a depositor listed the poster's own DOI as a related
   identifier (IsCitedBy, IsIdenticalTo, References, ... pointing at itself),
   the old identifier cleanup in align_schema dropped the DOI from identifiers
   and kept the self-relation. The DOI is restored from the raw harvest and the
   self-relation is dropped.
2. Multiple DOIs. DOIs found in the poster text or copied from the depositor's
   related identifiers sometimes stayed in identifiers. Every DOI that does not
   identify this record is removed. A DOI is the record's own when it is the
   first DOI, equals the deposit DOI, or contains the record id (the collapsed
   legacy family DOIs, such as CropTiPS 5483821). Removed DOIs are not turned
   into relations.

New records get the same result from align_schema in the pipeline. Idempotent.

Usage:
    python repair_identifiers.py --batch /storage/poster-work/pre2025 \\
        --batch /storage/poster-work/data2025 --dry-run
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from poster_to_json.field_normalize import repair_identifiers  # noqa: E402


def _source_doi(batch: Path, repo: str, rec_id: str):
    raw_path = batch / "metadata" / repo / f"{rec_id}.json"
    try:
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    doi = raw.get("doi") or (raw.get("metadata") or {}).get("doi")
    return str(doi).strip() if doi else None


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
        for repo_dir in sorted(p for p in (batch / "merged").iterdir() if p.is_dir()):
            repo = repo_dir.name
            for path in sorted(repo_dir.glob("*.json")):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    stats["unreadable"] += 1
                    continue
                if not isinstance(record, dict):
                    continue
                stats["records scanned"] += 1
                stem = path.name.split("_", 1)[0]
                rec_id = stem.split(".", 1)[0]
                # The raw harvest describes the deposit's latest version. An older
                # version kept beside it (903710.v1_complete.json) has its own DOI
                # and links to the latest one, so it gets no source DOI.
                source = _source_doi(batch, repo, rec_id) if stem == rec_id else None
                out = repair_identifiers(record, source, rec_id)
                if not (out["restored"] or out["self_relations"] or out["removed_dois"]):
                    continue
                stats["records changed"] += 1
                if out["restored"]:
                    stats["  DOI restored"] += 1
                if out["self_relations"]:
                    stats["  self-relations dropped"] += out["self_relations"]
                if out["removed_dois"]:
                    stats["  records with extra DOIs removed"] += 1
                    stats["  extra DOIs removed"] += len(out["removed_dois"])
                print(f"{batch.name}/{repo}/{rec_id}: restored={out['restored']} "
                      f"self_relations={out['self_relations']} removed={out['removed_dois']}")
                if not args.dry_run:
                    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n",
                                    encoding="utf-8")

    print()
    for key in sorted(stats):
        print(f"{key:36} {stats[key]}")
    print("(dry run, nothing written)" if args.dry_run else "(written in place)")


if __name__ == "__main__":
    main()
