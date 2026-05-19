#!/usr/bin/env python3
"""
Schema converter - Converts repository metadata to posters-science JSON schema.

Supports:
- Zenodo metadata format
- Figshare metadata format
- DataCite metadata format

Output conforms to the posters-science schema (based on DataCite with poster extensions).
"""

import html
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from tqdm import tqdm

logger = logging.getLogger(__name__)

# ISO 639-2/B (3-letter) to ISO 639-1 (2-letter) mapping for common languages
_LANG3_TO_LANG2 = {
    "eng": "en", "fra": "fr", "fre": "fr", "deu": "de", "ger": "de",
    "spa": "es", "ita": "it", "por": "pt", "nld": "nl", "dut": "nl",
    "rus": "ru", "zho": "zh", "chi": "zh", "jpn": "ja", "kor": "ko",
    "ara": "ar", "hin": "hi", "pol": "pl", "tur": "tr", "swe": "sv",
    "nor": "no", "dan": "da", "fin": "fi", "hun": "hu", "ces": "cs",
    "cze": "cs", "ron": "ro", "rum": "ro", "ell": "el", "gre": "el",
    "heb": "he", "tha": "th", "vie": "vi", "ind": "id", "ukr": "uk",
    "cat": "ca", "hrv": "hr", "slk": "sk", "slo": "sk", "bul": "bg",
    "lit": "lt", "lav": "lv", "est": "et", "slv": "sl", "srp": "sr",
    "msa": "ms", "may": "ms", "fas": "fa", "per": "fa",
}


def _normalize_language(lang: str) -> Optional[str]:
    """Normalize language code to ISO 639-1 (2-letter). Returns None if unrecognized."""
    if not lang:
        return None
    lang = lang.strip().lower()
    if len(lang) == 2:
        return lang
    if len(lang) == 3:
        return _LANG3_TO_LANG2.get(lang)
    name_map = {"english": "en", "german": "de", "french": "fr", "spanish": "es"}
    return name_map.get(lang)


def _clean_html(text: str) -> str:
    """Remove HTML tags and decode HTML entities."""
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return text.strip()


def _to_family_given(full_name: str) -> str:
    """Convert 'Given Family' to 'Family, Given' format if not already."""
    if not full_name or "," in full_name:
        return full_name  # Already in "Family, Given" format
    parts = full_name.strip().split()
    if len(parts) >= 2:
        return f"{parts[-1]}, {' '.join(parts[:-1])}"
    return full_name


def _extract_year_from_text(text: str) -> Optional[int]:
    """Extract a 4-digit year from free-text like '9-10 October, 2023'."""
    match = re.search(r'\b(19\d{2}|20\d{2})\b', text)
    if match:
        return int(match.group(1))
    return None


def _strip_timestamp(date_str: str) -> str:
    """Strip time portion from ISO timestamp, returning YYYY-MM-DD."""
    if not date_str:
        return date_str
    if "T" in date_str:
        return date_str.split("T")[0]
    return date_str


def _clean_title(title: str) -> str:
    """Clean title: strip file extensions, whitespace."""
    title = title.strip()
    # Remove common file extensions from titles (Figshare often uses filenames)
    title = re.sub(r'\.(pdf|pptx?|png|jpe?g|tiff?)$', '', title, flags=re.IGNORECASE)
    return title.strip()


