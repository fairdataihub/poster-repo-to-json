#!/usr/bin/env python3
"""
Give records with no DOI the DOI of their own published abstract (backfill).

For each record without a DOI, looks up the registered title of each related
DOI (doi.org content negotiation, which answers for Crossref and DataCite DOIs)
and adopts the first whose title matches the poster's title. See
src/poster_to_json/adjacent_doi.py for the rule. Lookups are cached in a JSON
file so reruns make no network calls. Idempotent.

Usage:
    python adjacent_doi.py --batch /storage/poster-work/pre2025 \\
        --batch /storage/poster-work/data2025 --cache /storage/poster-work/doi_titles.json --dry-run
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from poster_to_json.adjacent_doi import adopt_adjacent_doi, has_doi  # noqa: E402

UA = "posters-science-indexer (https://posters.science)"


class TitleResolver:
    def __init__(self, cache_path: Path, delay: float = 0.1):
        self.cache_path = cache_path
        self.delay = delay
        self.cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
        self.lookups = 0

    def __call__(self, doi: str):
        key = doi.lower()
        if key in self.cache:
            return self.cache[key] or None
        url = "https://doi.org/" + urllib.parse.quote(doi, safe="/()")
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.citationstyles.csl+json",
                                                   "User-Agent": UA})
        title = ""
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.load(resp)
            t = data.get("title")
            title = (t[0] if isinstance(t, list) and t else t) or ""
        except urllib.error.HTTPError as e:
            if e.code not in (404, 406):  # not registered / no metadata: cache as empty
                return None               # transient: do not cache
        except Exception:
            return None
        self.lookups += 1
        self.cache[key] = title
        if self.lookups % 50 == 0:
            self.save()
        time.sleep(self.delay)
        return title or None

    def save(self):
        self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False, indent=0), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batch", action="append", required=True,
                    help="batch root with merged/; repeatable")
    ap.add_argument("--cache", required=True, help="JSON cache of DOI -> registered title")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = ap.parse_args()

    resolve = TitleResolver(Path(args.cache))
    stats = Counter()
    try:
        for b in args.batch:
            for repo_dir in sorted(p for p in (Path(b) / "merged").iterdir() if p.is_dir()):
                for path in sorted(repo_dir.glob("*_complete.json")):
                    try:
                        record = json.loads(path.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        stats["unreadable"] += 1
                        continue
                    if not isinstance(record, dict) or has_doi(record):
                        continue
                    stats["records without a DOI"] += 1
                    doi = adopt_adjacent_doi(record, resolve)
                    if not doi:
                        continue
                    stats["DOI adopted"] += 1
                    print(f"{repo_dir.name}/{path.name}: {doi}")
                    if not args.dry_run:
                        path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n",
                                        encoding="utf-8")
    finally:
        resolve.save()
    print()
    for k in sorted(stats):
        print(f"{k:28} {stats[k]}")
    print(f"{'network lookups':28} {resolve.lookups}")
    print("(dry run, nothing written)" if args.dry_run else "(written in place)")


if __name__ == "__main__":
    main()
