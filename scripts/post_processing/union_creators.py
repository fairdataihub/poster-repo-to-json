#!/usr/bin/env python3
"""
Union clean extraction authors into merged creators across an existing corpus.

Creators stay deposit-authoritative (names/order/identifiers), but the deposit
author list is sometimes incomplete. This appends authors that poster2json
extracted (clean, non-junk, non-lumped) and that aren't already present,
deduped by an order-insensitive name key. Existing creators and their resolved
ORCID/ROR are preserved.

Usage:
    python union_creators.py --merged-dir <m> --extraction-dir <e> --dry-run [--show N]
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

from poster_to_json.field_normalize import creator_addable_to_union  # noqa: E402


def _name_key(name):
    cleaned = (name or "").lower().replace(",", " ").replace(".", " ")
    return " ".join(sorted(t for t in cleaned.split() if t))


def build_ext_index(ext_dir):
    idx = {}
    for f in Path(ext_dir).glob("*_extracted.json"):
        stem = f.stem.replace("_extracted", "")
        parts = stem.split("_")
        if len(parts) >= 2 and parts[0] in ("zenodo", "figshare"):
            idx[(parts[0], parts[1])] = f
    return idx


def run(merged_dir, ext_dir, dry_run, limit, show):
    idx = build_ext_index(ext_dir)
    logger.info(f"indexed {len(idx)} extractions")
    stats = {"scanned": 0, "changed": 0, "added": 0, "no_extraction": 0, "errors": 0}
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
                merged = json.loads(f.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error(f"  read {f.name}: {e}")
                stats["errors"] += 1
                continue
            if not isinstance(merged, dict):
                continue
            mcre = merged.get("creators") or []
            rid = f.stem.replace("_complete", "")
            ef = idx.get((src, rid))
            if not ef:
                stats["no_extraction"] += 1
                continue
            try:
                ext = json.loads(ef.read_text(encoding="utf-8"))
            except Exception:
                stats["errors"] += 1
                continue
            ecre = (ext.get("creators") if isinstance(ext, dict) else None) or []
            mkeys = {_name_key(c.get("name", "")) for c in mcre
                     if isinstance(c, dict) and c.get("name")}
            added = []
            for ec in ecre:
                if not creator_addable_to_union(ec):
                    continue
                k = _name_key(ec.get("name", ""))
                if k and k not in mkeys:
                    mkeys.add(k)
                    added.append(ec)
            if added:
                merged["creators"] = mcre + added
                stats["changed"] += 1
                stats["added"] += len(added)
                if show and len(samples) < show:
                    samples.append(f"{rid}: +{len(added)} {[a.get('name') for a in added][:3]}")
                if not dry_run:
                    try:
                        f.write_text(json.dumps(merged, indent=2, ensure_ascii=False),
                                     encoding="utf-8")
                    except Exception as e:
                        logger.error(f"  write {rid}: {e}")
                        stats["errors"] += 1
    return stats, samples


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merged-dir", required=True)
    ap.add_argument("--extraction-dir", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--show", type=int, default=0)
    args = ap.parse_args()

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    logger.info(f"[{mode}] union creators  merged={args.merged_dir}")
    stats, samples = run(args.merged_dir, args.extraction_dir, args.dry_run,
                         args.limit, args.show)
    for s in samples:
        logger.info(f"  e.g. {s}")
    logger.info("Done:")
    for k in ("scanned", "changed", "added", "no_extraction", "errors"):
        logger.info(f"  {k:14s} {stats[k]}")


if __name__ == "__main__":
    main()
