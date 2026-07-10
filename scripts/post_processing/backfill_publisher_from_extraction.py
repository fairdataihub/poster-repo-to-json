#!/usr/bin/env python3
"""Backfill: re-source `publisher` from the poster2json extraction (normalized), with a
Zenodo/Figshare fallback.

Reverts the earlier repository-only collapse (normalize_publisher used to overwrite the
extracted publisher with the bare repository), restoring the original poster publishers
(EPA, arXiv, PosterPresentations, universities, ...) cleaned with the same rigor as the
other fields. A poster with no usable extracted publisher falls back to its repository.
Idempotent.

Usage:
    python backfill_publisher_from_extraction.py --merged-dir <m> --extractions-dir <x> --dry-run [--show N] [--limit N]
"""
import argparse
import glob
import json
import logging
import re
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

_SRC = Path(__file__).resolve().parents[2] / "src"
if _SRC.exists():
    sys.path.insert(0, str(_SRC))
from poster_to_json.field_normalize import _clean_publisher  # noqa: E402


def build_ext_index(extractions_dir):
    """(src, id) -> extraction file path, parsed from the flat '{src}_{id}_...json' names."""
    idx = {}
    for f in glob.glob(str(Path(extractions_dir) / "*.json")):
        m = re.match(r"(zenodo|figshare)_(\d+)_", Path(f).name)
        if m:
            idx.setdefault((m.group(1), m.group(2)), f)
    return idx


def _ext_publisher(path):
    try:
        e = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None
    p = e.get("publisher")
    return p.get("name") if isinstance(p, dict) else p


def run(merged_dir, extractions_dir, dry_run, limit, show):
    stats = {"scanned": 0, "changed": 0, "from_extraction": 0, "fallback": 0,
             "no_ext_file": 0, "errors": 0}
    samples = []
    idx = build_ext_index(extractions_dir)
    for src in ("zenodo", "figshare"):
        md = Path(merged_dir) / src
        if not md.exists():
            continue
        for f in sorted(md.glob("*_complete.json")):
            if limit and stats["scanned"] >= limit:
                break
            stats["scanned"] += 1
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                stats["errors"] += 1
                continue
            rid = f.stem.replace("_complete", "")
            xf = idx.get((src, rid))
            if not xf:
                stats["no_ext_file"] += 1
            name = _clean_publisher(_ext_publisher(xf) if xf else None)
            if name:
                stats["from_extraction"] += 1
            else:
                name = "Zenodo" if src == "zenodo" else "Figshare"
                stats["fallback"] += 1
            want = {"name": name}
            if d.get("publisher") != want:
                old = d.get("publisher")
                old = old.get("name") if isinstance(old, dict) else old
                d["publisher"] = want
                stats["changed"] += 1
                if show and len(samples) < show and name not in ("Zenodo", "Figshare"):
                    samples.append(f"{rid}: {str(old)[:18]!r} -> {name[:48]!r}")
                if not dry_run:
                    try:
                        f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
                    except Exception as e:
                        logger.error(f"  write {rid}: {e}")
                        stats["errors"] += 1
    return stats, samples


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merged-dir", required=True)
    ap.add_argument("--extractions-dir", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--show", type=int, default=0)
    args = ap.parse_args()
    logger.info(f"[{'DRY-RUN' if args.dry_run else 'LIVE'}] publisher from extraction  merged={args.merged_dir}")
    stats, samples = run(args.merged_dir, args.extractions_dir, args.dry_run, args.limit, args.show)
    for s in samples:
        logger.info(f"  e.g. {s}")
    for k in ("scanned", "changed", "from_extraction", "fallback", "no_ext_file", "errors"):
        logger.info(f"  {k:16s} {stats[k]}")


if __name__ == "__main__":
    main()
