#!/usr/bin/env python3
"""Recover dropped licenses for a platform DB ndjson export from DataCite.

Some export records have ``posterJson.rightsList = null`` and are also null in
our licensed corpus (the license was never captured on our side either). Their
DataCite deposit, however, usually still declares the license. This tool fetches
each such record's ``rightsList`` from the public DataCite REST API by the
record's OWN DOI and injects it, so the downstream policy (correct_export.py /
enforce_license_policy.py) classifies the real license instead of default-denying
an actually-open poster.

It writes a corrected copy of the export (never mutates the input) and prints a
classification summary. Records whose DataCite license is non-open, empty, or
unrecoverable are left null and will be default-denied downstream, unchanged.

Usage:
    python backfill_export_licenses_datacite.py \
        --export-dir <dir of *.ndjson> \
        --out-dir <dir> \
        [--cache datacite_cache.json] [--sleep 0.3]
"""
import argparse
import glob
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from license_policy import classify_license  # noqa: E402

API = "https://api.datacite.org/dois/"


def fetch_rights(doi, timeout=20):
    url = API + urllib.parse.quote(doi, safe="")
    req = urllib.request.Request(url, headers={"User-Agent": "posters.science-license-qc"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    rl = d.get("data", {}).get("attributes", {}).get("rightsList")
    # DataCite marks access level with a bare "rights" entry (Open/Restricted
    # Access) alongside the real license; keep only entries that carry a license
    # id/uri or a Creative-Commons/CC0/named-license string.
    keep = []
    for e in rl or []:
        if not isinstance(e, dict):
            continue
        if e.get("rightsIdentifier") or e.get("rightsUri"):
            keep.append(e)
        elif "creative commons" in str(e.get("rights", "")).lower() or "cc0" in str(e.get("rights", "")).lower():
            keep.append(e)
    return keep or None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--export-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--cache", default=None, help="JSON {doi: rightsList} cache, read+write")
    ap.add_argument("--sleep", type=float, default=0.3)
    args = ap.parse_args()

    cache = {}
    if args.cache and os.path.exists(args.cache):
        cache = json.loads(Path(args.cache).read_text(encoding="utf-8"))

    Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    st = Counter()
    cls_recovered = Counter()
    for ef in sorted(glob.glob(os.path.join(args.export_dir, "*.ndjson"))):
        out_lines = []
        for line in open(ef, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            st["records"] += 1
            rec = json.loads(line)
            pj = rec.get("posterJson")
            if isinstance(pj, dict) and pj.get("rightsList") is None:
                doi = pj.get("doi")
                if doi:
                    st["null_with_doi"] += 1
                    if doi in cache:
                        rl = cache[doi]
                    else:
                        try:
                            rl = fetch_rights(doi)
                        except Exception as ex:  # noqa: BLE001
                            rl = None
                            st["fetch_err"] += 1
                        cache[doi] = rl
                        time.sleep(args.sleep)
                    if rl:
                        pj["rightsList"] = rl
                        st["recovered"] += 1
                        cls_recovered[classify_license(rl)] += 1
                    rec["posterJson"] = pj
            out_lines.append(json.dumps(rec, ensure_ascii=False))
        Path(args.out_dir, os.path.basename(ef)).write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        if args.cache:
            Path(args.cache).write_text(json.dumps(cache), encoding="utf-8")

    print("=== DataCite license backfill summary ===")
    for k in ("records", "null_with_doi", "recovered", "fetch_err"):
        print(f"  {k:16s} {st.get(k, 0)}")
    print(f"  recovered license classes: {dict(cls_recovered)}")


if __name__ == "__main__":
    main()
