#!/usr/bin/env python3
"""Repair the small set of affiliations damaged by the pre-holdout affiliation merge, and
coerce bare-string affiliations to list form.

(1) The old synlustre merge (before the acronym/short-token holdout) collapsed a handful of
    short forms onto a wrong 2-char canonical (United Kingdom->UK, GTC->GT, LUH->HU,
    L-up->UP, UWS->UW, Wisconsin->WI, UL (Germany)->UL, MIDAS->AU). For every creator whose
    LIST affiliation still carries one of those canonicals, restore that creator's
    affiliation from the pre-merge snapshot (matched by creator name-token set). The
    corrected map is then re-applied downstream.

(2) Some creators carry affiliation as a bare string ("University of Trier, Germany") rather
    than a list, so the list-guarded normalizers skip them. Wrap those as [{"name": s}].

Usage (pubverse env):
    ~/myenv/bin/python fix_bad_affiliations.py \
        --merged-dir /storage/poster-work/pre2025/merged \
        --snapshot-dir /storage/poster-work/azure_snap/merged
"""
import argparse
import json
import re
from pathlib import Path

BAD = {"UP", "UL", "AU", "UK", "HU", "UW", "GT", "WI"}


def name_key(c):
    n = c.get("name") or f"{c.get('givenName', '')} {c.get('familyName', '')}"
    return frozenset(t for t in re.sub(r"[^a-z0-9]", " ", str(n).lower()).split() if t)


def aff_names(aff):
    out = []
    if isinstance(aff, list):
        for a in aff:
            nm = a.get("name") if isinstance(a, dict) else (a if isinstance(a, str) else None)
            if isinstance(nm, str):
                out.append(nm.strip())
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merged-dir", required=True)
    ap.add_argument("--snapshot-dir", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    restored = coerced = unmatched = files_changed = 0
    for f in Path(args.merged_dir).rglob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        creators = d.get("creators")
        if not isinstance(creators, list):
            continue
        changed = False
        snap_map = None
        for c in creators:
            if not isinstance(c, dict):
                continue
            aff = c.get("affiliation")
            # (2) coerce bare-string affiliation to list form
            if isinstance(aff, str) and aff.strip():
                c["affiliation"] = [{"name": aff.strip()}]
                coerced += 1
                changed = True
                continue
            # (1) restore creators whose list affiliation carries a bad short canonical
            if isinstance(aff, list) and BAD.intersection(aff_names(aff)):
                if snap_map is None:
                    sp = Path(args.snapshot_dir) / f.relative_to(args.merged_dir)
                    snap_map = {}
                    if sp.exists():
                        try:
                            sd = json.loads(sp.read_text(encoding="utf-8"))
                            for sc in sd.get("creators") or []:
                                if isinstance(sc, dict) and sc.get("affiliation") is not None:
                                    snap_map[name_key(sc)] = sc.get("affiliation")
                        except Exception:
                            snap_map = {}
                k = name_key(c)
                if k in snap_map:
                    sa = snap_map[k]
                    c["affiliation"] = [{"name": sa}] if isinstance(sa, str) else sa
                    restored += 1
                    changed = True
                else:
                    unmatched += 1
        if changed:
            files_changed += 1
            if not args.dry_run:
                f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"files_changed={files_changed} restored_from_snapshot={restored} "
          f"string_affils_coerced={coerced} unmatched_in_snapshot={unmatched}"
          + (" (dry-run)" if args.dry_run else ""))


if __name__ == "__main__":
    main()
