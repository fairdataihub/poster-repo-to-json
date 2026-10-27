#!/usr/bin/env python3
"""Publisher repair: undo publisher merges that changed the organization, and name the
registrant's publisher where a Figshare record fell back to the bare repository.

`publisher` is the poster's own publisher, the cleaned poster2json extraction
(normalize_publisher, backfill_publisher_from_extraction.py). A poster that names none
falls back to its repository, "Zenodo" or "Figshare". Two repairs, each optional:

1. --extractions-dir: the publisher synlustre map was applied on top of the extraction
   publishers. When the current publisher is none of the record's own extraction
   publishers, and replacing the extraction publisher by it changes which organization is
   meant (affiliation_merge_is_lossy, fairly's identity rule), the extraction publisher is
   put back, cleaned with the corpus's publisher rigor (clean_llm_publishers.clean_publisher,
   conservative). A harmless merge ("The University of Sydney" -> "University of Sydney")
   is kept.
2. --cache-dir (the DataCite cache of backfill_datacite_ror_affiliations.py): a Figshare
   record whose publisher is the bare "Figshare" fallback takes the publisher registered
   with its own Figshare DOI (one that carries its article id) when that is not Figshare
   itself. Institutional portals
   register their own name ("UL Research Institutes", "Loughborough University").
   Zenodo records are left alone: Zenodo registers every deposit as "Zenodo", so another
   DataCite publisher belongs to a different resource's DOI.

Idempotent. Every skip is counted, also in --dry-run.

Usage (pubverse env):
    ~/myenv/bin/python fix_publishers.py --merged-dir /storage/poster-work/pre2025/merged \
        --ror-data /storage/poster-work/ror-data/v2.9-2026-06-23-ror-data.json \
        [--extractions-dir /storage/poster-work/pre2025/extractions] \
        [--metadata-dir /storage/poster-work/pre2025/metadata] \
        [--cache-dir /storage/poster-work/datacite_aff_cache] --dry-run --show 30
"""
import argparse
import json
import re
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
    _clean_publisher, affiliation_merge_is_lossy, load_geo_tokens,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpus_index import extraction_files_in  # noqa: E402
import clean_llm_publishers  # noqa: E402
from backfill_datacite_ror_affiliations import _cache_state, _doi_of, _safe  # noqa: E402

clean_llm_publishers._conservative = True          # extraction names may be long and real
_REPO = {"zenodo": "Zenodo", "figshare": "Figshare"}
_GENERIC_REGISTRANTS = {"figshare", "figshare.com", "figshare ltd", "figshare ltd.", "zenodo", "zenodo.org"}


def _name(p):
    return p.get("name") if isinstance(p, dict) else p


def extraction_publishers(extractions_dir, src, rid):
    """Raw extraction publishers of a record, one per extraction file, in file order."""
    out = []
    for f in extraction_files_in(extractions_dir, src, rid):
        try:
            p = _name(json.loads(Path(f).read_text(encoding="utf-8")).get("publisher"))
        except Exception:
            continue
        if isinstance(p, str) and p.strip():
            out.append(p)
    return out


def restore_publisher(d, src, raw, geo, stats):
    """Put back the record's own extraction publisher when the current one names another
    organization. `raw` is extraction_publishers() output. Returns (old, new) or None."""
    cur = _name(d.get("publisher"))
    if not isinstance(cur, str) or not cur.strip():
        stats["no_publisher"] += 1
        return None
    own = [x for x in raw if _clean_publisher(x)]
    if not own:
        stats["no_extraction_publisher"] += 1
        return None
    seen = {_clean_publisher(x) for x in own} | {clean_llm_publishers.clean_publisher(x) for x in own}
    if cur in seen:
        stats["kept_source_says_it"] += 1
        return None
    if cur == _REPO.get(src):
        stats["kept_repository_fallback"] += 1     # a later cleaning dropped the extraction value
        return None
    lossy = [x for x in own if affiliation_merge_is_lossy(_clean_publisher(x), cur, geo)]
    if len(lossy) < len(own):
        stats["harmless_merge_kept"] += 1          # some own wording is the same organization
        return None
    new = clean_llm_publishers.clean_publisher(own[0])
    if not new:
        stats["extraction_publisher_is_junk"] += 1
        return None
    d["publisher"] = {"name": new}
    stats["restored"] += 1
    return cur, new


