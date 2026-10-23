#!/usr/bin/env python3
"""Add the ROR affiliations a repository registered with DataCite to each creator.

Zenodo and institutional Figshare portals register creator affiliations with DataCite,
often with a ROR id ("UL (United States)", https://ror.org/045n75910, for the UL Research
Institutes portal). These are only returned when DataCite is asked with ?affiliation=true,
which fetch_datacite.py never did, and enrich_from_datacite.py only ran on Zenodo records.
So Figshare posters carried no institution unless the poster's own header named it.

For every record with a DOI, the DataCite record is fetched with affiliations (cached).
For each DataCite creator that matches a record creator (same_author), and each of its
affiliations that has a ROR id:
  - the creator already has that ROR id: nothing to do;
  - the creator has an affiliation without an id whose name is exactly the DataCite name
    or one of the ROR record's names: the id is added to it;
  - otherwise a new affiliation is added, named by the ROR record's display name.
The ROR id comes from the repository, not from matching text, so it is used as given.
Ids missing from the local ROR dump keep the name DataCite has. Never removes anything.
Idempotent.

Usage (pubverse env):
    ~/myenv/bin/python backfill_datacite_ror_affiliations.py \
        --merged-dir /storage/poster-work/pre2025/merged \
        --cache-dir /storage/poster-work/datacite_aff_cache \
        --ror-data /storage/poster-work/ror-data/v2.9-2026-06-23-ror-data.json \
        [--fetch] [--delay 0.15] --dry-run --show 30
"""
import argparse
import json
import logging
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

_SRC = Path(__file__).resolve().parents[2] / "src"
if _SRC.exists():
    sys.path.insert(0, str(_SRC))
from poster_to_json.field_normalize import affiliation_tokens, same_author  # noqa: E402

ROR_PREFIX = "https://ror.org/"
_UA = "posters.science-pipeline/0.40 (https://posters.science; mailto:joneill@calmi2.org)"


def _safe(doi):
    return doi.replace("/", "_").replace(":", "_")


def _doi_of(d):
    for i in d.get("identifiers") or []:
        if isinstance(i, dict) and i.get("identifierType") == "DOI" and i.get("identifier"):
            return str(i["identifier"]).strip()
    return None


def fetch(doi, cache_dir, delay):
    """DataCite attributes for `doi` with affiliations, from cache or the API. None when
    DataCite has no record."""
    p = Path(cache_dir) / f"{_safe(doi)}.json"
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8"))
        return d or None
    url = f"https://api.datacite.org/dois/{urllib.parse.quote(doi, safe='')}?affiliation=true"
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/vnd.api+json"})
    for attempt in range(5):
        try:
            time.sleep(delay)
            with urllib.request.urlopen(req, timeout=30) as r:
                attrs = json.load(r)["data"]["attributes"]
            p.write_text(json.dumps(attrs, ensure_ascii=False), encoding="utf-8")
            return attrs
        except urllib.error.HTTPError as e:
            if e.code == 404:
                p.write_text("{}", encoding="utf-8")
                return None
            if e.code in (429, 503):
                time.sleep(min(10 * 2 ** attempt, 120))
                continue
            time.sleep(5 * (attempt + 1))
        except Exception:
            time.sleep(5 * (attempt + 1))
    return None


def load_ror_names(path):
    """ROR id (https://ror.org/xxx) -> (display name, set of normalized names)."""
    out = {}
    for org in json.loads(Path(path).read_text(encoding="utf-8")):
        names = org.get("names") or []
        disp = next((n["value"] for n in names if "ror_display" in (n.get("types") or [])), None)
        if disp:
            out[org["id"]] = (disp, {" ".join(affiliation_tokens(n.get("value"))) for n in names})
    return out


def _ror_id(a):
    if not isinstance(a, dict):
        return None
    if (a.get("affiliationIdentifierScheme") or "").upper() != "ROR":
        return None
    v = str(a.get("affiliationIdentifier") or "").strip()
    if not v:
        return None
    return v if v.startswith(ROR_PREFIX) else ROR_PREFIX + v.rsplit("/", 1)[-1]


