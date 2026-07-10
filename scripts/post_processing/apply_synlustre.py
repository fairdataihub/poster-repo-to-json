#!/usr/bin/env python3
"""Apply a synonym-lustre map (variant -> canonical, from build_synlustre.py) to a corpus
field in place, canonicalizing redundant variant names. For list fields (funder /
affiliation / subject) the map can collapse two entries to the same value, so duplicates
are removed after mapping. Idempotent.

Usage:
    python apply_synlustre.py --field publisher --synlustre <map.pickle> \
        --merged-dir <m> --dry-run [--show N]
"""
import argparse
import json
import logging
import pickle
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def _canon(name, syn):
    return syn.get(name.strip(), name) if isinstance(name, str) else name


def apply_record(d, field, syn, samples, show):
    changed = False
    if field == "publisher":
        p = d.get("publisher")
        n = p.get("name") if isinstance(p, dict) else p
        if isinstance(n, str) and n.strip() in syn:
            rep = syn[n.strip()]
            if show is not None and len(samples) < show:
                samples.append(f"{n[:30]!r} -> {rep[:40]!r}")
            d["publisher"] = {"name": rep}
            changed = True
    elif field == "funder":
        arr = d.get("fundingReferences")
        if isinstance(arr, list):
            seen, kept = set(), []
            for fr in arr:
                if isinstance(fr, dict) and isinstance(fr.get("funderName"), str):
                    rep = _canon(fr["funderName"], syn)
                    if rep != fr["funderName"]:
                        fr["funderName"] = rep
                        changed = True
                    key = (rep, fr.get("awardNumber"))
                    if key in seen:               # collapsed duplicate
                        changed = True
                        continue
                    seen.add(key)
                kept.append(fr)
            if kept != arr:
                d["fundingReferences"] = kept
    elif field == "affiliation":
        for cr in d.get("creators") or []:
            if not isinstance(cr, dict) or not isinstance(cr.get("affiliation"), list):
                continue
            seen, kept = set(), []
            for a in cr["affiliation"]:
                if isinstance(a, dict) and isinstance(a.get("name"), str):
                    rep = _canon(a["name"], syn)
                    if rep != a["name"]:
                        a["name"] = rep
                        changed = True
                    key = (rep, a.get("affiliationIdentifier"))
                    if key in seen:
                        changed = True
                        continue
                    seen.add(key)
                kept.append(a)
            if kept != cr["affiliation"]:
                cr["affiliation"] = kept
    elif field == "subject":
        arr = d.get("subjects")
        if isinstance(arr, list):
            seen, kept = set(), []
            for s in arr:
                n = s.get("subject") if isinstance(s, dict) else s
                if isinstance(n, str):
                    rep = _canon(n, syn)
                    if rep != n:
                        if isinstance(s, dict):
                            s = {**s, "subject": rep}
                        else:
                            s = rep
                        changed = True
                    key = rep.lower()
                    if key in seen:
                        changed = True
                        continue
                    seen.add(key)
                kept.append(s)
            if kept != arr:
                d["subjects"] = kept
    return changed


def run(merged_dir, field, syn, dry_run, show):
    stats = {"scanned": 0, "changed": 0, "errors": 0}
    samples = []
    for f in sorted(Path(merged_dir).rglob("*.json")):
        stats["scanned"] += 1
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            stats["errors"] += 1
            continue
        if not isinstance(d, dict):
            continue
        if apply_record(d, field, syn, samples, show):
            stats["changed"] += 1
            if not dry_run:
                try:
                    f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
                except Exception as e:
                    logger.error(f"  write {f.name}: {e}")
                    stats["errors"] += 1
    return stats, samples


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--field", required=True, choices=["publisher", "funder", "affiliation", "subject"])
    ap.add_argument("--synlustre", required=True)
    ap.add_argument("--merged-dir", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--show", type=int, default=0)
    args = ap.parse_args()
    syn = pickle.load(open(args.synlustre, "rb"))
    logger.info(f"[{'DRY-RUN' if args.dry_run else 'LIVE'}] apply {args.field} synlustre ({len(syn)} remaps)  merged={args.merged_dir}")
    stats, samples = run(args.merged_dir, args.field, syn, args.dry_run, args.show or None)
    for s in samples:
        logger.info(f"  e.g. {s}")
    for k in ("scanned", "changed", "errors"):
        logger.info(f"  {k:10s} {stats[k]}")


if __name__ == "__main__":
    main()
