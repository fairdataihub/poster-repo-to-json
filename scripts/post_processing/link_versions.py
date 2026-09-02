#!/usr/bin/env python3
"""
Link repository version families across an existing poster corpus.

Per-record conversion can only see one deposit at a time, so it records the
family key and the position but cannot point a record at its siblings. This
script runs over a whole corpus, groups records into families, and writes the
sibling cross-links plus the settled isLatestVersion flag.

It reads the family signals from the raw harvested metadata when that is
available (--raw), because Zenodo's relations.version graph is the only place
the true sequence and the is_last flag live. Where raw metadata is missing it
falls back to versionInfo already present on the poster JSON, so a corpus that
was converted with 0.39.0 or later can be relinked without a re-harvest.

Every version is kept. Nothing is deleted or merged.

Usage:
    # Report only, change nothing
    python link_versions.py --corpus /storage/poster_corpus --dry-run

    # Link in place, reading version graphs from the raw harvest
    python link_versions.py --corpus /storage/poster_corpus \\
        --raw /storage/harvest/zenodo.ndjson --raw /storage/harvest/figshare.ndjson

    # Write to a new tree instead of in place
    python link_versions.py --corpus ./corpus --out ./corpus_linked
"""

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from poster_to_json import version_linking  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("link_versions")


def _iter_raw(paths):
    """Yield raw harvested records from ndjson or json-array files."""
    for path in paths:
        p = Path(path)
        if not p.exists():
            logger.warning("raw metadata not found: %s", p)
            continue
        text_head = p.open("r", encoding="utf-8").read(1)
        if text_head == "[":
            with p.open("r", encoding="utf-8") as fh:
                for rec in json.load(fh):
                    yield rec
            continue
        with p.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("skipping unparseable line in %s", p)


def build_raw_index(paths):
    """Map normalized DOI -> VersionFamily, from raw Zenodo/Figshare records."""
    index = {}
    counts = Counter()
    for rec in _iter_raw(paths):
        if not isinstance(rec, dict):
            continue
        if rec.get("conceptrecid") or (rec.get("metadata") or {}).get("relations"):
            family = version_linking.from_zenodo(rec)
            source = "zenodo"
        elif "defined_type" in rec or "url_public_api" in rec:
            family = version_linking.from_figshare(rec)
            source = "figshare"
        else:
            continue
        if not family or not family.own_doi:
            counts[f"{source}:no-family"] += 1
            continue
        index[family.own_doi] = family
        counts[f"{source}:indexed"] += 1
    if counts:
        logger.info("raw index: %s", dict(counts))
    return index


def _own_doi(poster_json):
    """The poster's own DOI, from identifiers[]."""
    for ident in poster_json.get("identifiers") or []:
        if not isinstance(ident, dict):
            continue
        if str(ident.get("identifierType", "")).upper() == "DOI":
            doi = version_linking._normalize_doi(ident.get("identifier"))
            if doi:
                return doi
    return ""


def _family_from_existing(poster_json, doi):
    """Rebuild a VersionFamily from versionInfo written at conversion time."""
    info = poster_json.get("versionInfo")
    if not isinstance(info, dict) or not info.get("versionRoot"):
        return None
    sequence = info.get("versionSequence")
    if not isinstance(sequence, int) or sequence < 1:
        return None
    return version_linking.VersionFamily(
        root=info["versionRoot"],
        root_type=info.get("versionRootType", "Other"),
        sequence=sequence,
        is_latest=bool(info.get("isLatestVersion", True)),
        own_doi=doi,
        source=info.get("versionSource", "existing"),
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", required=True, help="directory of poster .json files")
    ap.add_argument("--raw", action="append", default=[],
                    help="raw harvested metadata (ndjson or json array); repeatable")
    ap.add_argument("--out", help="write to this tree instead of in place")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    ap.add_argument("--report", help="write a CSV of every linked family here")
    args = ap.parse_args()

    corpus = Path(args.corpus)
    if not corpus.is_dir():
        ap.error(f"--corpus is not a directory: {corpus}")

    raw_index = build_raw_index(args.raw) if args.raw else {}

    files = sorted(corpus.rglob("*.json"))
    logger.info("scanning %d poster files", len(files))

    items = []
    stats = Counter()
    for path in files:
        try:
            poster_json = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            stats["unreadable"] += 1
            continue
        if not isinstance(poster_json, dict):
            stats["not-an-object"] += 1
            continue

        doi = _own_doi(poster_json)
        family = raw_index.get(doi) if doi else None
        if family:
            stats["family-from-raw"] += 1
        else:
            family = _family_from_existing(poster_json, doi)
            if family:
                stats["family-from-versioninfo"] += 1
        if not family:
            stats["no-version-signal"] += 1
            continue

        items.append({"path": path, "family": family, "poster_json": poster_json,
                      "before": json.dumps(poster_json, sort_keys=True)})

    link_stats = version_linking.link_families(items)

    written = 0
    multi = []
    for item in items:
        after = json.dumps(item["poster_json"], sort_keys=True)
        family = item["family"]
        if (item["poster_json"].get("versionInfo") or {}).get("versionCount", 1) > 1:
            multi.append(item)
        if after == item["before"]:
            continue
        written += 1
        if args.dry_run:
            continue
        if args.out:
            dest = Path(args.out) / item["path"].relative_to(corpus)
            dest.parent.mkdir(parents=True, exist_ok=True)
        else:
            dest = item["path"]
        dest.write_text(
            json.dumps(item["poster_json"], indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    print()
    print(f"poster files scanned      : {len(files)}")
    for key in sorted(stats):
        print(f"  {key:<24}: {stats[key]}")
    print(f"records with a family     : {link_stats['records']}")
    print(f"distinct families         : {link_stats['families']}")
    print(f"families with >1 version  : {link_stats['multi_version_families']}")
    print(f"records in those families : {len(multi)}")
    print(f"files {'that would change' if args.dry_run else 'written'}       : {written}")

    if multi:
        print()
        print("Multi-version families (sequence, latest, doi):")
        by_root = {}
        for item in multi:
            by_root.setdefault(item["family"].root, []).append(item)
        for root in sorted(by_root)[:25]:
            members = sorted(by_root[root], key=lambda m: m["family"].sequence)
            print(f"  {root}")
            for m in members:
                f = m["family"]
                flag = "latest" if f.is_latest else "      "
                print(f"    v{f.sequence:<3} {flag}  {f.own_doi or '(no doi)'}  [{f.source}]")
        if len(by_root) > 25:
            print(f"  ... and {len(by_root) - 25} more families")

    if args.report:
        lines = ["versionRoot,versionSequence,isLatestVersion,doi,versionSource,file"]
        for item in sorted(multi, key=lambda m: (m["family"].root, m["family"].sequence)):
            f = item["family"]
            lines.append(
                f'"{f.root}",{f.sequence},{str(f.is_latest).lower()},'
                f'"{f.own_doi}","{f.source}","{item["path"]}"'
            )
        Path(args.report).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\nreport written to {args.report}")

    if args.dry_run:
        print("\nDry run. Nothing was written.")


if __name__ == "__main__":
    main()
