#!/usr/bin/env python3
"""Repair the 3 department-level cross-campus affiliation mis-merges.

The affiliation synonym merge collapsed a few long "Department of X, University of
California, <campus>" strings across campuses (the department name dominated the
embedding and the string did not resolve to a ROR, so the ROR split could not catch
it). Each such (correct_variant -> wrong_canonical) pair is listed below. We restore
only the mis-mapped entries -- identified by matching the same creator in the pre-merge
snapshot -- so legitimate affiliations that genuinely carry the canonical value are
left untouched.

Usage (pubverse env):
    ~/myenv/bin/python fix_uc_crosscampus.py \
        --merged-dir /storage/poster-work/pre2025/merged \
        --snapshot-dir /storage/poster-work/azure_snap/merged [--dry-run]
"""
import argparse
import json
import re
from pathlib import Path

# (correct original variant, wrong canonical it was merged into)
PAIRS = [
    ("Department of Chemistry and Biochemistry, University of California, San Diego, La Jolla, CA 9203, USA",
     "Department of Chemistry and Biochemistry, University of California, Santa Cruz"),
    ("Environmental Science, Policy, and Management, University of California, Berkeley",
     "Department of Environmental Science and Policy at UC Davis"),
    ("Department of Chemistry, University of California Davis, Davis, CA, USA",
     "Pharmaceutical Sciences, University of California Irvine, Irvine, CA, USA"),
]
WRONG = {canon for _, canon in PAIRS}


def name_key(c):
    n = c.get("name") or f"{c.get('givenName', '')} {c.get('familyName', '')}"
    return frozenset(t for t in re.sub(r"[^a-z0-9]", " ", str(n).lower()).split() if t)


def snap_variants(sd):
    """name_key -> set of original affiliation names for that creator in the snapshot."""
    out = {}
    for c in sd.get("creators") or []:
        if not isinstance(c, dict):
            continue
        names = set()
        for a in c.get("affiliation") or []:
            n = a.get("name") if isinstance(a, dict) else (a if isinstance(a, str) else None)
            if isinstance(n, str):
                names.add(n)
        out.setdefault(name_key(c), set()).update(names)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merged-dir", required=True)
    ap.add_argument("--snapshot-dir", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    restored = 0
    for f in Path(args.merged_dir).rglob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        creators = d.get("creators")
        if not isinstance(creators, list):
            continue
        # any creator carrying one of the wrong canonicals?
        if not any(isinstance(a, dict) and a.get("name") in WRONG
                   for c in creators if isinstance(c, dict)
                   for a in (c.get("affiliation") or [])):
            continue
        sp = Path(args.snapshot_dir) / f.relative_to(args.merged_dir)
        if not sp.exists():
            continue
        try:
            sv = snap_variants(json.loads(sp.read_text(encoding="utf-8")))
        except Exception:
            continue
        changed = False
        for c in creators:
            if not isinstance(c, dict) or not isinstance(c.get("affiliation"), list):
                continue
            orig = sv.get(name_key(c), set())
            for a in c["affiliation"]:
                if isinstance(a, dict) and a.get("name") in WRONG:
                    # restore only if the snapshot creator carried the correct variant
                    for variant, canon in PAIRS:
                        if a["name"] == canon and variant in orig:
                            a["name"] = variant
                            restored += 1
                            changed = True
                            break
        if changed and not args.dry_run:
            f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"restored {restored} cross-campus affiliation entries" + (" (dry-run)" if args.dry_run else ""))


if __name__ == "__main__":
    main()
