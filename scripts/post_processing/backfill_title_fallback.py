#!/usr/bin/env python3
"""Backfill: replace clearly-bad poster2json (LLM) titles with the deposit title.

titles[] is LLM-only; the LLM sometimes emitted a section-header fragment ("Aim",
"SIP") or an abstract paragraph as the title. Where the merged record's titles[0]
is clearly bad and the raw deposit title (Zenodo metadata.title / Figshare title)
is reasonable, fall back to the deposit title. Precision-first: good LLM titles and
bad/missing deposit titles are left untouched. Idempotent.

Usage:
    python backfill_title_fallback.py --merged-dir <m> --metadata-dir <meta> --dry-run [--show N] [--limit N]
"""
import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

_SRC = Path(__file__).resolve().parents[2] / "src"
if _SRC.exists():
    sys.path.insert(0, str(_SRC))
from poster_to_json.field_normalize import replace_bad_llm_title  # noqa: E402


def _deposit_title(src, raw):
    if src == "zenodo":
        return (raw.get("metadata") or {}).get("title", "") or ""
    return raw.get("title", "") or ""


def run(merged_dir, metadata_dir, dry_run, limit, show):
    stats = {"scanned": 0, "replaced": 0, "no_raw": 0, "errors": 0}
    samples = []
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
            rp = Path(metadata_dir) / src / f"{rid}.json"
            if not rp.exists():
                stats["no_raw"] += 1
                continue
            try:
                raw = json.loads(rp.read_text(encoding="utf-8"))
            except Exception:
                stats["errors"] += 1
                continue
            old = (d.get("titles") or [{}])[0]
            old_t = old.get("title") if isinstance(old, dict) else old
            if replace_bad_llm_title(d, _deposit_title(src, raw)):
                stats["replaced"] += 1
                if show and len(samples) < show:
                    new_t = d["titles"][0]["title"]
                    samples.append(f"{rid}: {str(old_t)[:55]!r} -> {new_t[:55]!r}")
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
    ap.add_argument("--metadata-dir", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--show", type=int, default=0)
    args = ap.parse_args()
    mode = "DRY-RUN" if args.dry_run else "LIVE"
    logger.info(f"[{mode}] title fallback  merged={args.merged_dir}")
    stats, samples = run(args.merged_dir, args.metadata_dir, args.dry_run, args.limit, args.show)
    for s in samples:
        logger.info(f"  e.g. {s}")
    for k in ("scanned", "replaced", "no_raw", "errors"):
        logger.info(f"  {k:10s} {stats[k]}")


if __name__ == "__main__":
    main()
