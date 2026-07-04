#!/usr/bin/env python3
"""
Phase-4: upgrade terse deposit creator names to the fuller extraction form.

Per decision 4 (fullest-form name wins), a terse deposit author ("Smith, J.")
should be upgraded when the poster2json extraction has a fuller form of the SAME
person ("Jane Smith"). The original union's surname-overlap guard dropped the
fuller LLM form before dedup could compare; this recovers it.

For each merged creator, find an extraction creator that same_author-matches and
carries MORE full (2+ letter) name tokens; if so, rebuild the name via _merge_name
(Family, Given) and re-derive givenName/familyName. Structured ORCID/affiliation
are untouched.

Usage:
    python backfill_fullest_names.py --merged-dir <m> --extraction-dir <e> --dry-run [--show N]
"""
import argparse
import json
import logging
import re
import sys
from pathlib import Path

# A valid single-author upgrade target: no multi-author delimiters, at most one comma.
_MULTI = re.compile(r"&|\band\b|;|/|\bet\s*al\b|\+", re.I)


def _single_author(name):
    n = str(name)
    return not _MULTI.search(n) and n.count(",") <= 1

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

_SRC = Path(__file__).resolve().parents[2] / "src"
if _SRC.exists():
    sys.path.insert(0, str(_SRC))
from poster_to_json.field_normalize import (  # noqa: E402
    same_author, _merge_name, _full_tokens, _name_is_lumped,
)


def build_idx(ext_dir):
    idx = {}
    for f in Path(ext_dir).glob("*_extracted.json"):
        parts = f.stem.replace("_extracted", "").split("_")
        if len(parts) >= 2 and parts[0] in ("zenodo", "figshare"):
            idx[(parts[0], parts[1])] = f
    return idx


def run(merged_dir, ext_dir, dry_run, limit, show):
    idx = build_idx(ext_dir)
    stats = {"scanned": 0, "records_changed": 0, "names_upgraded": 0, "no_ext": 0, "errors": 0}
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
            mcres = d.get("creators") or []
            if not mcres:
                continue
            rid = f.stem.replace("_complete", "")
            ef = idx.get((src, rid))
            if not ef:
                stats["no_ext"] += 1
                continue
            try:
                ext = json.loads(ef.read_text(encoding="utf-8"))
            except Exception:
                stats["errors"] += 1
                continue
            ecres = [c.get("name", "") for c in (ext.get("creators") or [])
                     if isinstance(c, dict) and c.get("name")
                     and not _name_is_lumped(c.get("name")) and _single_author(c.get("name"))]
            changed = False
            for mc in mcres:
                if not isinstance(mc, dict) or not mc.get("name") or _name_is_lumped(mc["name"]):
                    continue
                mname = mc["name"]
                mfull = len(_full_tokens(mname))
                best = None
                for en in ecres:
                    if same_author(mname, en) and len(_full_tokens(en)) > mfull:
                        if not best or len(_full_tokens(en)) > len(_full_tokens(best)):
                            best = en
                if not best:
                    continue
                new_name = _merge_name([mname, best])
                if new_name == mname or len(_full_tokens(new_name)) <= mfull:
                    continue
                if show and len(samples) < show:
                    samples.append(f"{mname!r} + {best!r} -> {new_name!r}")
                mc["name"] = new_name
                if "," in new_name:
                    fam, giv = new_name.split(",", 1)
                    mc["familyName"] = fam.strip()
                    mc["givenName"] = giv.strip()
                stats["names_upgraded"] += 1
                changed = True
            if changed:
                stats["records_changed"] += 1
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
    ap.add_argument("--extraction-dir", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--show", type=int, default=0)
    args = ap.parse_args()

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    logger.info(f"[{mode}] fullest-name upgrade  merged={args.merged_dir}")
    stats, samples = run(args.merged_dir, args.extraction_dir, args.dry_run, args.limit, args.show)
    for s in samples:
        logger.info(f"  e.g. {s}")
    for k in ("scanned", "records_changed", "names_upgraded", "no_ext", "errors"):
        logger.info(f"  {k:16s} {stats[k]}")


if __name__ == "__main__":
    main()
