#!/usr/bin/env python3
"""
Link repository version families across an existing poster corpus.

Per-record conversion can only see one deposit at a time, so it records the
family key and the position but cannot point a record at its siblings. This
script runs over a whole corpus, groups records into families, and writes the
sibling cross-links plus the settled isLatestVersion flag.

It reads the family signals from the raw harvested metadata (--raw), because
Zenodo's relations.version graph is the only place the true sequence and the
is_last flag live. --raw is required: the poster JSON carries the resulting
relations but not the sequence they were derived from, so a corpus cannot be
relinked from its own output.

Only fields the poster schema already defines are written: relatedIdentifiers
entries using the DataCite version relations. Nothing else is touched, and the
depositor's own `version` string is left exactly as it is.

Every version is kept. Nothing is deleted or merged.

Usage:
    # Report only, change nothing
    python link_versions.py --corpus /storage/poster-work/pre2025/merged --dry-run

    # Link in place, reading version graphs from the raw harvest
    python link_versions.py --corpus /storage/poster-work/pre2025/merged \\
        --raw /storage/poster-work/pre2025/metadata

    # Write to a new tree instead of in place
    python link_versions.py --corpus ./corpus --out ./corpus_linked

Pass every corpus slice that could share a family in one run. A poster deposited
in 2024 and revised in 2025 has its two versions in different harvest batches,
so linking the batches separately would never connect them:

    python link_versions.py \\
        --corpus /storage/poster-work/pre2025/merged \\
        --corpus /storage/poster-work/data2025/merged \\
        --raw /storage/poster-work/pre2025/metadata \\
        --raw /storage/poster-work/data2025/metadata
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
    """Yield raw harvested records.

    Accepts a directory of one-record-per-file JSON (how the harvest is laid out
    on disk, e.g. metadata/zenodo/13341161.json), an ndjson/jsonl file, or a
    file holding a JSON array.
    """
    for path in paths:
        p = Path(path)
        if not p.exists():
            logger.warning("raw metadata not found: %s", p)
            continue

        if p.is_dir():
            count = 0
            for f in p.rglob("*.json"):
                try:
                    rec = json.loads(f.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                    logger.warning("skipping unreadable raw file %s", f)
                    continue
                if isinstance(rec, list):
                    for r in rec:
                        yield r
                        count += 1
                else:
                    yield rec
                    count += 1
            logger.info("read %d raw records from %s", count, p)
            continue

        with p.open("r", encoding="utf-8") as fh:
            head = fh.read(1)
        if head == "[":
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


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", action="append", required=True,
                    help="directory of poster .json files; repeatable, and every "
                         "slice that could share a version family must be in the "
                         "same run")
    ap.add_argument("--raw", action="append", default=[], required=True,
                    help="raw harvested metadata: a directory of per-record JSON, "
                         "an ndjson file, or a JSON array file; repeatable. Required, "
                         "because the version graph exists only in the raw harvest")
    ap.add_argument("--out", help="write to this tree instead of in place")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    ap.add_argument("--report", help="write a CSV of every linked family here")
    args = ap.parse_args()

    roots = []
    for c in args.corpus:
        root = Path(c)
        if not root.is_dir():
            ap.error(f"--corpus is not a directory: {root}")
        roots.append(root)

    raw_index = build_raw_index(args.raw)

    files = []
    for root in roots:
        found = sorted(root.rglob("*.json"))
        logger.info("%s: %d poster files", root, len(found))
        files.extend((root, f) for f in found)
    logger.info("scanning %d poster files across %d corpus roots", len(files), len(roots))

    items = []
    stats = Counter()
    for root, path in files:
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
        if not family:
            stats["no-version-signal"] += 1
            continue
        stats["family-from-raw"] += 1

        items.append({"path": path, "root": root, "family": family,
                      "poster_json": poster_json,
                      "before": json.dumps(poster_json, sort_keys=True)})

    link_stats = version_linking.link_families(items)

    # How many distinct versions of each family the corpus holds. Files sharing a
    # DOI are one version present twice, not two versions.
    family_versions = {}
    for item in items:
        f = item["family"]
        family_versions.setdefault(f.group_key, set()).add(f.own_doi)

    written = 0
    multi = []
    for item in items:
        after = json.dumps(item["poster_json"], sort_keys=True)
        if len(family_versions[item["family"].group_key]) > 1:
            multi.append(item)
        if after == item["before"]:
            continue
        written += 1
        if args.dry_run:
            continue
        if args.out:
            # Keep the corpus roots apart under --out so same-named files in
            # different slices cannot overwrite each other.
            root = item["root"]
            prefix = root.name if len(roots) > 1 else ""
            dest = Path(args.out) / prefix / item["path"].relative_to(root)
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
            by_root.setdefault(item["family"].root_doi or item["family"].group_key, []).append(item)
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
        for item in sorted(multi, key=lambda m: (m["family"].group_key, m["family"].sequence)):
            f = item["family"]
            lines.append(
                f'"{f.root_doi or f.group_key}",{f.sequence},{str(f.is_latest).lower()},'
                f'"{f.own_doi}","{f.source}","{item["path"]}"'
            )
        Path(args.report).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\nreport written to {args.report}")

    if args.dry_run:
        print("\nDry run. Nothing was written.")


if __name__ == "__main__":
    main()
