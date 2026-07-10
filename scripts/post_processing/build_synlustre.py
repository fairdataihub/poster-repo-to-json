#!/usr/bin/env python3
"""Build a synonym-lustre (variant -> canonical) map for a corpus field, mirroring the
pubverse_brett cd_tf_idf method: embed the distinct terms (gte-large), cluster them with
HDBSCAN, and map every member of a cluster to the term closest to the cluster centroid
(frequency breaks ties). Emits the map as a pickle + a human-review TSV of the merges.

Fields: publisher | funder | affiliation | subject  (extraction differs per field).

Run with the pubverse env, e.g.:
    ~/myenv/bin/python build_synlustre.py --field publisher \
        --merged-glob '/storage/poster-work/*/merged/*/*.json' \
        --out /storage/poster-work/synlustre_publisher.pickle \
        --review /storage/poster-work/synlustre_publisher_review.tsv
"""
import argparse
import glob
import json
import pickle
from collections import Counter

import numpy as np


def collect_terms(merged_glob, field):
    """Counter of distinct term -> corpus frequency for the chosen field."""
    c = Counter()
    for f in glob.glob(merged_glob):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        if field == "publisher":
            p = d.get("publisher")
            n = p.get("name") if isinstance(p, dict) else p
            if isinstance(n, str) and n.strip():
                c[n.strip()] += 1
        elif field == "funder":
            for fr in d.get("fundingReferences") or []:
                if isinstance(fr, dict) and isinstance(fr.get("funderName"), str) and fr["funderName"].strip():
                    c[fr["funderName"].strip()] += 1
        elif field == "affiliation":
            for cr in d.get("creators") or []:
                for a in (cr.get("affiliation") or []) if isinstance(cr, dict) else []:
                    n = a.get("name") if isinstance(a, dict) else a
                    if isinstance(n, str) and n.strip():
                        c[n.strip()] += 1
        elif field == "subject":
            for s in d.get("subjects") or []:
                n = s.get("subject") if isinstance(s, dict) else s
                if isinstance(n, str) and n.strip():
                    c[n.strip()] += 1
    return c


def build_synlustre(counts, eps):
    from sentence_transformers import SentenceTransformer
    from sklearn.cluster import HDBSCAN
    from scipy.spatial.distance import cdist

    phrases = list(counts.keys())
    print(f"[synlustre] {len(phrases)} distinct terms; embedding with gte-large ...", flush=True)
    model = SentenceTransformer("Alibaba-NLP/gte-large-en-v1.5", trust_remote_code=True)
    emb = model.encode(phrases, convert_to_numpy=True, normalize_embeddings=True,
                       show_progress_bar=True, batch_size=256)
    print("[synlustre] clustering (HDBSCAN) ...", flush=True)
    labels = HDBSCAN(min_cluster_size=2, min_samples=1, cluster_selection_epsilon=eps,
                     cluster_selection_method="leaf", metric="euclidean").fit_predict(emb)
    synlustre, clusters = {}, {}
    for lbl in sorted(set(labels)):
        if lbl < 0:
            continue                                  # noise -> maps to itself (omitted)
        idxs = np.where(labels == lbl)[0]
        cemb = emb[idxs]
        centroid = cemb.mean(axis=0)
        dists = cdist([centroid], cemb, "euclidean")[0]
        closest = np.where(dists == dists.min())[0]
        if len(closest) > 1:
            rep_i = closest[int(np.argmax([counts[phrases[idxs[i]]] for i in closest]))]
        else:
            rep_i = closest[0]
        rep = phrases[idxs[rep_i]]
        members = [phrases[i] for i in idxs]
        for m in members:
            if m != rep:
                synlustre[m] = rep                    # only store real remaps
        if len(members) > 1:
            clusters[rep] = sorted(members, key=lambda m: -counts[m])
    return synlustre, clusters


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--field", required=True, choices=["publisher", "funder", "affiliation", "subject"])
    ap.add_argument("--merged-glob", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--review", required=True)
    ap.add_argument("--eps", type=float, default=0.35)
    args = ap.parse_args()

    counts = collect_terms(args.merged_glob, args.field)
    synlustre, clusters = build_synlustre(counts, args.eps)
    pickle.dump(synlustre, open(args.out, "wb"))
    with open(args.review, "w", encoding="utf-8") as o:
        o.write("representative\tfreq\tmerged_variants\n")
        for rep, members in sorted(clusters.items(), key=lambda kv: -sum(counts[m] for m in kv[1])):
            variants = " | ".join(f"{m}({counts[m]})" for m in members if m != rep)
            o.write(f"{rep}\t{counts[rep]}\t{variants}\n")
    print(f"[synlustre] {len(synlustre)} variants -> canonical across {len(clusters)} clusters")
    print(f"[synlustre] map: {args.out}  review: {args.review}")


if __name__ == "__main__":
    main()
