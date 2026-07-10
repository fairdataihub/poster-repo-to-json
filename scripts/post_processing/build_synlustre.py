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


def collect_terms(merged_glob, field, cache_path=None):
    """Counter of distinct term -> corpus frequency for the chosen field. Cached to JSON
    so repeated eps runs don't re-scan the corpus (the slow part)."""
    import os
    if cache_path and os.path.exists(cache_path):
        print(f"[synlustre] loading cached terms {cache_path}", flush=True)
        return Counter(json.load(open(cache_path, encoding="utf-8")))
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
    if cache_path:
        json.dump(dict(c), open(cache_path, "w", encoding="utf-8"), ensure_ascii=False)
    return c


import re as _re
import unicodedata as _ud


def _norm_ror(s):
    s = _ud.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    s = _re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return _re.sub(r"\s+", " ", s).strip()


def _split_by_ror(idxs, phrases, emb, ror_index):
    """Split a semantic cluster into per-ROR sub-clusters. A cluster spanning >=2 distinct
    RORs (e.g. University of Washington vs Washington University) is partitioned by ROR;
    ROR-less members are assigned to the nearest ROR sub-group centroid (by embedding).
    Clusters with <=1 distinct ROR are returned intact."""
    n2r = ror_index["name2ror"]
    rors = [n2r.get(_norm_ror(phrases[i])) for i in idxs]
    if len({r for r in rors if r}) <= 1:
        return [idxs]
    groups = {}
    for k, i in enumerate(idxs):
        if rors[k]:
            groups.setdefault(rors[k], []).append(i)
    cents = {r: emb[g].mean(0) for r, g in groups.items()}
    for k, i in enumerate(idxs):
        if not rors[k]:
            best = min(cents, key=lambda rr: float(((emb[i] - cents[rr]) ** 2).sum()))
            groups[best].append(i)
    return list(groups.values())


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


def build_synlustre(counts, eps, cache, pca_dims=0, ror_index=None):
    from sklearn.cluster import HDBSCAN
    from scipy.spatial.distance import cdist

    phrases = list(counts.keys())
    emb = _embed(phrases, cache)
    cemb_all = emb
    # PCA is OFF by default -- it costs granularity (the fine distinctions that keep
    # similar-but-distinct entities apart). Use it ONLY when full-dim HDBSCAN is
    # infeasible (very large N), and then keep MANY dims to preserve granularity.
    if pca_dims and pca_dims < emb.shape[1]:
        from sklearn.decomposition import PCA
        print(f"[synlustre] PCA {emb.shape} -> {pca_dims} dims (opt-in) ...", flush=True)
        cemb_all = PCA(n_components=pca_dims, random_state=0).fit_transform(emb).astype("float32")
        cemb_all /= np.clip(np.linalg.norm(cemb_all, axis=1, keepdims=True), 1e-9, None)
    print(f"[synlustre] clustering (HDBSCAN eps={eps}) ...", flush=True)
    labels = HDBSCAN(min_cluster_size=2, min_samples=1, cluster_selection_epsilon=eps,
                     cluster_selection_method="leaf", metric="euclidean").fit_predict(cemb_all)
    synlustre, clusters = {}, {}
    split_count = [0]

    def _emit(idxs):
        members = [phrases[i] for i in idxs]
        # canonical = the MOST FREQUENT variant (clean chart label); centroid-closest
        # only breaks ties among equally-frequent variants.
        sub = cemb_all[idxs]
        dists = cdist([sub.mean(axis=0)], sub, "euclidean")[0]
        rep_i = max(range(len(idxs)), key=lambda i: (counts[members[i]], -dists[i]))
        rep = members[rep_i]
        for m in members:
            if m != rep:
                synlustre[m] = rep                    # only store real remaps
        if len(members) > 1:
            clusters[rep] = sorted(members, key=lambda m: -counts[m])

    for lbl in sorted(set(labels)):
        if lbl < 0:
            continue                                  # noise -> maps to itself (omitted)
        idxs = list(np.where(labels == lbl)[0])
        if ror_index and len(idxs) > 1:               # split clusters that span >=2 RORs
            parts = _split_by_ror(idxs, phrases, cemb_all, ror_index)
            if len(parts) > 1:
                split_count[0] += 1
            for part in parts:
                _emit(part)
        else:
            _emit(idxs)
    if ror_index:
        print(f"[synlustre] ROR split {split_count[0]} multi-institution clusters", flush=True)
    return synlustre, clusters


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--field", required=True, choices=["publisher", "funder", "affiliation", "subject"])
    ap.add_argument("--merged-glob", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--review", required=True)
    ap.add_argument("--eps", type=float, default=0.35)
    ap.add_argument("--pca-dims", type=int, default=0,
                    help="0 = no PCA (full-dim, most granular). Only set for very large fields.")
    ap.add_argument("--ror-index", default=None,
                    help="pickle from build_ror_index.py; splits clusters spanning >=2 RORs.")
    args = ap.parse_args()

    ror_index = pickle.load(open(args.ror_index, "rb")) if args.ror_index else None
    counts = collect_terms(args.merged_glob, args.field, args.out + ".terms.json")
    synlustre, clusters = build_synlustre(counts, args.eps, args.out + ".emb", args.pca_dims, ror_index)
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
