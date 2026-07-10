#!/usr/bin/env python3
"""Validate the synlustre clustering (build_synlustre.py) against a hand-labelled gold set,
using V-measure to pick the best HDBSCAN cluster_selection_epsilon for a field.

We embed the gold surface variants with the SAME recipe build_synlustre uses (gte-large,
L2-normalized, euclidean HDBSCAN, min_cluster_size=2 / min_samples=1 / leaf) and reuse its
_embed and _holdout directly, so the sweep measures the real pipeline -- not a proxy. For a
grid of epsilon values we score the predicted clusters against the gold canonical labels with
sklearn's homogeneity_completeness_v_measure and report the eps that maximizes V-measure.

Gold format -- a TSV (default) or JSON mapping each surface variant to its true canonical:

    TSV   (two columns, header optional; a line whose 2nd col is "true_canonical" is skipped)
        surface_variant<TAB>true_canonical
        Univ. of Washington<TAB>University of Washington
        University of Washington<TAB>University of Washington
        Washington University in St. Louis<TAB>Washington University
        Wash U<TAB>Washington University

    JSON  {"Univ. of Washington": "University of Washington",
           "University of Washington": "University of Washington",
           "Washington University in St. Louis": "Washington University",
           "Wash U": "Washington University"}

Noise handling (documented, matches the shipping pipeline): build_synlustre feeds only the
non-_holdout terms to HDBSCAN and leaves everything else at label -1, where "-1 maps to
itself" (each such term is effectively its own singleton canonical). Collapsing every -1 point
into ONE cluster would be wrong -- it would falsely tell V-measure that all held-out/noise
terms are the same entity, tanking homogeneity. So before scoring we EXPLODE noise: every -1
point (held-out acronyms + HDBSCAN noise) is reassigned a unique singleton label. This is the
faithful V-measure of the pipeline's actual behaviour.

Run with the pubverse env, e.g.:
    ~/myenv/bin/python validate_vmeasure.py --field affiliation \
        --gold /storage/poster-work/gold_affiliation.tsv \
        --eps-list 0.20,0.25,0.30,0.35,0.40,0.45,0.50 \
        --emb-cache /storage/poster-work/vmeasure_affiliation.emb
"""
import argparse
import json
import os

import numpy as np

from build_synlustre import _embed, _holdout


def load_gold(path):
    """Return (variants, true_labels) from a gold TSV or JSON. Order is preserved and
    deterministic so the embedding cache key (a hash of the joined phrases) stays stable
    across runs. Canonical strings are mapped to small integer label ids for sklearn."""
    pairs = []
    if path.lower().endswith(".json"):
        d = json.load(open(path, encoding="utf-8"))
        pairs = [(str(k), str(v)) for k, v in d.items()]
    else:
        for line in open(path, encoding="utf-8"):
            line = line.rstrip("\n")
            if not line.strip():
                continue
            cols = line.split("\t")
            if len(cols) < 2:
                continue
            variant, canonical = cols[0].strip(), cols[1].strip()
            if not variant or not canonical:
                continue
            if canonical == "true_canonical":     # skip a header row
                continue
            pairs.append((variant, canonical))
    variants = [v for v, _ in pairs]
    canon2id = {}
    true_labels = []
    for _, c in pairs:
        true_labels.append(canon2id.setdefault(c, len(canon2id)))
    return variants, np.asarray(true_labels, dtype=int)


def predict_labels(emb, phrases, eps):
    """Cluster exactly as build_synlustre does: hold acronyms/short tokens OUT (they stay
    -1), run HDBSCAN(leaf, euclidean, min_cluster_size=2, min_samples=1) on the rest, scatter
    labels back. Then EXPLODE every -1 point into its own unique singleton label so noise is
    scored as self-mapping, not as one giant cluster (see module docstring)."""
    from sklearn.cluster import HDBSCAN

    labels = np.full(len(phrases), -1, dtype=int)
    clusterable = [i for i, p in enumerate(phrases) if not _holdout(p)]
    if clusterable:
        sub = HDBSCAN(min_cluster_size=2, min_samples=1, cluster_selection_epsilon=eps,
                      cluster_selection_method="leaf", metric="euclidean").fit_predict(emb[clusterable])
        labels[np.asarray(clusterable)] = sub

    # Explode noise (-1) into unique singleton labels, above any real cluster id.
    next_lbl = int(labels.max()) + 1 if labels.max() >= 0 else 0
    for i in range(len(labels)):
        if labels[i] < 0:
            labels[i] = next_lbl
            next_lbl += 1
    return labels


def sweep(variants, true_labels, eps_list, emb_cache):
    """Embed once, then score homogeneity/completeness/V-measure at each eps."""
    from sklearn.metrics import homogeneity_completeness_v_measure

    emb = _embed(variants, emb_cache)
    held = sum(1 for p in variants if _holdout(p))
    n_gold = len(set(true_labels.tolist()))
    print(f"[vmeasure] {len(variants)} gold variants across {n_gold} true canonicals; "
          f"{held} held out as acronym/short (scored as singletons)", flush=True)

    rows = []
    for eps in eps_list:
        pred = predict_labels(emb, variants, eps)
        n_pred = len(set(pred.tolist()))
        h, c, v = homogeneity_completeness_v_measure(true_labels, pred)
        rows.append((eps, h, c, v, n_pred))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gold", required=True, help="gold TSV (variant\\tcanonical) or .json map")
    ap.add_argument("--field", required=True, choices=["publisher", "funder", "affiliation", "subject"],
                    help="field name, for labelling output (gold is already field-specific)")
    ap.add_argument("--eps-list", default="0.20,0.25,0.30,0.35,0.40,0.45,0.50",
                    help="comma-separated cluster_selection_epsilon values to sweep")
    ap.add_argument("--emb-cache", required=True, help="embedding cache prefix (as in build_synlustre)")
    args = ap.parse_args()

    eps_list = [float(x) for x in args.eps_list.split(",") if x.strip()]
    variants, true_labels = load_gold(args.gold)
    rows = sweep(variants, true_labels, eps_list, args.emb_cache)

    print(f"\n[vmeasure] field={args.field}  gold={os.path.basename(args.gold)}")
    print(f"{'eps':>6}  {'homogeneity':>11}  {'completeness':>12}  {'v_measure':>9}  {'n_pred':>6}")
    print("-" * 56)
    for eps, h, c, v, n_pred in rows:
        print(f"{eps:6.3f}  {h:11.4f}  {c:12.4f}  {v:9.4f}  {n_pred:6d}")
    print("-" * 56)
    best = max(rows, key=lambda r: r[3])
    print(f"[vmeasure] BEST eps={best[0]:.3f}  V={best[3]:.4f}  "
          f"(homogeneity={best[1]:.4f}, completeness={best[2]:.4f})")


if __name__ == "__main__":
    main()
