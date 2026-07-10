#!/usr/bin/env python3
"""Build a normalized name -> ROR-id index from a ROR data dump (v2 schema JSON, from
https://zenodo.org/communities/ror-data). Used by build_synlustre.py --ror-index to split
semantic clusters that span more than one real institution. Also carries each org's
ROR display name and GeoNames location (country/city) for downstream disambiguation.

A normalized name that maps to >=2 different RORs is AMBIGUOUS and dropped (so it never
triggers a false split). Names <3 chars or with no letter are skipped.

Usage:
    python build_ror_index.py --dump <v2.x-...-ror-data.json> --out ror_index.pickle
"""
import argparse
import json
import pickle
import re
import unicodedata


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dump", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    orgs = json.load(open(args.dump, encoding="utf-8"))
    name2ror, ambiguous, display, loc = {}, set(), {}, {}
    for org in orgs:
        rid = org["id"]
        disp = None
        for n in org.get("names") or []:
            if "ror_display" in (n.get("types") or []):
                disp = n["value"]
        display[rid] = disp or ((org.get("names") or [{}])[0].get("value") or rid)
        locs = org.get("locations") or []
        if locs:
            g = locs[0].get("geonames_details") or {}
            loc[rid] = (g.get("country_code"), g.get("name") or g.get("country_name"))
        for n in org.get("names") or []:
            nn = norm(n.get("value"))
            if len(nn) < 3 or not re.search(r"[a-z]", nn):
                continue
            if nn in name2ror and name2ror[nn] != rid:
                ambiguous.add(nn)
            else:
                name2ror[nn] = rid
    for nn in ambiguous:
        name2ror.pop(nn, None)
    pickle.dump({"name2ror": name2ror, "display": display, "loc": loc}, open(args.out, "wb"))
    print(f"orgs={len(orgs)}  name2ror={len(name2ror)} (dropped {len(ambiguous)} ambiguous)")


if __name__ == "__main__":
    main()
