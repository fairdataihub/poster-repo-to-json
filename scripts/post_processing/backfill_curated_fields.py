#!/usr/bin/env python3
"""
Backfill the v0.4.0 repository-authoritative merge deltas onto an existing
merged corpus, in place, WITHOUT re-running poster2json.

Older merged records were produced when the merger treated extraction as the
primary source for creators, descriptions and funding. v0.4.0 makes the
depositor-curated repository metadata authoritative for those fields. This
script re-applies just those deltas to each already-merged + enriched record
using the converted deposit metadata as the source of truth, while PRESERVING
the downstream enrichment already baked into the merged file (resolved ROR /
ORCID identifiers on creators, funderIdentifier on funders, domain
classification, license policy, _postprocess_json cleanup, etc.).

Deltas applied (only when the deposit provides the field):
  * creators       -> repository names/order win; carry over the ORCID/ROR/
                      nameType already resolved on the merged record.
  * descriptions   -> deposit description becomes the primary Abstract; the
                      LLM summary is demoted to a secondary "Other".
  * fundingReferences -> deposit funders/grants win; any funderIdentifier
                      already resolved on the merged record is carried over.
  * subjects       -> union + case-insensitive dedup (idempotent).

nameType note: records converted by the pre-0.4.0 converter hardcoded
"Personal". Legacy Zenodo/Figshare records carry no person_or_org.type, so
re-conversion would not change nameType for them; this script leaves the
existing nameType in place. To refresh nameType for newer InvenioRDM records,
re-run `run_merge.py` Phase 1 (conversion) first, then this backfill.

Layout (mirrors run_merge.py), per corpus root:
    <root>/converted/{zenodo,figshare}/<rec_id>.json     # deposit metadata
    <root>/merged/{zenodo,figshare}/<rec_id>_complete.json  # merged + enriched

extraction_only/ records have no deposit metadata and are left untouched.

Usage:
    python backfill_curated_fields.py --root /home/james/corpus_output --dry-run
    python backfill_curated_fields.py --root /home/james/corpus_output
    python backfill_curated_fields.py \
        --merged-dir  /path/merged \
        --converted-dir /path/converted
    # Run once per corpus (pre-2025 root and 2025 root).
"""
import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Make the package importable whether run from a checkout or an installed env.
_SRC = Path(__file__).resolve().parents[2] / "src"
if _SRC.exists():
    sys.path.insert(0, str(_SRC))

from poster_to_json.merger import MetadataMerger  # noqa: E402

_merger = MetadataMerger()


def _funder_key(name: str) -> str:
    return (name or "").strip().lower()


def _carry_funder_identifiers(new_funders, old_funders):
    """Copy any resolved funderIdentifier from the old merged funders onto the
    deposit funders, matching by normalized funderName. Preserves enrichment."""
    if not new_funders or not old_funders:
        return new_funders
    old_by_name = {}
    for f in old_funders:
        if isinstance(f, dict) and f.get("funderName") and f.get("funderIdentifier"):
            old_by_name[_funder_key(f["funderName"])] = f
    out = []
    for f in new_funders:
        if not isinstance(f, dict):
            out.append(f)
            continue
        nf = dict(f)
        if not nf.get("funderIdentifier"):
            m = old_by_name.get(_funder_key(nf.get("funderName", "")))
            if m:
                nf["funderIdentifier"] = m["funderIdentifier"]
                if m.get("funderIdentifierType"):
                    nf["funderIdentifierType"] = m["funderIdentifierType"]
                if m.get("schemeUri"):
                    nf["schemeUri"] = m["schemeUri"]
        out.append(nf)
    return out


