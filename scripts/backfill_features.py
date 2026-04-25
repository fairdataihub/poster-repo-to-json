#!/usr/bin/env python3
"""
Backfill already-extracted JSONs with newer poster2json features.

Applies (idempotently) to each output file:
- SPDX license normalization (normalize.normalize_rights_list)
- Subject dedupe/cleanup (normalize.normalize_subjects)
- ROR enrichment on creators.affiliation and publisher
- Heuristic language detection from cached raw text
- researchField placeholder strip (drop "Other", empty, etc.)
- NFKC normalization on string fields

Reuses cached raw text from `_raw_text/{stem}.json` for language detection.

IMPORTANT: researchField ALREADY set to a valid OpenAlex domain is kept.
If missing or placeholder, we set to None (re-extraction can fill it later).

Usage:
    POSTER2JSON_PATH=/path/to/poster2json \\
    python scripts/backfill_features.py --extractions ./output/extractions
"""
import argparse
import json
import os
import sys
import unicodedata
from pathlib import Path

from tqdm import tqdm

PLACEHOLDER_RF = {
    "", "other", "unknown", "n/a", "na", "none",
    "research field", "domain", "field",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--extractions", required=True,
                        help="Directory of *_extracted.json files to backfill")
    parser.add_argument("--poster2json-path", default=os.environ.get("POSTER2JSON_PATH"),
                        help="Path to poster2json repo (for normalize/ror/language imports)")
    parser.add_argument("--skip-ror", action="store_true",
                        help="Skip ROR lookups (network-dependent, slow)")
    parser.add_argument("--skip-language", action="store_true",
                        help="Skip language detection")
    args = parser.parse_args()

    if args.poster2json_path:
        sys.path.insert(0, args.poster2json_path)

    from poster2json.normalize import (
        normalize_rights_list,
        normalize_subjects,
    )

    ror = None
    if not args.skip_ror:
        from poster2json.ror import (
            enrich_persons,
            enrich_publisher,
            get_default_client,
        )
        ror = get_default_client()

    detect_language = None
    if not args.skip_language:
        from poster2json.language import detect_language

    ext_dir = Path(args.extractions)
    raw_cache_dir = ext_dir / "_raw_text"

    files = sorted(ext_dir.glob("*_extracted.json"))
    print(f"Backfilling {len(files)} files...")

    stats = {
        "updated": 0,
        "skipped_error": 0,
        "rights_normalized": 0,
        "subjects_normalized": 0,
        "ror_affiliations": 0,
        "ror_publisher": 0,
        "language_set": 0,
        "rf_cleared": 0,
        "errors": 0,
    }

    for f in tqdm(files, unit="file"):
        try:
            data = json.loads(f.read_text())
        except Exception:
            stats["errors"] += 1
            continue

        if "error" in data:
            stats["skipped_error"] += 1
            continue

        changed = False

        # rightsList -> SPDX
        if "rightsList" in data and isinstance(data["rightsList"], list):
            old = data["rightsList"]
            new = normalize_rights_list(old)
            if new != old:
                data["rightsList"] = new
                stats["rights_normalized"] += 1
                changed = True

        # subjects dedupe/cleanup
        if "subjects" in data and isinstance(data["subjects"], list):
            old = data["subjects"]
            new = normalize_subjects(old)
            if new != old:
                data["subjects"] = new
                stats["subjects_normalized"] += 1
                changed = True

        # ROR enrichment
        if ror is not None:
            if "creators" in data and isinstance(data["creators"], list):
                before = json.dumps(data["creators"], sort_keys=True)
                enrich_persons(data["creators"], ror)
                after = json.dumps(data["creators"], sort_keys=True)
                if before != after:
                    stats["ror_affiliations"] += 1
                    changed = True
            if "contributors" in data and isinstance(data["contributors"], list):
                enrich_persons(data["contributors"], ror)
            if "publisher" in data and isinstance(data["publisher"], dict):
                before = json.dumps(data["publisher"], sort_keys=True)
                data["publisher"] = enrich_publisher(data["publisher"], ror)
                after = json.dumps(data["publisher"], sort_keys=True)
                if before != after:
                    stats["ror_publisher"] += 1
                    changed = True

        # Language detection from cached raw text
        if detect_language is not None:
            stem = f.stem.replace("_extracted", "")
            cache = raw_cache_dir / f"{stem}.json"
            if cache.exists():
                try:
                    raw = json.loads(cache.read_text()).get("text", "")
                except Exception:
                    raw = ""
                if raw:
                    detected = detect_language(raw)
                    if data.get("language") != detected:
                        data["language"] = detected
                        stats["language_set"] += 1
                        changed = True

        # researchField placeholder strip
        rf = data.get("researchField")
        if isinstance(rf, str) and rf.strip().lower() in PLACEHOLDER_RF:
            data["researchField"] = None
            stats["rf_cleared"] += 1
            changed = True

        if changed:
            # NFKC normalize string fields we just touched (titles, descriptions)
            for key in ("titles", "descriptions"):
                if key in data and isinstance(data[key], list):
                    for item in data[key]:
                        if isinstance(item, dict):
                            for k, v in list(item.items()):
                                if isinstance(v, str):
                                    item[k] = unicodedata.normalize("NFKC", v)

            f.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            stats["updated"] += 1

    print("\n=== BACKFILL SUMMARY ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
