#!/usr/bin/env python3
"""
Title QC audit: flag records where the poster2json (LLM) title and the deposit
title diverge, for manual review. Does NOT modify anything — the LLM title stays
authoritative (SCHEMA_ALIGNMENT_PLAN.md D/titles). Output is a TSV report.

Divergence = low token-overlap (Jaccard) between the merged title and the deposit
title (Zenodo metadata.title / Figshare title), both non-trivial.

Usage:
    python title_qc.py --merged-dir <m> --metadata-dir <meta> --out <report.tsv> [--threshold 0.5]
"""
import argparse
import json
import logging
import re
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def _title(d):
    ts = d.get("titles") or []
    if not ts:
        return ""
    t0 = ts[0]
    return (t0.get("title", "") if isinstance(t0, dict) else str(t0)) or ""


def _words(t):
    return set(w for w in re.sub(r"[^a-z0-9 ]", " ", str(t).lower()).split() if len(w) > 1)


def _deposit_title(src, raw):
    if src == "zenodo":
        return (raw.get("metadata") or {}).get("title", "") or ""
    return raw.get("title", "") or ""


def run(merged_dir, metadata_dir, out, threshold):
    rows = []
    stats = {"scanned": 0, "compared": 0, "divergent": 0, "no_deposit": 0}
    for src in ("zenodo", "figshare"):
        md = Path(merged_dir) / src
        if not md.exists():
            continue
        for f in sorted(md.glob("*_complete.json")):
            stats["scanned"] += 1
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            rid = f.stem.replace("_complete", "")
            mp = Path(metadata_dir) / src / f"{rid}.json"
            if not mp.exists():
                continue
            try:
                raw = json.loads(mp.read_text(encoding="utf-8"))
            except Exception:
                continue
            mt = _title(d)
            dt = _deposit_title(src, raw)
            mw, dw = _words(mt), _words(dt)
            if len(mw) < 3 or len(dw) < 3:
                stats["no_deposit"] += 1
                continue
            stats["compared"] += 1
            jac = len(mw & dw) / len(mw | dw)
            if jac < threshold:
                stats["divergent"] += 1
                rows.append((rid, src, f"{jac:.2f}", mt.replace("\t", " ")[:120],
                             dt.replace("\t", " ")[:120]))
    rows.sort(key=lambda r: float(r[2]))
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("id\tsource\tsimilarity\tllm_title\tdeposit_title\n")
        for r in rows:
            fh.write("\t".join(r) + "\n")
    for k in ("scanned", "compared", "divergent", "no_deposit"):
        logger.info(f"  {k:10s} {stats[k]}")
    logger.info(f"  report -> {out}")
    return stats


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merged-dir", required=True)
    ap.add_argument("--metadata-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--threshold", type=float, default=0.5)
    args = ap.parse_args()
    logger.info(f"title QC  merged={args.merged_dir}  threshold={args.threshold}")
    run(args.merged_dir, args.metadata_dir, args.out, args.threshold)


if __name__ == "__main__":
    main()
