#!/usr/bin/env python3
"""
Metadata merger — combines poster2json extraction with repository metadata.

poster2json output is the PRIMARY source (it extracted the actual poster).
Repository metadata (Zenodo/Figshare) only BACKFILLS fields that poster2json
left empty or missing. It never overwrites existing poster2json data.
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, List

from tqdm import tqdm

logger = logging.getLogger(__name__)


def _is_empty(value) -> bool:
    """Check if a value is effectively empty."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def _is_placeholder(value) -> bool:
    """Check if a value is a placeholder like 'Not specified'."""
    if isinstance(value, str):
        return value.strip().lower() in ("not specified", "unknown", "n/a", "")
    if isinstance(value, dict):
        vals = list(value.values())
        return all(_is_placeholder(v) for v in vals)
    return False


class MetadataMerger:
    """
    Merge poster2json extraction output with repository metadata.

    poster2json output is the base. Repository metadata only fills gaps.
    """

    # Fields that poster2json extracts from the poster itself.
    # These are NEVER overwritten by repo metadata.
    EXTRACTION_ONLY_FIELDS = [
        "content",
        "imageCaptions",
        "tableCaptions",
    ]

    # Fields where repo metadata is typically more authoritative
    # (DOIs, publication dates, licenses, funding) — but still
    # only used to FILL missing fields, not replace.
    METADATA_FIELDS = [
        "$schema",
        "identifiers",
        "creators",
        "titles",
        "publisher",
        "publicationYear",
        "dates",
        "types",
        "descriptions",
        "subjects",
        "language",
        "formats",
        "rightsList",
        "conference",
        "fundingReferences",
        "relatedIdentifiers",
        "researchField",
    ]

    def merge(self, extraction: Dict, metadata: Dict) -> Dict:
        """
        Merge poster2json extraction with repository metadata.

        The extraction dict is the BASE. For every field in the schema,
        if the extraction has a non-empty value, keep it. Only backfill
        from metadata when the extraction field is missing or empty.

        Args:
            extraction: poster2json output (primary source)
            metadata: Converted repository metadata (Zenodo/Figshare)

        Returns:
            Merged record
        """
        # Start with the extraction as the base
        result = dict(extraction)

        # Backfill from metadata: only add fields that are missing or empty
        for field in self.METADATA_FIELDS:
            ext_val = result.get(field)
            meta_val = metadata.get(field)

            if meta_val is None or _is_empty(meta_val):
                continue  # metadata has nothing to offer

            if _is_empty(ext_val) or _is_placeholder(ext_val):
                # Extraction is missing this field — use metadata
                result[field] = meta_val
            elif field == "creators":
                # Special: enrich extraction creators with metadata affiliations/ORCIDs
                result["creators"] = self._enrich_creators(ext_val, meta_val)
            elif field == "subjects":
                # Special: merge subjects (union, deduped)
                result["subjects"] = self._merge_subjects(ext_val, meta_val)
            elif field == "identifiers":
                # Special: merge identifiers (DOIs from metadata are authoritative)
                result["identifiers"] = self._merge_identifiers(ext_val, meta_val)
            elif field == "descriptions":
                # Special: keep extraction descriptions, add metadata ones if different
                result["descriptions"] = self._merge_descriptions(ext_val, meta_val)
            # Otherwise: extraction has a value — keep it, don't overwrite

        return result

    def _enrich_creators(self, ext_creators: List, meta_creators: List) -> List:
        """Enrich extraction creators with ORCIDs/affiliations from metadata."""
        if not ext_creators:
            return meta_creators
        if not meta_creators:
            return ext_creators

        # Index metadata creators by normalized name
        meta_by_name = {}
        for mc in meta_creators:
            if isinstance(mc, dict) and mc.get("name"):
                key = mc["name"].lower().strip().replace(",", "").replace(".", "")
                meta_by_name[key] = mc

        enriched = []
        for ec in ext_creators:
            if not isinstance(ec, dict):
                enriched.append(ec)
                continue

            creator = dict(ec)
            name = creator.get("name", "")
            key = name.lower().strip().replace(",", "").replace(".", "")

            match = meta_by_name.get(key)
            if match:
                # Backfill ORCID
                if not creator.get("nameIdentifiers") and match.get("nameIdentifiers"):
                    creator["nameIdentifiers"] = match["nameIdentifiers"]
                # Backfill familyName/givenName
                if not creator.get("familyName") and match.get("familyName"):
                    creator["familyName"] = match["familyName"]
                if not creator.get("givenName") and match.get("givenName"):
                    creator["givenName"] = match["givenName"]
                # Backfill affiliation only if extraction has none
                if not creator.get("affiliation") and match.get("affiliation"):
                    creator["affiliation"] = match["affiliation"]

            enriched.append(creator)

        return enriched

    def _merge_subjects(self, ext_subjects: List, meta_subjects: List) -> List:
        """Union of subjects, case-insensitive dedup."""
        seen = set()
        merged = []
        for subj in (ext_subjects or []) + (meta_subjects or []):
            if isinstance(subj, dict) and subj.get("subject"):
                key = subj["subject"].strip().lower()
                if key not in seen:
                    seen.add(key)
                    merged.append(subj)
        return merged

    def _merge_identifiers(self, ext_ids: List, meta_ids: List) -> List:
        """Merge identifiers, preferring metadata DOIs."""
        seen = set()
        merged = []
        # Metadata identifiers first (DOIs are authoritative)
        for ident in (meta_ids or []) + (ext_ids or []):
            if isinstance(ident, dict) and ident.get("identifier"):
                key = ident["identifier"].lower().strip()
                if key not in seen:
                    seen.add(key)
                    merged.append(ident)
        return merged

    def _merge_descriptions(self, ext_descs: List, meta_descs: List) -> List:
        """Keep extraction descriptions, add unique metadata descriptions."""
        seen = set()
        merged = list(ext_descs or [])
        for d in merged:
            if isinstance(d, dict) and d.get("description"):
                seen.add(d["description"].strip()[:100].lower())

        for d in (meta_descs or []):
            if isinstance(d, dict) and d.get("description"):
                key = d["description"].strip()[:100].lower()
                if key not in seen:
                    seen.add(key)
                    merged.append(d)
        return merged

    def merge_files(self, extraction_file: str, metadata_file: str, output_file: str) -> Dict:
        """Merge extraction and metadata JSON files."""
        with open(extraction_file, "r", encoding="utf-8") as f:
            extraction = json.load(f)

        with open(metadata_file, "r", encoding="utf-8") as f:
            metadata = json.load(f)

        merged = self.merge(extraction, metadata)

        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)

        return merged

    def merge_directories(
        self,
        extraction_dir: str,
        metadata_dir: str,
        output_dir: str,
    ) -> Dict:
        """
        Merge all matching files from extraction and metadata directories.

        File matching: extraction files named like `{record_id}_extracted.json`
        match metadata files named like `{record_id}.json` or
        `{record_id}_converted.json`.
        """
        ext_path = Path(extraction_dir)
        meta_path = Path(metadata_dir)
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        # Index extraction files by record ID
        ext_files = {}
        for f in ext_path.glob("*.json"):
            if f.name == "extraction_summary.json" or f.name == "batch_results.json":
                continue
            key = f.stem
            for suffix in ["_extracted", "_extraction"]:
                key = key.replace(suffix, "")
            ext_files[key] = f

        # Index metadata files by record ID
        meta_files = {}
        for f in meta_path.glob("*.json"):
            key = f.stem
            for suffix in ["_converted", "_metadata"]:
                key = key.replace(suffix, "")
            meta_files[key] = f

        # Find matches
        matched = set(ext_files.keys()) & set(meta_files.keys())
        logger.info(f"Found {len(matched)} matching files to merge")

        stats = {"merged": 0, "extraction_only": 0, "metadata_only": 0, "errors": 0}

        for key in tqdm(sorted(matched), desc="Merging"):
            try:
                self.merge_files(
                    str(ext_files[key]),
                    str(meta_files[key]),
                    str(out_path / f"{key}_complete.json"),
                )
                stats["merged"] += 1
            except Exception as e:
                logger.error(f"Error merging {key}: {e}")
                stats["errors"] += 1

        stats["extraction_only"] = len(set(ext_files.keys()) - matched)
        stats["metadata_only"] = len(set(meta_files.keys()) - matched)

        logger.info(f"Merge complete: {stats}")
        return stats

    @staticmethod
    def validate_merged(record: Dict) -> List[str]:
        """Validate a merged record for completeness."""
        required = ["titles", "creators", "content"]
        missing = []
        for field in required:
            if field not in record or _is_empty(record[field]):
                missing.append(field)

        if "content" in record:
            pc = record["content"]
            if not isinstance(pc, dict):
                missing.append("content (invalid type)")
            elif "sections" not in pc or not pc["sections"]:
                missing.append("content.sections")

        return missing
