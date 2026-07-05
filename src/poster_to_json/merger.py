#!/usr/bin/env python3
"""
Metadata merger — combines poster2json extraction with repository metadata.

poster2json output is the base for poster-content fields (sections, captions,
research field) and for resolved identifiers it discovers (ORCID, ROR).

Repository metadata (Zenodo/Figshare) is authoritative for fields the depositor
curated: the author list and ordering (creators), the deposit description (the
primary Abstract), funding/grants, publication year, dates, and rights. For
these, repository metadata wins when present; poster2json only ENRICHES
repository creators with identifiers it resolved (ORCID, ROR, nameType) and
contributes its LLM summary as a secondary description. When the repository has
nothing for a field, the extraction value is kept as a backfill.
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from tqdm import tqdm

from .date_normalize import normalize_record_dates
from .field_normalize import (
    normalize_conference, normalize_publisher, normalize_subjects,
    normalize_creators, normalize_formats, resolve_lumped_creators,
    creator_addable_to_union, creator_surnames, name_tokens, dedup_creators,
    normalize_lumped_creators, align_schema, ensure_presented_date,
    reconcile_publication_year, sanitize_conference_dates, strip_invalid_dates,
)

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


_PLACEHOLDER_STRINGS = frozenset({
    "not specified", "unknown", "n/a", "na", "none", "", "untitled poster",
    "scientific poster", "all rights reserved",
    "name of conference", "conference name", "city, country",
    "conference organizer or institution name", "institution name",
    "yyyy-mm-dd", "yyyy", "http://example.com", "https://example.com",
    "conference url", "poster title", "main poster title",
})


def _is_placeholder(value) -> bool:
    """Check if a value is a placeholder injected by SchemaConverter defaults."""
    if isinstance(value, str):
        return value.strip().lower() in _PLACEHOLDER_STRINGS
    if isinstance(value, int):
        return value == datetime.now().year
    if isinstance(value, dict):
        vals = [v for v in value.values() if v is not None]
        return bool(vals) and all(_is_placeholder(v) for v in vals)
    return False


class MetadataMerger:
    """
    Merge poster2json extraction output with repository metadata.

    poster2json output is the base. Repository metadata only fills gaps.
    """

    EXTRACTION_ONLY_FIELDS = [
        "content",
        "imageCaptions",
        "tableCaptions",
        "researchField",
        "_validation",
    ]

    METADATA_AUTHORITATIVE_FIELDS = frozenset({
        "publicationYear",
        "rightsList",
        "dates",
        "fundingReferences",
    })

    # Fields the extraction must NEVER contribute. The license/rights of a
    # poster come only from the repository deposit (or the platform downstream),
    # never from the LLM reading the poster. If the deposit declares no license,
    # any extraction-fabricated rightsList is dropped rather than kept — older
    # extractions misfiled funding/acknowledgements/citations into rights.
    DEPOSIT_ONLY_FIELDS = frozenset({
        "rightsList",
    })

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
        "version",
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
                # Deposit offers nothing. For deposit-only fields, also remove
                # any extraction value so a fabricated license can't survive.
                if field in self.DEPOSIT_ONLY_FIELDS:
                    result.pop(field, None)
                continue  # metadata has nothing to offer

            if field in self.METADATA_AUTHORITATIVE_FIELDS:
                result[field] = meta_val
            elif _is_empty(ext_val) or _is_placeholder(ext_val):
                result[field] = meta_val
            elif field == "creators":
                result["creators"] = self._enrich_creators(ext_val, meta_val)
            elif field == "subjects":
                result["subjects"] = self._merge_subjects(ext_val, meta_val)
            elif field == "identifiers":
                result["identifiers"] = self._clean_identifiers(ext_val, meta_val, result)
            elif field == "descriptions":
                result["descriptions"] = self._merge_descriptions(ext_val, meta_val)
            elif field == "conference":
                result["conference"] = self._merge_conference(ext_val, meta_val)

        self._strip_metadata_placeholders(result)
        normalize_record_dates(result)
        strip_invalid_dates(result)
        normalize_conference(result)
        normalize_publisher(result)
        normalize_subjects(result)
        normalize_lumped_creators(result)
        normalize_creators(result)
        dedup_creators(result)
        normalize_formats(result)
        align_schema(result)
        reconcile_publication_year(result)
        sanitize_conference_dates(result)
        ensure_presented_date(result)

        return result

    @staticmethod
    def _ensure_presented_date(result: Dict):
        """If conference has dates, ensure a Presented entry exists in dates[]."""
        conf = result.get("conference")
        if not isinstance(conf, dict):
            return

        start = conf.get("conferenceStartDate")
        if not start or _is_placeholder(start):
            return

        end = conf.get("conferenceEndDate")
        presented_date = f"{start}/{end}" if (end and not _is_placeholder(end)) else start

        dates = result.get("dates", [])
        for d in dates:
            if isinstance(d, dict) and d.get("dateType") == "Presented":
                return
        dates.append({"date": presented_date, "dateType": "Presented"})
        result["dates"] = dates

    @staticmethod
    def _strip_metadata_placeholders(result: Dict):
        """Remove placeholder values that leaked from SchemaConverter defaults."""
        for field in ("fundingReferences", "conference", "rightsList",
                      "descriptions", "identifiers", "creators", "titles",
                      "subjects"):
            val = result.get(field)
            if isinstance(val, list):
                result[field] = [
                    v for v in val
                    if not (isinstance(v, dict) and _is_placeholder(v))
                ]
                if not result[field]:
                    del result[field]
            elif isinstance(val, dict) and _is_placeholder(val):
                del result[field]

    @staticmethod
    def _name_key(name: str) -> str:
        """Order-insensitive normalized key for matching the same author across sources.

        Extraction often yields "Given Family" while repositories yield
        "Family, Given"; sorting the tokens lets both forms match.
        """
        cleaned = (name or "").lower().replace(",", " ").replace(".", " ")
        tokens = [t for t in cleaned.split() if t]
        return " ".join(sorted(tokens))

    def _enrich_creators(self, ext_creators: List, meta_creators: List) -> List:
        """Repository creators are authoritative for names and ordering.

        The depositor curated the author list, so the repository (Zenodo/
        Figshare) creators are the base: their names, order, nameType and
        affiliation text are kept as-is. poster2json extraction only ENRICHES
        each repository creator with identifiers it resolved that the deposit
        is missing — ORCID (nameIdentifiers), ROR (affiliationIdentifier) and,
        as a last resort, nameType. It never overwrites a curated value.
        """
        if not meta_creators:
            return ext_creators
        if not ext_creators:
            return meta_creators

        # Exception to deposit-authority: when the depositor crammed every author
        # into a single name field but the extraction cleanly separated them,
        # prefer the extraction's split authors.
        resolved = resolve_lumped_creators(meta_creators, ext_creators)
        if resolved is not meta_creators:
            return resolved

        # Index extraction creators by normalized name for enrichment lookup
        ext_by_name = {}
        for ec in ext_creators:
            if isinstance(ec, dict) and ec.get("name"):
                ext_by_name[self._name_key(ec["name"])] = ec

        enriched = []
        for mc in meta_creators:
            if not isinstance(mc, dict):
                enriched.append(mc)
                continue

            creator = dict(mc)
            match = ext_by_name.get(self._name_key(creator.get("name", "")))
            if match:
                # ORCID / other identifiers — only when the deposit has none
                if not creator.get("nameIdentifiers") and match.get("nameIdentifiers"):
                    creator["nameIdentifiers"] = match["nameIdentifiers"]
                # nameType — only when the deposit left it unset
                if not creator.get("nameType") and match.get("nameType"):
                    creator["nameType"] = match["nameType"]
                # Affiliation: backfill if the deposit has none, otherwise graft
                # ROR identifiers onto the deposit's affiliation text.
                creator["affiliation"] = self._enrich_affiliations(
                    creator.get("affiliation"), match.get("affiliation")
                )

            enriched.append(creator)

        # Union: append extraction authors the deposit is missing. An extraction
        # author is skipped if a known surname already appears in its name, so
        # the same person isn't double-added under a different form (initials/
        # accents/order).
        surnames = set()
        for mc in meta_creators:
            if isinstance(mc, dict):
                surnames |= creator_surnames(mc.get("name", ""))
        for ec in ext_creators:
            if not creator_addable_to_union(ec):
                continue
            if surnames & name_tokens(ec.get("name", "")):
                continue
            surnames |= creator_surnames(ec.get("name", ""))
            enriched.append(ec)

        return enriched

    def _enrich_affiliations(self, meta_affs, ext_affs):
        """Keep repository affiliation text; graft ROR identifiers from extraction.

        Repository affiliation text wins. If the repository has no affiliation,
        backfill from extraction. Where names match and the repository entry
        lacks a ROR id, copy the affiliationIdentifier resolved by poster2json.
        """
        if not meta_affs:
            return ext_affs if ext_affs else meta_affs
        if not ext_affs:
            return meta_affs

        # Index extraction affiliations that carry a resolved identifier
        ext_ror = {}
        for ea in ext_affs:
            if isinstance(ea, dict) and ea.get("affiliationIdentifier") and ea.get("name"):
                ext_ror[ea["name"].strip().lower()] = ea

        out = []
        for ma in meta_affs:
            if not isinstance(ma, dict):
                out.append(ma)
                continue
            aff = dict(ma)
            if not aff.get("affiliationIdentifier"):
                m = ext_ror.get(aff.get("name", "").strip().lower())
                if m:
                    aff["affiliationIdentifier"] = m["affiliationIdentifier"]
                    if m.get("affiliationIdentifierScheme"):
                        aff["affiliationIdentifierScheme"] = m["affiliationIdentifierScheme"]
                    if m.get("schemeURI") or m.get("schemeUri"):
                        aff["schemeURI"] = m.get("schemeURI") or "https://ror.org"
            out.append(aff)
        return out

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

    def _clean_identifiers(self, ext_ids: List, meta_ids: List, result: Dict) -> List:
        """Use metadata identifiers as authoritative; move extraction DOIs to relatedIdentifiers.

        The poster's real DOI and record ID come from metadata (Zenodo/Figshare).
        DOIs found by poster2json in the poster text are reference citations,
        not the poster's own identifiers — they belong in relatedIdentifiers.
        """
        # Metadata identifiers are the real ones
        real_ids = list(meta_ids or [])
        real_id_values = {i.get("identifier", "").lower() for i in real_ids}

        # Any extraction identifiers NOT already in metadata are likely references
        ref_ids = []
        for eid in (ext_ids or []):
            if isinstance(eid, dict) and eid.get("identifier"):
                if eid["identifier"].lower() not in real_id_values:
                    ref_ids.append({
                        "relatedIdentifier": eid["identifier"],
                        "relatedIdentifierType": eid.get("identifierType", "DOI"),
                        "relationType": "References",
                    })

        # Add reference DOIs to relatedIdentifiers
        if ref_ids:
            existing_related = result.get("relatedIdentifiers", [])
            existing_vals = {r.get("relatedIdentifier", "").lower() for r in existing_related}
            for r in ref_ids:
                if r["relatedIdentifier"].lower() not in existing_vals:
                    existing_related.append(r)
            result["relatedIdentifiers"] = existing_related

        return real_ids if real_ids else (ext_ids or [])

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

    def _merge_conference(self, ext_conf, meta_conf) -> Dict:
        """Merge conference info -- metadata supersedes extraction.

        Repository metadata (Zenodo/Figshare) is authoritative for conference
        details because the uploader explicitly provided this information.
        The LLM extraction often guesses or hallucinates conference names/dates.

        Strategy: start with metadata, backfill missing fields from extraction.
        """
        if isinstance(ext_conf, str):
            ext_conf = {"conferenceName": ext_conf} if ext_conf.strip() else {}
        if isinstance(meta_conf, str):
            meta_conf = {"conferenceName": meta_conf} if meta_conf.strip() else {}

        if not meta_conf or _is_placeholder(meta_conf.get("conferenceName", "")):
            return ext_conf or {}

        if not ext_conf:
            return meta_conf

        # Start with metadata as base (authoritative)
        result = dict(meta_conf)

        # Backfill any fields metadata is missing from extraction
        for key, val in ext_conf.items():
            if key not in result or _is_empty(result[key]) or _is_placeholder(result.get(key, "")):
                if not _is_empty(val) and not _is_placeholder(val):
                    result[key] = val

        return result

    def _merge_descriptions(self, ext_descs: List, meta_descs: List) -> List:
        """Repository deposit description is the primary Abstract.

        The depositor's own description (Zenodo/Figshare) leads as the
        authoritative Abstract. The poster2json LLM-generated summary is
        retained, but demoted to a secondary description of type "Other" so
        it remains searchable without competing with the curated abstract.

        This method only runs when the repository has a description; if the
        deposit has none, the merge loop keeps the extraction summary as-is
        (still typed Abstract), so a missing deposit abstract is not lost.
        """
        def _key(d):
            return d.get("description", "").strip()[:120].lower()

        seen = set()
        merged = []

        # Repository descriptions first — the authoritative Abstract(s)
        for d in (meta_descs or []):
            if isinstance(d, dict) and d.get("description"):
                k = _key(d)
                if k and k not in seen:
                    seen.add(k)
                    merged.append(d)

        # Extraction summary kept as a secondary "Other" description
        for d in (ext_descs or []):
            if isinstance(d, dict) and d.get("description"):
                k = _key(d)
                if k and k not in seen:
                    seen.add(k)
                    secondary = dict(d)
                    secondary["descriptionType"] = "Other"
                    merged.append(secondary)

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
