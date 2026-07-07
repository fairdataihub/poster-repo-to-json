#!/usr/bin/env python3
"""Sync the bundled poster schema + _SCHEMA_URL from the authoritative
poster-json-schema repo, so the version is never hand-hardcoded.

Copies <schema-repo>/poster_schema.json over src/poster_to_json/poster_schema.json
and rewrites the _SCHEMA_URL constant in field_normalize.py to the schema's own $id.
Run whenever poster-json-schema is updated.

Usage:
    python scripts/sync_schema.py --schema-repo /path/to/poster-json-schema [--check]
"""
import argparse
import json
import re
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src" / "poster_to_json"
_BUNDLE = _SRC / "poster_schema.json"
_FIELD_NORMALIZE = _SRC / "field_normalize.py"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--schema-repo", required=True,
                    help="path to the poster-json-schema checkout")
    ap.add_argument("--check", action="store_true",
                    help="report drift and exit non-zero if out of sync; do not write")
    args = ap.parse_args()

    src = Path(args.schema_repo) / "poster_schema.json"
    if not src.exists():
        sys.exit(f"schema not found: {src}")
    repo_bytes = src.read_bytes()
    schema_id = json.loads(repo_bytes).get("$id")
    if not schema_id:
        sys.exit("authoritative schema has no $id")

    bundle_drift = (not _BUNDLE.exists()) or _BUNDLE.read_bytes() != repo_bytes
    fn_text = _FIELD_NORMALIZE.read_text(encoding="utf-8")
    cur = re.search(r'_SCHEMA_URL = "([^"]*)"', fn_text)
    url_drift = not cur or cur.group(1) != schema_id

    if args.check:
        print(f"$id={schema_id} | bundle_drift={bundle_drift} | url_drift={url_drift}")
        sys.exit(1 if (bundle_drift or url_drift) else 0)

    if bundle_drift:
        _BUNDLE.write_bytes(repo_bytes)
        print(f"synced bundle <- {src}")
    if url_drift:
        fn_text = re.sub(r'_SCHEMA_URL = "[^"]*"', f'_SCHEMA_URL = "{schema_id}"', fn_text, count=1)
        _FIELD_NORMALIZE.write_text(fn_text, encoding="utf-8")
        print(f"updated _SCHEMA_URL -> {schema_id}")
    if not (bundle_drift or url_drift):
        print("already in sync")


if __name__ == "__main__":
    main()
