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


def _embed(phrases, cache):
    import hashlib
    key = hashlib.sha1(("\n".join(phrases)).encode("utf-8")).hexdigest()[:16]
    cpath = f"{cache}.{key}.npy"
    import os
    if os.path.exists(cpath):
        print(f"[synlustre] loading cached embeddings {cpath}", flush=True)
        return np.load(cpath)
    from sentence_transformers import SentenceTransformer
    print(f"[synlustre] {len(phrases)} distinct terms; embedding with gte-large ...", flush=True)
    model = SentenceTransformer("Alibaba-NLP/gte-large-en-v1.5", trust_remote_code=True)
    emb = model.encode(phrases, convert_to_numpy=True, normalize_embeddings=True,
                       show_progress_bar=True, batch_size=256)
    np.save(cpath, emb)
    return emb


def build_synlustre(counts, eps, cache):
    from sklearn.cluster import HDBSCAN
    from scipy.spatial.distance import cdist

    phrases = list(counts.keys())
    emb = _embed(phrases, cache)
    cemb_all = emb
    if len(phrases) > 20000:                          # PCA-reduce for scale (as pubverse does)
        from sklearn.decomposition import PCA
        ncomp = min(50, emb.shape[1])
        print(f"[synlustre] PCA {emb.shape} -> {ncomp} dims for {len(phrases)} terms ...", flush=True)
        cemb_all = PCA(n_components=ncomp, random_state=0).fit_transform(emb).astype("float32")
        cemb_all /= np.clip(np.linalg.norm(cemb_all, axis=1, keepdims=True), 1e-9, None)
    print(f"[synlustre] clustering (HDBSCAN eps={eps}) ...", flush=True)
    labels = HDBSCAN(min_cluster_size=2, min_samples=1, cluster_selection_epsilon=eps,
                     cluster_selection_method="leaf", metric="euclidean").fit_predict(cemb_all)
    synlustre, clusters = {}, {}
    for lbl in sorted(set(labels)):
        if lbl < 0:
            continue                                  # noise -> maps to itself (omitted)
        idxs = np.where(labels == lbl)[0]
        members = [phrases[i] for i in idxs]
        # canonical = the MOST FREQUENT variant (clean chart label); centroid-closest
        # only breaks ties among equally-frequent variants.
        cemb = cemb_all[idxs]
        dists = cdist([cemb.mean(axis=0)], cemb, "euclidean")[0]
        rep_i = max(range(len(idxs)), key=lambda i: (counts[members[i]], -dists[i]))
        rep = members[rep_i]
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
    synlustre, clusters = build_synlustre(counts, args.eps, args.out + ".emb")
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
