#!/usr/bin/env python3
"""Repair merged records against their own deposit (the repository record).

Fixes the issues of the 2026-10-30 corpus audit, each with a general rule:
  - creators: a record left with no creators gets the deposit's, cleaned by the current
    rules (normalize_creators: names in any script are real; a work-package team that is
    the only creator stays, as an organization);
  - titles: a bare-string title becomes {"title": ...}; an empty or template title
    ("Main Poster Title") takes a reasonable deposit title (replace_bad_llm_title);
  - text damage from PDF extraction: a short text (title, publisher, conference,
    subject, funder, affiliation) takes the deposit's spelling of the same letters
    (accents, undecodable ligatures, lost spaces; repair_from_reference), and any
    U+FFFD left anywhere is filled with the ligature letters that make a known word
    (repair_ligatures, with a vocabulary of the deposits' own words and ROR names);
  - identifiers: a DOI with trailing punctuation is trimmed; a Zenodo record with no DOI
    gets 10.5281/zenodo.<id> when DataCite has that DOI registered for the record;
  - affiliations whose name is a URL (fix_url_affiliations; a ROR URL that differs from
    the deposit's for the same person takes the deposit's);
  - subjects repeated with other case or spacing (dedup_subjects); a funder named as the
    poster's title (drop_junk_funding).
Idempotent. Run with --dry-run first.

Usage (pubverse env):
    ~/myenv/bin/python repair_from_deposit.py --corpus-dir /storage/poster-work/pre2025 \
        --ror-data /storage/poster-work/ror-data/v2.15-2026-10-20-ror-data_schema_v2.json \
        --vocab /storage/poster-work/scratch/deposit_vocab.pickle \
        --vocab-corpora /storage/poster-work/pre2025 /storage/poster-work/data2025 \
        --doi-cache /storage/poster-work/scratch/zenodo_doi_check.json --dry-run --show 20
"""
import argparse
import json
import logging
import pickle
import re
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

_SRC = Path(__file__).resolve().parents[2] / "src"
if _SRC.exists():
    sys.path.insert(0, str(_SRC))
from poster_to_json.field_normalize import (  # noqa: E402
    _URL_ONLY_RE, _ROR_URL_RE, dedup_subjects, drop_junk_funding, fix_url_affiliations,
    normalize_creators, normalize_title_entries, replace_bad_llm_title, repair_text_from_deposit,
)
from poster_to_json.schema_converter import SchemaConverter  # noqa: E402

_DOI_TRAIL_RE = re.compile(r"[\s,.;:]+$")


def _deposit_texts(corpora):
    for corpus in corpora:
        for f in sorted(Path(corpus, "metadata").glob("*/*.json")):
            try:
                m = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            md = m.get("metadata") if isinstance(m.get("metadata"), dict) else m
            yield md.get("title")
            yield md.get("description")
            yield from (k for k in (md.get("keywords") or md.get("tags") or []) if isinstance(k, str))
            # names as the depositors typed them (Zenodo creators, Figshare authors)
            yield from (c.get("name") for c in md.get("creators") or [] if isinstance(c, dict))
            yield from (a.get("full_name") for a in m.get("authors") or [] if isinstance(a, dict))


def build_vocab(corpora, ror_data):
    """Case-folded word counts (fairly.repair.word_counts) of the deposits' own texts
    (title, description, keywords, creator names) and of the ROR organization names:
    words people typed, to fill undecodable ligatures with."""
    from fairly.repair import word_counts
    vocab = word_counts(_deposit_texts(corpora))
    vocab.update(word_counts(n.get("value") for org in json.loads(Path(ror_data).read_text(encoding="utf-8"))
                             for n in org.get("names") or []))
    return vocab


def ror_names(ror_data):
    out = {}
    for org in json.loads(Path(ror_data).read_text(encoding="utf-8")):
        rid = str(org.get("id", "")).rsplit("/", 1)[-1]
        for n in org.get("names") or []:
            if "ror_display" in (n.get("types") or []):
                out[rid] = n.get("value")
    return out