def deposit_doi(doi, rid):
    """Is `doi` the Figshare deposit's own DOI? Figshare DOIs carry the article id
    ("10.60752/102376.27328098.v1"); a DOI taken from a published abstract does not."""
    article = str(rid).split(".v")[0]
    return bool(article) and re.search(rf"(?<!\d){re.escape(article)}(?!\d)", str(doi)) is not None


def registrant_publisher(d, src, rid, cache_dir, metadata_dir, stats):
    """Name the DataCite-registered publisher of a Figshare record that fell back to
    "Figshare". Returns (old, new) or None."""
    if src != "figshare":
        return None
    cur = _name(d.get("publisher"))
    if cur not in (None, "", "Figshare"):
        return None
    doi = _doi_of(d)
    if not doi:
        stats["dc_no_doi"] += 1
        return None
    if not deposit_doi(doi, rid):
        stats["dc_doi_not_the_deposit"] += 1       # e.g. a published abstract's DOI
        return None
    state, attrs = _cache_state(Path(cache_dir) / f"{_safe(doi)}.json", 10 ** 6)
    if state != "hit":
        stats["dc_not_cached" if state in ("absent", "stale") else "dc_no_record"] += 1
        return None
    reg = _clean_publisher(_name(attrs.get("publisher")))
    if not reg:
        stats["dc_no_publisher"] += 1
        return None
    if reg.casefold() in _GENERIC_REGISTRANTS:
        stats["dc_registrant_is_figshare"] += 1
        return None
    d["publisher"] = {"name": reg}
    stats["publisher_from_datacite"] += 1
    return cur, reg


_COUNTERS = ("scanned", "records_changed", "restored", "kept_source_says_it", "harmless_merge_kept",
             "kept_repository_fallback", "no_extraction_publisher", "extraction_publisher_is_junk",
             "no_publisher", "publisher_from_datacite", "dc_registrant_is_figshare", "dc_no_doi",
             "dc_doi_not_the_deposit", "dc_not_cached", "dc_no_record", "dc_no_publisher", "errors")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merged-dir", required=True)
    ap.add_argument("--ror-data", required=True, help="ROR dump, for the place words of the identity rule")
    ap.add_argument("--extractions-dir", default=None, help="repair 1: undo lossy publisher merges")
    ap.add_argument("--cache-dir", default=None, help="repair 2: DataCite publisher for the Figshare fallback")
    ap.add_argument("--metadata-dir", default=None, help="unused; kept for older command lines")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--show", type=int, default=0)
    args = ap.parse_args()
    if not (args.extractions_dir or args.cache_dir):
        ap.error("nothing to do: give --extractions-dir and/or --cache-dir")
    geo = load_geo_tokens(args.ror_data)
    logger.info(f"[{'DRY-RUN' if args.dry_run else 'LIVE'}] fix publishers  merged={args.merged_dir}")
    stats, samples = Counter(), []
    for src in ("zenodo", "figshare"):
        for f in sorted((Path(args.merged_dir) / src).glob("*_complete.json")):
            stats["scanned"] += 1
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                stats["errors"] += 1
                continue
            rid = f.name[: -len("_complete.json")]
            changes = []
            if args.extractions_dir:
                ch = restore_publisher(d, src, extraction_publishers(args.extractions_dir, src, rid), geo, stats)
                if ch:
                    changes.append(("restored", ch))
            if args.cache_dir:
                ch = registrant_publisher(d, src, rid, args.cache_dir, args.metadata_dir, stats)
                if ch:
                    changes.append(("datacite", ch))
            if not changes:
                continue
            stats["records_changed"] += 1
            for what, (old, new) in changes:
                if args.show and len(samples) < args.show:
                    samples.append(f"{src}/{rid} {what}: {old!r} -> {new!r}")
            if not args.dry_run:
                f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    for s in samples:
        logger.info(f"  e.g. {s}")
    for k in _COUNTERS:
        logger.info(f"  {k:30s} {stats[k]}")


if __name__ == "__main__":
    main()
