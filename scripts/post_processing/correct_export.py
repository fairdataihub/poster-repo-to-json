#!/usr/bin/env python3
"""Produce a corrected, license-compliant version of the platform DB ndjson export.

For each record:
  1. If posterJson.rightsList is null/missing, RECOVER it from our licensed corpus
     (matched by Zenodo/Figshare id parsed from posterJson.suffix), so open posters
     whose license the export dropped are no longer mislabeled as unlicensed.
  2. Classify the (recovered) rightsList with the normalized policy.
  3. For any non-open license, STRIP the poster-derived content (strip_extracted_content
     -> drops content/captions/researchField/domain + LLM 'Other' descriptions, keeps the
     deposit Abstract, sets _license_blocked) and blank the thumbnail (imageUrl).

Usage (pubverse env):
    ~/myenv/bin/python correct_export.py \
        --export-dir <dir of *.ndjson> \
        --corpus-glob '/storage/poster-work/*2025/merged/*/*.json' \
        --out-dir <dir>
"""
import argparse
import glob
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from license_policy import classify_license, strip_extracted_content  # noqa: E402


def build_lookup(corpus_glob):
    by_srcid, by_id = {}, {}
    for f in glob.glob(corpus_glob):
        p = Path(f)
        src = p.parent.name
        rid = p.stem.split("_")[0]
        try:
            rl = json.loads(p.read_text(encoding="utf-8")).get("rightsList")
        except Exception:
            continue
        by_srcid[(src, rid)] = rl
        by_id[rid] = rl
    return by_srcid, by_id


def recover_rights(pj, by_srcid, by_id):
    """Recover a dropped rightsList from the corpus using ONLY the record's OWN id
    (suffix, its own DOI, or its 'Other' bare id) -- never the `identifiers` DOIs, which
    may be cited/related works, not this poster."""
    # 1) suffix "zenodo.<id>"
    m = re.match(r"(zenodo|figshare)\.(\w+)", str(pj.get("suffix") or ""))
    if m and (m.group(1), m.group(2)) in by_srcid:
        return by_srcid[(m.group(1), m.group(2))]
    # 2) the record's own DOI -> zenodo/figshare id
    m = re.search(r"(zenodo|figshare)\.(\w+)", str(pj.get("doi") or ""))
    if m and m.group(2) in by_id:
        return by_id[m.group(2)]
    # 3) the 'Other' identifier = the record's own bare repository id
    for i in pj.get("identifiers") or []:
        if isinstance(i, dict) and i.get("identifierType") == "Other":
            rid = str(i.get("identifier", "")).strip()
            if rid in by_id:
                return by_id[rid]
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--export-dir", required=True)
    ap.add_argument("--corpus-glob", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    print("building license lookup from corpus ...", flush=True)
    by_srcid, by_id = build_lookup(args.corpus_glob)
    print(f"  corpus licenses: {len(by_id)} ids", flush=True)

    Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    st = Counter()
    for ef in sorted(glob.glob(os.path.join(args.export_dir, "*.ndjson"))):
        out_lines = []
        for line in open(ef, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            st["records"] += 1
            try:
                rec = json.loads(line)
            except Exception:
                st["parse_err"] += 1
                continue
            pj = rec.get("posterJson")
            if isinstance(pj, str):
                try:
                    pj = json.loads(pj)
                except Exception:
                    pj = None
            if isinstance(pj, dict):
                if pj.get("rightsList") is None:                     # recover dropped license
                    rl = recover_rights(pj, by_srcid, by_id)
                    if rl is not None:
                        pj["rightsList"] = rl
                        st["license_recovered"] += 1
                cls = classify_license(pj.get("rightsList"))
                st[f"class_{cls}"] += 1
                if cls != "allowed":                                 # enforce
                    if not pj.get("_license_blocked"):
                        st["stripped"] += 1
                    pj = strip_extracted_content(pj)
                    rec["imageUrl"] = ""                             # no thumbnail for blocked
                rec["posterJson"] = pj
            out_lines.append(json.dumps(rec, ensure_ascii=False))
        Path(args.out_dir, os.path.basename(ef)).write_text("\n".join(out_lines) + "\n", encoding="utf-8")

    print("=== correction summary ===")
    for k in ("records", "license_recovered", "class_allowed", "class_blocked", "class_unknown",
              "stripped", "parse_err"):
        print(f"  {k:18s} {st.get(k, 0)}")


if __name__ == "__main__":
    main()
