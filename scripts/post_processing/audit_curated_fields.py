#!/usr/bin/env python3
"""
Read-only audit of the curated-field deltas across a merged corpus.

For every merged record that has matching converted deposit metadata, compare
the curated fields against the deposit so a backfill can be verified
corpus-wide. Run it BEFORE and AFTER the backfill: before, the
"primary description is the deposit Abstract" count is low (extraction won);
after, it should approach the "deposit description present" count.

Layout (same as run_merge.py / backfill_curated_fields.py):
    <root>/converted/{zenodo,figshare}/<rec_id>.json
    <root>/merged/{zenodo,figshare}/<rec_id>_complete.json

Usage:
    python audit_curated_fields.py --root /home/james/corpus_output
    python audit_curated_fields.py --root /home/james/corpus_output --sample 5
"""
import argparse
import json
from pathlib import Path


def _norm(s):
    return (s or "").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--sample", type=int, default=3,
                    help="print this many example records")
    args = ap.parse_args()

    merged_dir = args.root / "merged"
    conv_dir = args.root / "converted"

    total = 0
    dep_desc_present = 0
    primary_is_deposit_abstract = 0
    has_secondary_other = 0
    dep_funding_present = 0
    funding_matches_deposit = 0
    dep_creators_present = 0
    creators_match_deposit_order = 0
    samples = []

    for src in ("zenodo", "figshare"):
        md = merged_dir / src
        if not md.exists():
            continue
        for f in sorted(md.glob("*_complete.json")):
            rid = f.stem.replace("_complete", "")
            cf = conv_dir / src / f"{rid}.json"
            if not cf.exists():
                continue
            try:
                m = json.loads(f.read_text(encoding="utf-8"))
                c = json.loads(cf.read_text(encoding="utf-8"))
            except Exception:
                continue
            total += 1

            mdesc = m.get("descriptions") or []
            cdesc = c.get("descriptions") or []
            if cdesc:
                dep_desc_present += 1
                if (mdesc
                        and _norm(mdesc[0].get("description")) == _norm(cdesc[0].get("description"))
                        and mdesc[0].get("descriptionType") == "Abstract"):
                    primary_is_deposit_abstract += 1
                if any(d.get("descriptionType") == "Other" for d in mdesc):
                    has_secondary_other += 1

            mfund = m.get("fundingReferences") or []
            cfund = c.get("fundingReferences") or []
            if cfund:
                dep_funding_present += 1
                mnames = sorted(_norm(x.get("funderName")).lower() for x in mfund if isinstance(x, dict))
                cnames = sorted(_norm(x.get("funderName")).lower() for x in cfund if isinstance(x, dict))
                if mnames == cnames:
                    funding_matches_deposit += 1

            mcre = m.get("creators") or []
            ccre = c.get("creators") or []
            if ccre:
                dep_creators_present += 1
                mnames = [_norm(x.get("name")) for x in mcre if isinstance(x, dict)]
                cnames = [_norm(x.get("name")) for x in ccre if isinstance(x, dict)]
                if mnames == cnames:
                    creators_match_deposit_order += 1

            if len(samples) < args.sample:
                samples.append((
                    rid,
                    [d.get("descriptionType") for d in mdesc],
                    [x.get("name") for x in mcre if isinstance(x, dict)][:3],
                ))

    print(f"records_with_deposit_metadata:   {total}")
    print(f"  deposit_description_present:    {dep_desc_present}")
    print(f"  primary_is_deposit_abstract:    {primary_is_deposit_abstract}")
    print(f"  has_secondary_llm_other:        {has_secondary_other}")
    print(f"  deposit_funding_present:        {dep_funding_present}")
    print(f"  funding_matches_deposit:        {funding_matches_deposit}")
    print(f"  deposit_creators_present:       {dep_creators_present}")
    print(f"  creators_match_deposit_order:   {creators_match_deposit_order}")
    print("samples:")
    for rid, dt, cr in samples:
        print(f"  {rid}: descTypes={dt} creators={cr}")


if __name__ == "__main__":
    main()