def load_bundled_schema() -> Dict:
    """Load the bundled poster_schema.json."""
    schema_path = Path(__file__).parent / "poster_schema.json"
    if schema_path.exists():
        with open(schema_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


class SchemaConverter:
    """Converts repository metadata to posters-science schema."""

    SCHEMA_URL = "https://posters.science/schema/v0.2/poster_schema.json"

    def __init__(self):
        """Initialize converter."""
        self._schema = None

    @property
    def schema(self) -> Dict:
        if self._schema is None:
            self._schema = load_bundled_schema()
        return self._schema

    def _ensure_required_fields(self, result: Dict) -> Dict:
        """Set universal defaults only. Missing data stays missing — no placeholders."""
        if "types" not in result:
            result["types"] = {"resourceType": "Scientific Poster", "resourceTypeGeneral": "Image"}
        if "formats" not in result or not result["formats"]:
            result["formats"] = ["PDF"]
        return result

    def convert_zenodo(self, record: Dict) -> Dict:
        """Convert Zenodo record to posters-science schema."""
        metadata = record.get("metadata", {})

        result = {
            "$schema": self.SCHEMA_URL,
        }

        # DOI and identifiers
        doi = record.get("doi")
        identifiers = []
        if doi:
            identifiers.append({"identifier": doi, "identifierType": "DOI"})

        zenodo_id = record.get("id")
        if zenodo_id:
            identifiers.append({"identifier": str(zenodo_id), "identifierType": "Other"})

        if identifiers:
            result["identifiers"] = identifiers

        # Creators (Zenodo already provides "Family, Given" format)
        creators = []
        for creator in metadata.get("creators", []):
            name = _clean_html(creator.get("name", ""))
            creator_entry = {
                "name": name,
                "nameType": "Personal",
            }
            # Split "Family, Given" if present
            if "," in name:
                parts = name.split(",", 1)
                creator_entry["familyName"] = parts[0].strip()
                creator_entry["givenName"] = parts[1].strip()

            if creator.get("affiliation"):
                creator_entry["affiliation"] = [{"name": _clean_html(creator["affiliation"])}]
            if creator.get("orcid"):
                creator_entry["nameIdentifiers"] = [{
                    "nameIdentifier": creator["orcid"],
                    "nameIdentifierScheme": "ORCID",
                }]
            creators.append(creator_entry)

        if creators:
            result["creators"] = creators

        # Title
        title = metadata.get("title")
        if title:
            result["titles"] = [{"title": _clean_title(title)}]

        # Publisher
        result["publisher"] = {"name": "Zenodo"}

        # Publication year
        pub_date = metadata.get("publication_date")
        if pub_date:
            try:
                year = int(pub_date[:4])
                result["publicationYear"] = year
            except (ValueError, TypeError):
                pass
            result["dates"] = [{"date": pub_date, "dateType": "Issued"}]

        created = record.get("created", "")
        if created:
            dates_list = result.get("dates", [])
            dates_list.append({"date": _strip_timestamp(created), "dateType": "Submitted"})
            result["dates"] = dates_list

        # Resource type
        result["types"] = {
            "resourceType": "Scientific Poster",
            "resourceTypeGeneral": "Image"
        }

        # Description/Abstract — clean HTML tags AND entities
        description = metadata.get("description")
        if description:
            clean_desc = _clean_html(description)
            if clean_desc:
                result["descriptions"] = [{
                    "description": clean_desc,
                    "descriptionType": "Abstract"
                }]

        # Keywords
        keywords = metadata.get("keywords", [])
        if keywords:
            seen = set()
            subjects = []
            for kw in keywords:
                key = kw.strip().lower()
                if key and key not in seen:
                    seen.add(key)
                    subjects.append({"subject": kw})
            if subjects:
                result["subjects"] = subjects

        # Language — normalize to ISO 639-1
        language = metadata.get("language")
        if language:
            result["language"] = _normalize_language(language)

        # License
        license_info = metadata.get("license")
        if license_info:
            result["rightsList"] = [{
                "rights": license_info.get("id", ""),
                "rightsIdentifier": license_info.get("id", ""),
            }]

        # Conference/Meeting information
        meeting = metadata.get("meeting", {})
        if meeting:
            conference = {}
            if meeting.get("title"):
                conference["conferenceName"] = meeting["title"]
            if meeting.get("acronym"):
                conference["conferenceAcronym"] = meeting["acronym"]
            if meeting.get("dates"):
                dates_str = meeting["dates"]
                # Try structured "YYYY-MM-DD - YYYY-MM-DD" first
                if " - " in dates_str:
                    parts = dates_str.split(" - ")
                    conference["conferenceStartDate"] = parts[0].strip()
                    conference["conferenceEndDate"] = parts[1].strip()
                # Extract year from any date format ("9-10 October, 2023", etc.)
                year = _extract_year_from_text(dates_str)
                if year:
                    conference["conferenceYear"] = year
            if meeting.get("place"):
                conference["conferenceLocation"] = meeting["place"]
            if meeting.get("url"):
                conference["conferenceUri"] = meeting["url"]

            if conference:
                result["conference"] = conference
                conf_start = conference.get("conferenceStartDate")
                if conf_start:
                    conf_end = conference.get("conferenceEndDate")
                    presented = f"{conf_start}/{conf_end}" if conf_end else conf_start
                    dates_list = result.get("dates", [])
                    dates_list.append({"date": presented, "dateType": "Presented"})
                    result["dates"] = dates_list

        # Funding/Grants
        grants = metadata.get("grants", [])
        if grants:
            funders = []
            for grant in grants:
                funder_entry = {}
                funder_name = (grant.get("funder", {}).get("name")
                               or grant.get("title") or "")
                funder_entry["funderName"] = funder_name
                if grant.get("title") and grant["title"] != funder_name:
                    funder_entry["awardTitle"] = grant["title"]
                if grant.get("code"):
                    funder_entry["awardNumber"] = grant["code"]
                if funder_entry:
                    funders.append(funder_entry)
            if funders:
                result["fundingReferences"] = funders

        # Related identifiers
        related = metadata.get("related_identifiers", [])
        if related:
            RELATION_MAP = {
                "issupplementto": "IsSupplementTo",
                "is_supplement_to": "IsSupplementTo",
                "issupplementedby": "IsSupplementedBy",
                "iscitedby": "IsCitedBy",
                "cites": "Cites",
                "isderivedfrom": "IsDerivedFrom",
                "issourceof": "IsSourceOf",
                "isversionof": "IsVersionOf",
                "hasversionof": "HasVersion",
                "ispartof": "IsPartOf",
                "haspart": "HasPart",
                "references": "References",
                "isreferencedby": "IsReferencedBy",
                "isdocumentedby": "IsDocumentedBy",
                "documents": "Documents",
            }

            ID_TYPE_MAP = {
                "arxiv": "arXiv",
                "doi": "DOI",
                "url": "URL",
                "urn": "URN",
                "isbn": "ISBN",
                "issn": "ISSN",
                "pmid": "PMID",
                "handle": "Handle",
            }

            valid_relations = []
            for r in related:
                if r.get("identifier"):
                    rel_type = r.get("relation", "").lower().replace("_", "").replace(" ", "")
                    schema_rel = RELATION_MAP.get(rel_type, "References")
                    scheme = r.get("scheme", "Other").lower()
                    schema_id_type = ID_TYPE_MAP.get(scheme, "Other")
                    valid_relations.append({
                        "relatedIdentifier": r["identifier"],
                        "relatedIdentifierType": schema_id_type,
                        "relationType": schema_rel,
                    })
            if valid_relations:
                result["relatedIdentifiers"] = valid_relations

        # File formats (extract from files, don't store files themselves)
        files = record.get("files", [])
        if files:
            formats = set()
            for f in files:
                if f.get("key"):
                    ext = Path(f["key"]).suffix.upper().lstrip(".")
                    if ext:
                        formats.add(ext)
            if formats:
                result["formats"] = list(formats)

        return self._ensure_required_fields(result)

    def convert_figshare(self, record: Dict) -> Dict:
        """Convert Figshare record to posters-science schema."""
        result = {
            "$schema": self.SCHEMA_URL,
        }

        # DOI and identifiers
        doi = record.get("doi")
        identifiers = []
        if doi:
            identifiers.append({"identifier": doi, "identifierType": "DOI"})

        figshare_id = record.get("id")
        if figshare_id:
            identifiers.append({"identifier": str(figshare_id), "identifierType": "Other"})

        if identifiers:
            result["identifiers"] = identifiers

        # Creators — Figshare gives "Given Family", convert to "Family, Given"
        creators = []
        for author in record.get("authors", []):
            full_name = _clean_html(author.get("full_name", ""))
            name = _to_family_given(full_name)
            creator_entry = {
                "name": name,
                "nameType": "Personal",
            }
            # Use structured first/last if available
            if author.get("last_name"):
                creator_entry["familyName"] = _clean_html(author["last_name"])
            if author.get("first_name"):
                creator_entry["givenName"] = _clean_html(author["first_name"])

            if author.get("orcid_id"):
                creator_entry["nameIdentifiers"] = [{
                    "nameIdentifier": author["orcid_id"],
                    "nameIdentifierScheme": "ORCID",
                }]
            creators.append(creator_entry)

        if creators:
            result["creators"] = creators

        # Title — clean file extensions
        title = record.get("title")
        if title:
            result["titles"] = [{"title": _clean_title(title)}]

        # Publisher
        result["publisher"] = {"name": "Figshare"}

        # Publication year
        pub_date = record.get("published_date")
        if pub_date:
            clean_date = _strip_timestamp(pub_date)
            try:
                year = int(clean_date[:4])
                result["publicationYear"] = year
            except (ValueError, TypeError):
                pass
            result["dates"] = [{"date": clean_date, "dateType": "Issued"}]

        created = record.get("created_date", "")
        if created:
            dates_list = result.get("dates", [])
            dates_list.append({"date": _strip_timestamp(created), "dateType": "Submitted"})
            result["dates"] = dates_list

        # Resource type
        result["types"] = {
            "resourceType": "Scientific Poster",
            "resourceTypeGeneral": "Image"
        }

        # Description — clean HTML
        description = record.get("description")
        if description:
            clean_desc = _clean_html(description)
            if clean_desc:
                result["descriptions"] = [{
                    "description": clean_desc,
                    "descriptionType": "Abstract"
                }]

        # Tags/Keywords + Categories (deduped, case-insensitive)
        seen_subjects = set()
        subjects = []
        tags = record.get("tags", [])
        for tag in tags:
            key = tag.strip().lower()
            if key and key not in seen_subjects:
                seen_subjects.add(key)
                subjects.append({"subject": tag})

        categories = record.get("categories", [])
        for cat in categories:
            if isinstance(cat, dict):
                cat_title = cat.get("title", "")
            elif cat:
                cat_title = str(cat)
            else:
                continue
            key = cat_title.strip().lower()
            if key and key not in seen_subjects:
                seen_subjects.add(key)
                subjects.append({"subject": cat_title})

        if subjects:
            result["subjects"] = subjects

        # License
        license_info = record.get("license")
        if license_info:
            rights_entry = {}
            if license_info.get("name"):
                rights_entry["rights"] = license_info["name"]
            if license_info.get("url"):
                rights_entry["rightsUri"] = license_info["url"]
            if rights_entry:
                result["rightsList"] = [rights_entry]

        # Funding
        funding = record.get("funding_list", [])
        if funding:
            funders = []
            for f in funding:
                funder_entry = {}
                funder_name = f.get("funder_name") or f.get("title") or ""
                funder_entry["funderName"] = funder_name
                if f.get("title") and f["title"] != funder_name:
                    funder_entry["awardTitle"] = f["title"]
                if f.get("grant_code"):
                    funder_entry["awardNumber"] = f["grant_code"]
                if funder_entry:
                    funders.append(funder_entry)
            if funders:
                result["fundingReferences"] = funders

        # File formats
        files = record.get("files", [])
        if files:
            formats = set()
            for f in files:
                if f.get("name"):
                    ext = Path(f["name"]).suffix.upper().lstrip(".")
                    if ext:
                        formats.add(ext)
            if formats:
                result["formats"] = list(formats)

        return self._ensure_required_fields(result)

    def detect_source(self, record: Dict) -> str:
        """Detect the source repository of a record."""
        if "metadata" in record and record.get("conceptrecid"):
            return "zenodo"
        if record.get("links", {}).get("self", "").startswith("https://zenodo"):
            return "zenodo"
        if "url_public_api" in record or "figshare" in str(record.get("url", "")):
            return "figshare"
        if "defined_type" in record:
            return "figshare"
        return "unknown"

    def convert(self, record: Dict, source: Optional[str] = None) -> Dict:
        """Convert a record from any supported source."""
        if source is None:
            source = self.detect_source(record)

        if source == "zenodo":
            return self.convert_zenodo(record)
        elif source == "figshare":
            return self.convert_figshare(record)
        else:
            logger.warning(f"Unknown source: {source}")
            return {"error": f"Unknown source: {source}", "original": record}

    def convert_file(self, input_file: str, output_file: str, source: Optional[str] = None) -> Dict:
        """Convert a JSON file containing repository metadata."""
        with open(input_file, "r", encoding="utf-8") as f:
            record = json.load(f)

        converted = self.convert(record, source)

        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(converted, f, indent=2, ensure_ascii=False)

        return converted

    def convert_directory(self, input_dir: str, output_dir: str, source: Optional[str] = None) -> Dict:
        """Convert all JSON files in a directory."""
        input_path = Path(input_dir)
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        json_files = list(input_path.glob("*.json"))
        logger.info(f"Converting {len(json_files)} files from {input_dir}")

        stats = {"success": 0, "error": 0}

        for json_file in tqdm(json_files, desc="Converting"):
            try:
                output_file = output_path / json_file.name
                self.convert_file(str(json_file), str(output_file), source)
                stats["success"] += 1
            except Exception as e:
                logger.error(f"Error converting {json_file}: {e}")
                stats["error"] += 1

        logger.info(f"Conversion complete: {stats['success']} success, {stats['error']} errors")
        return stats