def enrich_record(d, dc, ror_names, samples, show):
    changed = 0
    for dcc in dc.get("creators") or []:
        if not isinstance(dcc, dict) or dcc.get("nameType") == "Organizational":
            continue
        dname = dcc.get("name") or ", ".join(x for x in (dcc.get("familyName"), dcc.get("givenName")) if x)
        rors = [(r, a.get("name")) for a in dcc.get("affiliation") or [] if (r := _ror_id(a))]
        if not dname or not rors:
            continue
        matches = [c for c in d.get("creators") or []
                   if isinstance(c, dict) and c.get("name") and same_author(str(c["name"]), str(dname))]
        if len(matches) != 1:
            continue
        cr = matches[0]
        n_before = changed
        affs = cr.get("affiliation") if isinstance(cr.get("affiliation"), list) else []
        affs = [{"name": a} if isinstance(a, str) else a for a in affs]
        for rid, dc_name in rors:
            if any(_ror_id(a) == rid for a in affs):
                continue
            disp, known = ror_names.get(rid, (dc_name, set()))
            known = known | {" ".join(affiliation_tokens(dc_name))}
            target = next((a for a in affs if isinstance(a, dict) and not a.get("affiliationIdentifier")
                           and " ".join(affiliation_tokens(a.get("name"))) in known), None)
            if target is not None:
                target.update({"affiliationIdentifier": rid, "affiliationIdentifierScheme": "ROR",
                               "schemeURI": "https://ror.org"})
                what = f"id on {target.get('name')!r}"
            elif disp:
                affs.append({"name": disp, "affiliationIdentifier": rid,
                             "affiliationIdentifierScheme": "ROR", "schemeURI": "https://ror.org"})
                what = f"added {disp!r}"
            else:
                continue
            changed += 1
            if show is not None and len(samples) < show:
                samples.append(f"{cr['name']}: {what} {rid}")
        if changed > n_before:
            cr["affiliation"] = affs
    return changed


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merged-dir", required=True)
    ap.add_argument("--cache-dir", required=True)
    ap.add_argument("--ror-data", required=True)
    ap.add_argument("--fetch", action="store_true", help="query DataCite for DOIs not in the cache")
    ap.add_argument("--delay", type=float, default=0.15)
    ap.add_argument("--workers", type=int, default=6,
                    help="parallel DataCite requests when prefetching (keep under 10/s)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--show", type=int, default=0)
    args = ap.parse_args()
    Path(args.cache_dir).mkdir(parents=True, exist_ok=True)
    ror_names = load_ror_names(args.ror_data)
    logger.info(f"[{'DRY-RUN' if args.dry_run else 'LIVE'}] {len(ror_names)} ROR records; merged={args.merged_dir}")

    files = sorted(Path(args.merged_dir).rglob("*_complete.json"))
    if args.fetch:                               # fill the cache in parallel first
        from concurrent.futures import ThreadPoolExecutor
        dois = set()
        for f in files:
            try:
                doi = _doi_of(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue
            if doi and not (Path(args.cache_dir) / f"{_safe(doi)}.json").exists():
                dois.add(doi)
        logger.info(f"  prefetching {len(dois)} DOIs with {args.workers} workers")
        with ThreadPoolExecutor(args.workers) as ex:
            for i, _ in enumerate(ex.map(lambda d: fetch(d, args.cache_dir, args.delay), sorted(dois)), 1):
                if i % 2000 == 0:
                    logger.info(f"  ... fetched {i}/{len(dois)}")

    stats, samples = Counter(), []
    show = args.show or None
    for f in files:
        stats["scanned"] += 1
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            stats["errors"] += 1
            continue
        doi = _doi_of(d)
        if not doi:
            stats["no_doi"] += 1
            continue
        cached = (Path(args.cache_dir) / f"{_safe(doi)}.json").exists()
        if not cached and not args.fetch:
            stats["not_cached"] += 1
            continue
        dc = fetch(doi, args.cache_dir, args.delay)
        if not dc:
            stats["no_datacite"] += 1
            continue
        n = enrich_record(d, dc, ror_names, samples, show)
        if not n:
            continue
        stats["records_changed"] += 1
        stats["affiliations_added_or_identified"] += n
        if not args.dry_run:
            f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
        if stats["scanned"] % 2000 == 0:
            logger.info(f"  ... {stats['scanned']} scanned, {stats['records_changed']} changed")
    for s in samples:
        logger.info(f"  e.g. {s}")
    for k in ("scanned", "no_doi", "not_cached", "no_datacite", "records_changed",
              "affiliations_added_or_identified", "errors"):
        logger.info(f"  {k:34s} {stats[k]}")


if __name__ == "__main__":
    main()