class DoiCheck:
    """Is `doi` registered with DataCite for this Zenodo record? Answers cached on disk."""

    def __init__(self, path):
        self.path = Path(path) if path else None
        self.cache = json.loads(self.path.read_text(encoding="utf-8")) if self.path and self.path.exists() else {}

    def registered_for(self, doi, rid):
        if doi not in self.cache:
            try:
                req = urllib.request.Request(f"https://api.datacite.org/dois/{doi}",
                                             headers={"User-Agent": "poster-repo-to-json (fairdataihub)"})
                url = json.load(urllib.request.urlopen(req, timeout=30))["data"]["attributes"].get("url") or ""
            except Exception:
                url = ""
            self.cache[doi] = url
            time.sleep(0.2)
            if self.path:
                self.path.write_text(json.dumps(self.cache, indent=1), encoding="utf-8")
        return bool(re.search(rf"zenodo\.org/(?:record|records)/{rid}(?!\d)", self.cache[doi]))


def _deposit(corpus, src, rid):
    p = Path(corpus, "metadata", src, f"{rid.split('.v')[0]}.json")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None, None
    try:
        return raw, SchemaConverter().convert(raw, src)
    except Exception:
        return raw, None


def _references(dep):
    refs = [t.get("title") for t in (dep or {}).get("titles") or [] if isinstance(t, dict)]
    refs += [d.get("description") for d in (dep or {}).get("descriptions") or [] if isinstance(d, dict)]
    return [re.sub(r"<[^>]+>", " ", r) for r in refs if isinstance(r, str) and r.strip()]


def _deposit_ror_urls(dep):
    """Person name -> the ROR URL the deposit gives as that person's affiliation."""
    out = {}
    for c in (dep or {}).get("creators") or []:
        for a in c.get("affiliation") or []:
            n = a.get("name") if isinstance(a, dict) else a
            if isinstance(n, str) and _ROR_URL_RE.match(n.strip()):
                out[str(c.get("name", "")).strip().casefold()] = n.strip()
    return out


