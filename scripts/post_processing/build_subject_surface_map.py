#!/usr/bin/env python3
"""Build a CONSERVATIVE subject synonym map: surface variants only.

Subjects are a controlled-vocabulary-heavy field (ANZSRC Fields of Research, incl.
"... not elsewhere classified" labels) that must NOT be semantically merged or
rewritten. This groups subject terms only when they are identical after a surface
normalization -- lowercase, punctuation/hyphen -> space, collapse whitespace, and a
light trailing-plural stem per word -- so it folds `machine learning` / `Machine
Learning` / `machine-learning` and `genomics` / `genomic`, but never merges two
distinct concepts (e.g. `Computational Biology` stays; it does not fold into
`Bioinformatics and computational biology not elsewhere classified`). Canonical =
the most frequent actual surface form in the group.

Emits a variant->canonical pickle (apply with apply_synlustre.py --field subject)
plus a human-review TSV.

Usage (pubverse env):
    ~/myenv/bin/python build_subject_surface_map.py \
        --terms /storage/poster-work/syn_subject.pickle.terms.json \
        --out /storage/poster-work/syn_subject_surface.pickle \
        --review /storage/poster-work/syn_subject_surface_review.tsv
"""
import argparse
import glob
import json
import pickle
import re
import unicodedata
from collections import Counter, defaultdict


def surface_key(s):
    # keep Unicode letters/digits (so non-Latin scripts are NOT stripped to empty);
    # fold case, turn punctuation/hyphens into spaces, collapse whitespace.
    k = unicodedata.normalize("NFKC", str(s)).lower()
    k = re.sub(r"[^\w]+", " ", k, flags=re.UNICODE).strip()
    # light plural stem: drop a trailing ASCII 's' on words longer than 3 chars
    words = [w[:-1] if len(w) > 3 and w.endswith("s") and w.isascii() else w for w in k.split()]
    return " ".join(words)


def collect_terms(terms_path, merged_glob):
    if terms_path:
        return Counter(json.load(open(terms_path, encoding="utf-8")))
    c = Counter()
    for f in glob.glob(merged_glob):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        for s in d.get("subjects") or []:
            n = s.get("subject") if isinstance(s, dict) else s
            if isinstance(n, str) and n.strip():
                c[n.strip()] += 1
    return c


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--terms", help="terms.json cache (term -> freq); else scan --merged-glob")
    ap.add_argument("--merged-glob")
    ap.add_argument("--out", required=True)
    ap.add_argument("--review", required=True)
    args = ap.parse_args()

    counts = collect_terms(args.terms, args.merged_glob)
    groups = defaultdict(list)
    for term in counts:
        k = surface_key(term)
        if k:                                          # skip punctuation-only / empty keys
            groups[k].append(term)

    mapping, clusters = {}, {}
    for key, members in groups.items():
        if len(members) < 2:
            continue
        rep = max(members, key=lambda m: counts[m])   # canonical = most frequent surface form
        for m in members:
            if m != rep:
                mapping[m] = rep
        clusters[rep] = sorted(members, key=lambda m: -counts[m])

    pickle.dump(mapping, open(args.out, "wb"))
    with open(args.review, "w", encoding="utf-8") as o:
        o.write("canonical\tfreq\tmerged_variants\n")
        for rep, members in sorted(clusters.items(), key=lambda kv: -sum(counts[m] for m in kv[1])):
            variants = " | ".join(f"{m}({counts[m]})" for m in members if m != rep)
            o.write(f"{rep}\t{counts[rep]}\t{variants}\n")
    print(f"[surface] {len(mapping)} variants -> canonical across {len(clusters)} groups "
          f"(of {len(counts)} distinct subjects)")


if __name__ == "__main__":
    main()
