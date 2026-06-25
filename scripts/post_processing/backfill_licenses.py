#!/usr/bin/env python3
"""
Re-derive each merged record's rightsList from the repository deposit, removing
LLM-fabricated licenses, WITHOUT re-running poster2json.

Background: poster2json before 0.9.3 let the model emit a rightsList guessed
from the poster text (funding sentences, acknowledgements, citations,
disclaimers, contacts misfiled as "rights"). When the deposit declared no
license, the merger kept that fabricated value. This restores the rule that a
poster's license comes ONLY from the deposit: for each merged record set
rightsList to the deposit's license (normalized), or remove it entirely if the
deposit declares none.

Deposit license source, in priority order:
  1. raw deposit metadata (--metadata-dir/<src>/<rec_id>.json) re-converted with
     the current SchemaConverter (handles legacy metadata.license AND InvenioRDM
     metadata.rights, plus id normalization). This is the most complete source.
  2. fallback: the already-converted file (<root>/converted/<src>/<rec_id>.json),
     with its rightsList ids re-normalized.
If neither source exists for a record, its rightsList is left UNCHANGED and the
record is counted "indeterminate" (we will not strip a license we cannot verify).

After running this, re-run enforce_license_policy.py over the same corpus so any
record that ends up with no/blocked license has its extracted content stripped.

Usage:
    python backfill_licenses.py --root /home/james/corpus_output \
        --metadata-dir /home/james/metadata --dry-run
    python backfill_licenses.py --root /home/james/corpus_output \
        --metadata-dir /home/james/metadata
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

from poster_to_json.schema_converter import SchemaConverter, _normalize_license_id  # noqa: E402

_converter = SchemaConverter()


def _renormalize(rights_list):
    """Re-normalize the ids of an existing converted rightsList."""
    out = []
    for e in rights_list or []:
        if not isinstance(e, dict):
            continue
        lid = _normalize_license_id(e.get("rightsIdentifier") or e.get("rights"))
        if lid:
            ne = dict(e)
            ne["rights"] = lid
            ne["rightsIdentifier"] = lid
            out.append(ne)
    return out


def deposit_rights(src, rid, conv_dir, meta_dir):
    """Return (rights_list, source) where source is 'raw'/'converted'/None.

    rights_list is [] when the deposit definitively declares no license, and
    None when no deposit source could be found (indeterminate).
    """
    if meta_dir:
        raw_path = meta_dir / src / f"{rid}.json"
        if raw_path.exists():
            try:
                raw = json.loads(raw_path.read_text(encoding="utf-8"))
                conv = _converter.convert(raw, source=src)
                return (conv.get("rightsList") or []), "raw"
            except Exception as e:
                logger.error(f"  reconvert error {src}/{rid}: {e}")

    conv_path = conv_dir / src / f"{rid}.json"
    if conv_path.exists():
        try:
            conv = json.loads(conv_path.read_text(encoding="utf-8"))
            return _renormalize(conv.get("rightsList")), "converted"
        except Exception as e:
            logger.error(f"  converted read error {src}/{rid}: {e}")

    return None, None


def _compact(rights):
    if not rights:
        return "(none)"
    return ", ".join(
        (e.get("rightsIdentifier") or e.get("rights") or "?") if isinstance(e, dict) else str(e)
        for e in rights
    )


def run(root, meta_dir, dry_run, limit, show=0):
    merged_dir = root / "merged"
    conv_dir = root / "converted"
    stats = {
        "scanned": 0, "rights_set": 0, "rights_removed": 0, "unchanged": 0,
        "indeterminate": 0, "no_merged_file": 0, "errors": 0,
    }
    shown = 0

    for src in ("zenodo", "figshare"):
        md = merged_dir / src
        if not md.exists():
            continue
        for f in sorted(md.glob("*_complete.json")):
            if limit and stats["scanned"] >= limit:
                break
            stats["scanned"] += 1
            rid = f.stem.replace("_complete", "")
            try:
                merged = json.loads(f.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error(f"  read error {rid}: {e}")
                stats["errors"] += 1
                continue
            if not isinstance(merged, dict):
                stats["unchanged"] += 1
                continue

            rights, source = deposit_rights(src, rid, conv_dir, meta_dir)
            if source is None:
                stats["indeterminate"] += 1
                continue

            current = merged.get("rightsList")
            changed = False
            if rights:
                if current != rights:
                    if show and shown < show:
                        logger.info(f"  CHANGE {src}/{rid}: [{_compact(current)}] -> "
                                    f"[{_compact(rights)}]  (src={source})")
                        shown += 1
                    merged["rightsList"] = rights
                    stats["rights_set"] += 1
                    changed = True
            else:
                if "rightsList" in merged:
                    if show and shown < show:
                        logger.info(f"  REMOVE {src}/{rid}: [{_compact(current)}] -> (none)"
                                    f"  (src={source})")
                        shown += 1
                    del merged["rightsList"]
                    stats["rights_removed"] += 1
                    changed = True

            if not changed:
                stats["unchanged"] += 1
                continue

            if not dry_run:
                try:
                    f.write_text(json.dumps(merged, indent=2, ensure_ascii=False),
                                 encoding="utf-8")
                except Exception as e:
                    logger.error(f"  write error {rid}: {e}")
                    stats["errors"] += 1

    return stats


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, required=True,
                    help="corpus root with merged/ and converted/")
    ap.add_argument("--metadata-dir", type=Path, default=None,
                    help="raw deposit metadata root with <src>/<id>.json (recommended)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--show", type=int, default=0,
                    help="print before->after for the first N changed records")
    args = ap.parse_args()

    if not (args.root / "merged").exists():
        ap.error(f"no merged/ under {args.root}")

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    logger.info(f"[{mode}] backfill licenses  root={args.root}  metadata={args.metadata_dir}")
    stats = run(args.root, args.metadata_dir, args.dry_run, args.limit, args.show)
    logger.info("Done:")
    for k in ("scanned", "rights_set", "rights_removed", "unchanged",
              "indeterminate", "errors"):
        logger.info(f"  {k:14s} {stats[k]}")
    logger.info("Next: re-run enforce_license_policy.py over the same merged dir "
                "to strip content where the license is now empty/blocked.")


if __name__ == "__main__":
    main()