def repair_record(d, src, rid, raw, dep, vocab, rnames, doi_check, stats, samples, show):
    def note(kind, before, after):
        stats[kind] += 1
        if len(samples) < show:
            samples.append(f"{src}/{rid} {kind}: {str(before)[:90]!r} -> {str(after)[:90]!r}")

    changed = False
    if normalize_title_entries(d):
        note("title_shape", "string", "object")
        changed = True
    if dep:
        if not d.get("creators") and dep.get("creators"):
            tmp = {"creators": [dict(c) for c in dep["creators"]]}
            normalize_creators(tmp)
            if tmp.get("creators"):
                d["creators"] = tmp["creators"]
                note("creators_from_deposit", 0, [c.get("name") for c in d["creators"]])
                changed = True
        dep_title = next((t.get("title") for t in dep.get("titles") or [] if isinstance(t, dict)), None)
        before = (d.get("titles") or [{}])[0].get("title") if d.get("titles") else None
        if replace_bad_llm_title(d, dep_title):
            note("title_from_deposit", before, d["titles"][0].get("title"))
            changed = True
        # a ROR URL the deposit gives for this person is the depositor's own claim
        dep_urls = _deposit_ror_urls(dep)
        for c in d.get("creators") or []:
            want = dep_urls.get(str(c.get("name", "")).strip().casefold()) if isinstance(c, dict) else None
            for a in (c.get("affiliation") if isinstance(c, dict) else None) or []:
                n = a.get("name") if isinstance(a, dict) else None
                if want and isinstance(n, str) and _ROR_URL_RE.match(n.strip()) and n.strip() != want:
                    note("ror_url_from_deposit", n, want)
                    a["name"] = want
                    changed = True
    fixes = repair_text_from_deposit(d, _references(dep), vocab)
    for key, old, new in fixes:
        stats["text_values_repaired"] += 1
        note(f"text_repaired[{key}]", old, new)
    if fixes:
        stats["text_records_repaired"] += 1
        changed = True
    if "\ufffd" in json.dumps(d, ensure_ascii=False):
        stats["records_with_fffd_left"] += 1
    for i in d.get("identifiers") or []:
        if isinstance(i, dict) and str(i.get("identifierType")).upper() == "DOI" and isinstance(i.get("identifier"), str):
            t = _DOI_TRAIL_RE.sub("", i["identifier"])
            if t != i["identifier"]:
                note("doi_trimmed", i["identifier"], t)
                i["identifier"] = t
                changed = True
    if src == "zenodo" and not any(isinstance(i, dict) and str(i.get("identifierType")).upper() == "DOI"
                                   for i in d.get("identifiers") or []):
        doi = f"10.5281/zenodo.{rid}"
        if doi_check and doi_check.registered_for(doi, rid):
            d["identifiers"] = [{"identifier": doi, "identifierType": "DOI"}] + list(d.get("identifiers") or [])
            note("zenodo_doi_added", None, doi)
            changed = True
        else:
            stats["zenodo_no_doi_unverified"] += 1
    url_affs = sum(1 for c in d.get("creators") or [] if isinstance(c, dict) for a in c.get("affiliation") or []
                   if isinstance(a, dict) and isinstance(a.get("name"), str) and _URL_ONLY_RE.match(a["name"].strip()))
    if fix_url_affiliations(d, rnames.get):
        note("url_affiliations", url_affs, "fixed")
        changed = True
    n_sub = len(d.get("subjects") or [])
    if dedup_subjects(d):
        note("subjects_deduped", n_sub, len(d.get("subjects") or []))
        changed = True
    n_fund = len(d.get("fundingReferences") or [])
    if drop_junk_funding(d, acknowledgements=False):
        note("funding_cleaned", n_fund, len(d.get("fundingReferences") or []))
        changed = True
    return changed


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus-dir", required=True, help="holds merged/ and metadata/{zenodo,figshare}/")
    ap.add_argument("--merged-subdir", default="merged")
    ap.add_argument("--ror-data", required=True)
    ap.add_argument("--vocab", required=True, help="pickle of word counts; built when missing")
    ap.add_argument("--vocab-corpora", nargs="+", default=None, help="corpus dirs whose deposits build the vocabulary")
    ap.add_argument("--doi-cache", default=None, help="json cache of DataCite DOI checks (Zenodo DOI backfill)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--show", type=int, default=0)
    args = ap.parse_args()

    vp = Path(args.vocab)
    if vp.exists():
        vocab = pickle.loads(vp.read_bytes())
    else:
        vocab = build_vocab(args.vocab_corpora or [args.corpus_dir], args.ror_data)
        vp.write_bytes(pickle.dumps(vocab))
    logger.info(f"vocabulary: {len(vocab)} words")
    rnames = ror_names(args.ror_data)
    doi_check = DoiCheck(args.doi_cache)
    stats, samples = Counter(), []
    logger.info(f"[{'DRY-RUN' if args.dry_run else 'LIVE'}] repair from deposit  {args.corpus_dir}/{args.merged_subdir}")
    for src in ("zenodo", "figshare"):
        for f in sorted(Path(args.corpus_dir, args.merged_subdir, src).glob("*_complete.json")):
            stats["scanned"] += 1
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                stats["errors"] += 1
                continue
            rid = f.name[: -len("_complete.json")]
            raw, dep = _deposit(args.corpus_dir, src, rid)
            if dep is None:
                stats["no_deposit"] += 1
            if repair_record(d, src, rid, raw, dep, vocab, rnames, doi_check, stats, samples, args.show):
                stats["records_changed"] += 1
                if not args.dry_run:
                    f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    for s in samples:
        logger.info(f"  e.g. {s}")
    for k in sorted(stats):
        logger.info(f"  {k:28s} {stats[k]}")


if __name__ == "__main__":
    main()
