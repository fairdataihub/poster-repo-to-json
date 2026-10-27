#!/usr/bin/env python3
"""Undo funder synonym merges that named a different funder.

The funder synlustre map renamed funderName in place to its cluster's most frequent name
and kept every other field, so a merge such as "National Research Fund" -> "U.S. National
Science Foundation" or "Genome Canada ..." -> "Canadian Institutes of Health Research"
left the record naming another funder, sometimes with the first funder's id and award.

Each record's fundingReferences are compared, position by position, with the list they
were made from before the map was applied:
  - Zenodo: the funding registered with DataCite (enrich_from_datacite.py made the record's
    list exactly that: deposit-authoritative), read from the DataCite cache;
  - Figshare: the funding in the pre-merge snapshot of the merged record (--snapshot-dir).
The lists must line up: same length, and at every position the same award number and the
same funder id (when both have one). A funderName that differs from the reference at its
position and names another funder (affiliation_merge_is_lossy, fairly's identity rule)
gets the reference name back, with the reference's id fields. A harmless merge ("NIH" ->
"National Institutes of Health") is kept. Records whose lists don't line up are counted
and listed, never guessed. Idempotent.

Usage (pubverse env):
    ~/myenv/bin/python restore_lossy_funder_merges.py --merged-dir /storage/poster-work/pre2025/merged \
        --cache-dir /storage/poster-work/datacite_aff_cache --snapshot-dir /storage/poster-work/azure_snap/merged \
        --ror-data /storage/poster-work/ror-data/v2.15-2026-10-20-ror-data_schema_v2.json --dry-run --show 30
"""
import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

_SRC = Path(__file__).resolve().parents[2] / "src"
if _SRC.exists():
    sys.path.insert(0, str(_SRC))
from poster_to_json.field_normalize import (  # noqa: E402
    _has_letter, _is_placeholder, affiliation_merge_is_lossy, load_geo_tokens,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backfill_datacite_ror_affiliations import _cache_state, _doi_of, _safe  # noqa: E402
from enrich_from_datacite import map_funding  # noqa: E402

_ID_FIELDS = ("funderIdentifier", "funderIdentifierType", "schemeUri", "schemeURI")


def _norm(v):
    return " ".join(str(v or "").split()).lower()


def reference_funding(src, d, rid, cache_dir, snapshot_dir):
    """The funding list the record's list was made from, or None when unknown."""
    if src == "zenodo":
        doi = _doi_of(d)
        if not doi or not cache_dir:
            return None
        state, attrs = _cache_state(Path(cache_dir) / f"{_safe(doi)}.json", 10 ** 6)
        if state != "hit":
            return None
        return [map_funding(x) for x in (attrs.get("fundingReferences") or []) if x.get("funderName")]
    if not snapshot_dir:
        return None
    try:
        snap = json.loads((Path(snapshot_dir) / src / f"{rid}_complete.json").read_text(encoding="utf-8"))
    except Exception:
        return None
    return [x for x in (snap.get("fundingReferences") or []) if isinstance(x, dict)]


def lined_up(cur, ref):
    """Same length, and at each position the same award number and (when both carry one)
    the same funder id."""
    if len(cur) != len(ref):
        return False
    for a, b in zip(cur, ref):
        if _norm(a.get("awardNumber")) != _norm(b.get("awardNumber")):
            return False
        ia, ib = _norm(a.get("funderIdentifier")).rstrip("/"), _norm(b.get("funderIdentifier")).rstrip("/")
        if ia and ib and ia.rsplit("/", 1)[-1] != ib.rsplit("/", 1)[-1]:
            return False
    return True


def restore_record(d, ref, geo, stats, samples=None, show=0):
    """Put back reference funder names where the current one names another funder;
    returns the number of entries restored."""
    cur = [x for x in (d.get("fundingReferences") or []) if isinstance(x, dict)]
    if ref is None:
        stats["no_reference"] += 1
        return 0
    if not cur:
        return 0
    if not lined_up(cur, ref):
        stats["not_lined_up"] += 1
        return 0
    n = 0
    for a, b in zip(cur, ref):
        old, now = b.get("funderName"), a.get("funderName")
        if not old or not now or old == now:
            continue
        if len(str(old).strip()) <= 2 or not _has_letter(old) or _is_placeholder(old):
            stats["reference_is_junk"] += 1         # never put back "n/a" over a real funder
            continue
        if not affiliation_merge_is_lossy(old, now, geo):
            stats["harmless_merge_kept"] += 1
            continue
        a["funderName"] = old
        for k in _ID_FIELDS:
            a.pop(k, None)
        a.update({k: b[k] for k in _ID_FIELDS if b.get(k)})
        n += 1
        if samples is not None and len(samples) < show:
            samples.append(f"{now!r} -> {old!r}")
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merged-dir", required=True)
    ap.add_argument("--cache-dir", required=True, help="DataCite cache (backfill_datacite_ror_affiliations.py)")
    ap.add_argument("--snapshot-dir", default=None, help="pre-merge copy of the merged dir (zenodo/, figshare/)")
    ap.add_argument("--ror-data", required=True, help="ROR dump, for the place words of the identity rule")
    ap.add_argument("--list", default=None, help="write the records whose lists don't line up here")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--show", type=int, default=0)
    args = ap.parse_args()
    geo = load_geo_tokens(args.ror_data)
    logger.info(f"[{'DRY-RUN' if args.dry_run else 'LIVE'}] restore lossy funder merges  merged={args.merged_dir}")
    stats, samples, unaligned = Counter(), [], []
    for src in ("zenodo", "figshare"):
        for f in sorted((Path(args.merged_dir) / src).glob("*_complete.json")):
            stats["scanned"] += 1
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                stats["errors"] += 1
                continue
            if not d.get("fundingReferences"):
                continue
            stats["with_funding"] += 1
            rid = f.name[: -len("_complete.json")]
            before = stats["not_lined_up"]
            n = restore_record(d, reference_funding(src, d, rid, args.cache_dir, args.snapshot_dir), geo, stats,
                               samples, args.show)
            if stats["not_lined_up"] > before:
                unaligned.append(f"{src}/{rid}")
            if not n:
                continue
            stats["records_restored"] += 1
            stats["funders_restored"] += n
            if not args.dry_run:
                f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    for s in samples:
        logger.info(f"  e.g. {s}")
    for k in ("scanned", "with_funding", "records_restored", "funders_restored", "harmless_merge_kept",
              "reference_is_junk", "not_lined_up", "no_reference", "errors"):
        logger.info(f"  {k:22s} {stats[k]}")
    if args.list:
        Path(args.list).write_text("\n".join(unaligned) + "\n", encoding="utf-8")
        logger.info(f"  wrote {len(unaligned)} unaligned records -> {args.list}")


if __name__ == "__main__":
    main()
