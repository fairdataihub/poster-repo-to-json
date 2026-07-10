#!/usr/bin/env python3
"""Drop single-character junk affiliation names from an already-built corpus.

normalize_affiliation_names keeps any affiliation with >=1 letter (so short surnames
survive when a name lands in an affiliation slot), which lets lone letters -- "e", "a",
"i", "n", "o" -- slip through as institutions. A single character is never a real
institution, so this removes affiliation entries whose NFKC-stripped name is one
character, per creator, dropping the affiliation key if it empties. Never touches a
creator otherwise. Idempotent. (The same rule now lives in normalize_affiliation_names
for future runs; this backfills the delivered corpus.)

Usage (pubverse env):
    ~/myenv/bin/python drop_junk_affiliations.py --merged-dir /storage/poster-work/pre2025/merged
"""
import argparse
import glob
import json
import unicodedata
from pathlib import Path


def clean_record(d):
    cres = d.get("creators")
    if not isinstance(cres, list):
        return 0
    dropped = 0
    for c in cres:
        if not isinstance(c, dict) or not isinstance(c.get("affiliation"), list):
            continue
        keep = []
        for a in c["affiliation"]:
            name = a.get("name") if isinstance(a, dict) else (a if isinstance(a, str) else None)
            if isinstance(name, str) and len(unicodedata.normalize("NFKC", name).strip()) <= 1:
                dropped += 1
                continue
            keep.append(a)
        if len(keep) != len(c["affiliation"]):
            if keep:
                c["affiliation"] = keep
            else:
                c.pop("affiliation", None)
    return dropped


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merged-dir", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    files = glob.glob(str(Path(args.merged_dir) / "*" / "*.json"))
    changed = total = errors = 0
    for f in files:
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            errors += 1
            continue
        n = clean_record(d)
        if n:
            total += n
            changed += 1
            if not args.dry_run:
                Path(f).write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"files={len(files)} changed={changed} affiliations_dropped={total} errors={errors}"
          + (" (dry-run)" if args.dry_run else ""))


if __name__ == "__main__":
    main()
