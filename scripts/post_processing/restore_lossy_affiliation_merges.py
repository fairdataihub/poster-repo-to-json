#!/usr/bin/env python3
"""Undo affiliation synonym merges that lost which organization was meant.

The affiliation synlustre map sends each variant to its cluster's most frequent name. When
that name is shorter and more generic, the merge drops identifying words:
"Library & Researcher Information Services" -> "Library", or "Department of
Microbiology..., University of California" -> the department alone. affiliation_merge_is_lossy
flags these merges (dropping only place names, address words, stopwords or a parenthesized
acronym is not a loss).

For every creator affiliation equal to the canonical of a lossy merge, the creator's own
source affiliations (an optional pre-merge snapshot, the repository deposit, then the
poster2json extraction) are checked for a variant that the map sends to that canonical. The
first such variant replaces the canonical (several are the same author's wordings, such as
with and without an address). If a source has the canonical itself, or no variant is found,
the affiliation is left as it is. Never drops a creator or an affiliation. Idempotent.

Usage (pubverse env):
    ~/myenv/bin/python restore_lossy_affiliation_merges.py \
        --corpus-dir /storage/poster-work/pre2025 \
        --synlustre /storage/poster-work/syn_affiliation.pickle \
        --ror-data /storage/poster-work/ror-data/v2.9-2026-06-23-ror-data.json \
        [--merged-subdir merged] --dry-run --show 30
"""
import argparse
import glob
import json
import logging
import pickle
import sys
import unicodedata
from collections import Counter
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

_SRC = Path(__file__).resolve().parents[2] / "src"
if _SRC.exists():
    sys.path.insert(0, str(_SRC))
from poster_to_json.field_normalize import (  # noqa: E402
    affiliation_merge_is_lossy, affiliation_tokens, load_geo_tokens, same_author,
)


def _clean(s):
    return unicodedata.normalize("NFKC", str(s)).strip()


def lossy_canonicals(syn, geo):
    """canonical -> set of variants whose merge onto it is lossy."""
    out = {}
    for v, c in syn.items():
        if v != c and affiliation_merge_is_lossy(v, c, geo):
            out.setdefault(c, set()).add(v)
    return out


def _aff_names(value):
    """Affiliation strings from a deposit or extraction creator (string, list of strings
    or of {"name": ...}). Semicolon-lumped strings are split, as the merger does."""
    items = value if isinstance(value, list) else [value]
    out = []
    for a in items:
        n = a.get("name") if isinstance(a, dict) else a
        if isinstance(n, str):
            out.extend(_clean(p) for p in n.split(";") if _clean(p))
    return out


def source_creators(corpus, src, rid, snapshot_dir=None):
    """[(name, [affiliation strings])] from the deposit metadata, the extraction and, when
    given, the pre-merge snapshot of the merged record."""
    people = []
    if snapshot_dir:
        try:
            snap = json.loads((Path(snapshot_dir) / src / f"{rid}_complete.json").read_text(encoding="utf-8"))
            for c in snap.get("creators") or []:
                if isinstance(c, dict):
                    people.append((c.get("name"), _aff_names(c.get("affiliation"))))
        except Exception:
            pass
    meta = Path(corpus) / "metadata" / src / f"{rid}.json"
    try:
        m = json.loads(meta.read_text(encoding="utf-8"))
        if src == "zenodo":
            for c in (m.get("metadata") or {}).get("creators") or []:
                people.append((c.get("name"), _aff_names(c.get("affiliation"))))
        else:   # Figshare deposits carry no affiliation; keep names for matching
            for c in m.get("authors") or []:
                people.append((c.get("full_name"), []))
    except Exception:
        pass
    for f in glob.glob(str(Path(corpus) / "extractions" / f"{src}_{glob.escape(rid)}_*_extracted.json")):
        try:
            e = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        for c in e.get("creators") or []:
            if isinstance(c, dict):
                people.append((c.get("name"), _aff_names(c.get("affiliation"))))
    return people


def restore_record(d, people, lossy, syn, samples, show):
    changed = 0
    for cr in d.get("creators") or []:
        if not isinstance(cr, dict) or not isinstance(cr.get("affiliation"), list):
            continue
        mine = [a for n, affs in people if n and same_author(str(cr.get("name") or ""), str(n))
                for a in affs]
        names_now = {a.get("name") for a in cr["affiliation"] if isinstance(a, dict)}
        for a in cr["affiliation"]:
            if not isinstance(a, dict) or a.get("name") not in lossy:
                continue
            canon = a["name"]
            if canon in mine:                     # the source itself says this; keep it
                continue
            # the creator's own variants, in source order (snapshot, deposit, extraction)
            cands = [v for v in dict.fromkeys(mine)
                     if syn.get(v) == canon and v in lossy[canon] and v not in names_now]
            if not cands:
                continue
            v = cands[0]
            if show is not None and len(samples) < show:
                samples.append(f"{canon!r} -> {v!r}")
            a["name"] = v
            names_now.add(v)
            changed += 1
    return changed


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus-dir", required=True, help="has metadata/, extractions/ and the merged dir")
    ap.add_argument("--merged-subdir", default="merged")
    ap.add_argument("--snapshot-dir", default=None,
                    help="optional pre-merge copy of the merged dir (zenodo/, figshare/) as an extra source")
    ap.add_argument("--only", default=None, help="process one record id, e.g. figshare/27063577")
    ap.add_argument("--synlustre", required=True)
    ap.add_argument("--ror-data", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--show", type=int, default=0)
    args = ap.parse_args()

    syn = pickle.load(open(args.synlustre, "rb"))
    geo = load_geo_tokens(args.ror_data)
    lossy = lossy_canonicals(syn, geo)
    logger.info(f"[{'DRY-RUN' if args.dry_run else 'LIVE'}] {sum(map(len, lossy.values()))} of "
                f"{sum(1 for v, c in syn.items() if v != c)} merges are lossy, onto {len(lossy)} canonicals")

    stats, samples, canon_hits = Counter(), [], Counter()
    show = args.show or None
    for src in ("zenodo", "figshare"):
        for f in sorted((Path(args.corpus_dir) / args.merged_subdir / src).glob("*_complete.json")):
            if args.only and args.only != f"{src}/{f.name[:-len('_complete.json')]}":
                continue
            stats["scanned"] += 1
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                stats["errors"] += 1
                continue
            before = [a.get("name") for c in d.get("creators") or [] if isinstance(c, dict)
                      for a in (c.get("affiliation") or []) if isinstance(a, dict)]
            if not any(n in lossy for n in before):
                continue
            stats["records_with_lossy_canonical"] += 1
            rid = f.name[: -len("_complete.json")]
            n = restore_record(d, source_creators(args.corpus_dir, src, rid, args.snapshot_dir), lossy, syn, samples, show)
            if not n:
                stats["unresolved_records"] += 1
                continue
            for b in before:
                if b in lossy:
                    canon_hits[b] += 1
            stats["records_restored"] += 1
            stats["affiliations_restored"] += n
            if not args.dry_run:
                f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    for s in samples:
        logger.info(f"  e.g. {s}")
    logger.info(f"  most restored canonicals: {canon_hits.most_common(15)}")
    for k in ("scanned", "records_with_lossy_canonical", "records_restored", "affiliations_restored",
              "unresolved_records", "errors"):
        logger.info(f"  {k:30s} {stats[k]}")


if __name__ == "__main__":
    main()