def backfill_record(merged: dict, converted: dict) -> bool:
    """Apply the curated-field deltas in place. Returns True if anything changed."""
    changed = False

    # creators: deposit names/order win; carry resolved ORCID/ROR/nameType from
    # the merged record (which already holds the enrichment).
    conv_creators = converted.get("creators")
    if conv_creators:
        merged_creators = merged.get("creators") or []
        new_creators = _merger._enrich_creators(merged_creators, conv_creators)
        if new_creators != merged.get("creators"):
            merged["creators"] = new_creators
            changed = True

    # descriptions: deposit description -> primary Abstract; LLM summary -> Other.
    conv_descs = converted.get("descriptions")
    if conv_descs:
        new_descs = _merger._merge_descriptions(merged.get("descriptions"), conv_descs)
        if new_descs != merged.get("descriptions"):
            merged["descriptions"] = new_descs
            changed = True

    # fundingReferences: deposit funders win; carry over resolved funderIdentifier.
    conv_funding = converted.get("fundingReferences")
    if conv_funding:
        new_funding = _carry_funder_identifiers(
            [dict(f) if isinstance(f, dict) else f for f in conv_funding],
            merged.get("fundingReferences") or [],
        )
        if new_funding != merged.get("fundingReferences"):
            merged["fundingReferences"] = new_funding
            changed = True

    # subjects: union + dedup (idempotent if already merged).
    conv_subjects = converted.get("subjects")
    if conv_subjects:
        new_subjects = _merger._merge_subjects(merged.get("subjects"), conv_subjects)
        if new_subjects != merged.get("subjects"):
            merged["subjects"] = new_subjects
            changed = True

    return changed


def _iter_merged(merged_dir: Path):
    """Yield (source, rec_id, merged_path) for every {rec_id}_complete.json."""
    for source in ("zenodo", "figshare"):
        sub = merged_dir / source
        if not sub.exists():
            continue
        for f in sub.glob("*_complete.json"):
            rec_id = f.stem.replace("_complete", "")
            yield source, rec_id, f


def run(merged_dir: Path, converted_dir: Path, dry_run: bool, limit: int) -> dict:
    stats = {"scanned": 0, "changed": 0, "no_metadata": 0, "errors": 0, "unchanged": 0}
    for source, rec_id, merged_path in _iter_merged(merged_dir):
        if limit and stats["scanned"] >= limit:
            break
        stats["scanned"] += 1

        conv_path = converted_dir / source / f"{rec_id}.json"
        if not conv_path.exists():
            stats["no_metadata"] += 1
            continue

        try:
            merged = json.loads(merged_path.read_text(encoding="utf-8"))
            converted = json.loads(conv_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"  read error {rec_id}: {e}")
            stats["errors"] += 1
            continue

        if not isinstance(merged, dict) or not isinstance(converted, dict):
            stats["unchanged"] += 1
            continue
        if "error" in merged:
            stats["unchanged"] += 1
            continue

        try:
            if backfill_record(merged, converted):
                stats["changed"] += 1
                if not dry_run:
                    merged_path.write_text(
                        json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8"
                    )
            else:
                stats["unchanged"] += 1
        except Exception as e:
            logger.error(f"  backfill error {rec_id}: {e}")
            stats["errors"] += 1

    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path,
                        help="Corpus root containing converted/ and merged/")
    parser.add_argument("--merged-dir", type=Path,
                        help="Override: merged dir (defaults to <root>/merged)")
    parser.add_argument("--converted-dir", type=Path,
                        help="Override: converted dir (defaults to <root>/converted)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without writing")
    parser.add_argument("--limit", type=int, default=0,
                        help="Stop after N merged files (0 = no limit)")
    args = parser.parse_args()

    merged_dir = args.merged_dir or (args.root / "merged" if args.root else None)
    converted_dir = args.converted_dir or (args.root / "converted" if args.root else None)
    if not merged_dir or not converted_dir:
        parser.error("provide --root, or both --merged-dir and --converted-dir")
    if not merged_dir.exists():
        parser.error(f"merged dir not found: {merged_dir}")
    if not converted_dir.exists():
        parser.error(f"converted dir not found: {converted_dir}")

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    logger.info(f"[{mode}] backfill curated fields")
    logger.info(f"  merged:    {merged_dir}")
    logger.info(f"  converted: {converted_dir}")

    stats = run(merged_dir, converted_dir, args.dry_run, args.limit)

    logger.info("Done:")
    for k in ("scanned", "changed", "unchanged", "no_metadata", "errors"):
        logger.info(f"  {k:12s} {stats[k]}")
    if args.dry_run:
        logger.info("  (dry-run: no files written)")


if __name__ == "__main__":
    main()
