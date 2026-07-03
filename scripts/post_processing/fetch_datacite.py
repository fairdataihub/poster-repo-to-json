#!/usr/bin/env python3
"""
Fetch deposit metadata from the DataCite REST API into a local cache.

DataCite returns each DOI's registered metadata already in DataCite schema
(creators with givenName/familyName/nameType/nameIdentifiers/affiliation,
fundingReferences, subjects, rightsList, relatedIdentifiers, dates,
descriptions) — the source for the Phase-3 enrichment (SCHEMA_ALIGNMENT_PLAN.md).

Reads DOIs from the merged corpus (identifiers[].identifierType == "DOI"),
caches data.attributes as <cache>/<safe-doi>.json, skips already-cached, is
polite (delay + retry on 429/5xx), and resumable.

Usage:
    python fetch_datacite.py --merged-dir <m>/zenodo --cache-dir <c> [--limit N] [--delay 0.2]
"""
import argparse
import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

_UA = "posters.science-indexing/1.0 (mailto:jimnoneill@gmail.com)"


def _safe(doi):
    return doi.replace("/", "_").replace(":", "_")


def _doi_of(record):
    for i in (record.get("identifiers") or []):
        if isinstance(i, dict) and i.get("identifierType") == "DOI" and i.get("identifier"):
            return str(i["identifier"]).strip()
    return None


def fetch(doi, retries=5):
    url = f"https://api.datacite.org/dois/{urllib.parse.quote(doi, safe='')}"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA,
                                                       "Accept": "application/vnd.api+json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json.loads(r.read().decode("utf-8"))
                return {"attributes": (body.get("data") or {}).get("attributes") or {}}
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {"_notfound": True}
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(min(2 ** attempt, 30))
                continue
            return {"_error": e.code}
        except Exception:
            time.sleep(min(2 ** attempt, 30))
    return {"_error": "retries_exhausted"}


def run(merged_dir, cache_dir, limit, delay):
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    stats = {"records": 0, "fetched": 0, "cached": 0, "notfound": 0, "errors": 0, "no_doi": 0}
    for f in sorted(Path(merged_dir).glob("*_complete.json")):
        if limit and stats["records"] >= limit:
            break
        stats["records"] += 1
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            stats["errors"] += 1
            continue
        doi = _doi_of(d)
        if not doi:
            stats["no_doi"] += 1
            continue
        out = cache / f"{_safe(doi)}.json"
        if out.exists():
            stats["cached"] += 1
            continue
        res = fetch(doi)
        if res.get("_notfound"):
            stats["notfound"] += 1
        elif res.get("_error") is not None:
            stats["errors"] += 1
        else:
            stats["fetched"] += 1
        try:
            out.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
        except Exception:
            stats["errors"] += 1
        if stats["fetched"] and stats["fetched"] % 500 == 0:
            logger.info(f"  fetched {stats['fetched']} (records seen {stats['records']})")
        time.sleep(delay)
    return stats


def main():
    import urllib.parse  # noqa: F401 (used in fetch via urllib.request)
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merged-dir", required=True, help="dir of *_complete.json (e.g. .../merged/zenodo)")
    ap.add_argument("--cache-dir", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=0.2)
    args = ap.parse_args()

    logger.info(f"fetch DataCite  merged={args.merged_dir}  cache={args.cache_dir}  delay={args.delay}")
    stats = run(args.merged_dir, args.cache_dir, args.limit, args.delay)
    for k in ("records", "fetched", "cached", "notfound", "errors", "no_doi"):
        logger.info(f"  {k:10s} {stats[k]}")


import urllib.parse  # noqa: E402  (module-level for fetch())

if __name__ == "__main__":
    main()
