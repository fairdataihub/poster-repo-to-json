#!/usr/bin/env python3
"""
Targeted ROR re-enrichment for files missing ROR affiliations.

Runs only the ROR enrichment steps from _postprocess_json on files
that don't already have ROR identifiers. Much faster than re-running
the full pipeline.

Usage:
    python ror_reenrich.py --extractions /home/james/corpus_output/extractions
"""
import argparse
import json
import sys
from pathlib import Path

from tqdm import tqdm

sys.path.insert(0, "/home/james/poster2json")

from poster2json.ror import enrich_persons, enrich_publisher, get_default_client


def file_has_ror(data: dict) -> bool:
    for creator in data.get("creators", []):
        if not isinstance(creator, dict):
            continue
        for aff in creator.get("affiliation", []):
            if isinstance(aff, dict) and aff.get("affiliationIdentifierScheme") == "ROR":
                return True
    pub = data.get("publisher")
    if isinstance(pub, dict) and pub.get("publisherIdentifierScheme") == "ROR":
        return True
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--extractions", required=True)
    args = parser.parse_args()

    ext_dir = Path(args.extractions)
    ext_files = sorted(ext_dir.glob("*_extracted.json"))
    print(f"Found {len(ext_files)} extraction files")

    ror_client = get_default_client()
    if not ror_client.enabled:
        print("ROR client is disabled (POSTER2JSON_ROR=0 or API failure). Exiting.")
        sys.exit(1)

    needs_ror = []
    skipped_errors = 0
    already_has = 0

    print("Scanning for files without ROR...")
    for f in tqdm(ext_files, desc="Scanning", unit="file"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if "error" in data:
                skipped_errors += 1
                continue
            if file_has_ror(data):
                already_has += 1
                continue
            has_affiliations = False
            for c in data.get("creators", []):
                if isinstance(c, dict) and c.get("affiliation"):
                    has_affiliations = True
                    break
            if has_affiliations:
                needs_ror.append((f, data))
        except Exception:
            pass

    print(f"  {already_has} already have ROR, {len(needs_ror)} need enrichment, {skipped_errors} error files")

    if not needs_ror:
        print("Nothing to do.")
        return

    enriched = 0
    errors = 0
    for f, data in tqdm(needs_ror, desc="ROR enrichment", unit="poster"):
        try:
            if not ror_client.enabled:
                print("\nROR client disabled (API failure). Stopping.")
                break

            creators = data.get("creators", [])
            contributors = data.get("contributors", [])
            enrich_persons(creators, ror_client)
            enrich_persons(contributors, ror_client)

            pub = data.get("publisher")
            if isinstance(pub, dict):
                enrich_publisher(pub, ror_client)

            f.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            enriched += 1
        except Exception as e:
            errors += 1
            tqdm.write(f"Error on {f.name}: {e}")

    ror_client._save_cache()
    print(f"\nDone: {enriched} enriched, {errors} errors")
    print(f"ROR cache: {len(ror_client._cache)} entries")


if __name__ == "__main__":
    main()
