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

from .date_normalize import normalize_date_value

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


_ISO_DATE_RE = re.compile(r'^\d{4}(-\d{2}(-\d{2})?)?$')


def _iso_date_or_none(s):
    """Return s if it is a clean ISO date (YYYY / YYYY-MM / YYYY-MM-DD), else None."""
    s = (str(s).strip() if s else "")
    return s if _ISO_DATE_RE.match(s) else None


def conference_from_meeting(meeting: dict) -> Optional[dict]:
    """Build a conference dict from a Zenodo deposit `meeting` (authoritative).

    Shared by convert_zenodo and the date backfill so the deposit meeting can
    override an LLM-extracted conference (which was prone to hallucinating the
    year). Returns None if the meeting yields nothing usable."""
    if not meeting:
        return None
    conference = {}
    if meeting.get("title"):
        conference["conferenceName"] = meeting["title"]
    if meeting.get("acronym"):
        conference["conferenceAcronym"] = meeting["acronym"]
    if meeting.get("dates"):
        dates_str = meeting["dates"]
        # Structured "YYYY-MM-DD - YYYY-MM-DD" first, else free-text; both routed
        # through normalize_date_value and kept ONLY if clean ISO (never leak
        # unparsed free-text like "19 november 2024" into a date field).
        src = dates_str
        if " - " in dates_str:
            left, _, right = dates_str.partition(" - ")
            ry = re.search(r"(?:19|20)\d{2}", right)
            if re.search(r"(?:19|20)\d{2}", left) and ry:
                src = left + "/" + right                       # both halves carry a year
            elif ry and re.search(r"[^\W\d_]", left):
                # cross-month range, year only on the right ("31 October - 1 December
                # 2023"): copy the year onto the month-bearing left half, then split.
                src = left + " " + ry.group(0) + "/" + right
            # else a day-only left ("5 - 7 May 2021") stays un-split for normalize_date_value
        parsed = normalize_date_value(src)
        s = e = None
        if parsed and "/" in parsed:
            s, e = parsed.split("/", 1)
        elif parsed:
            s = parsed
        s, e = _iso_date_or_none(s), _iso_date_or_none(e)
        if s and e and e < s:      # nonsensical (cross-month misparse) -> drop end
            e = None
        if s:
            conference["conferenceStartDate"] = s
        if e:
            conference["conferenceEndDate"] = e
        year = _extract_year_from_text(dates_str)
        if year:
            conference["conferenceYear"] = year
    if meeting.get("place"):
        conference["conferenceLocation"] = meeting["place"]
    if meeting.get("url"):
        conference["conferenceUri"] = meeting["url"]
    return conference or None


# Canonical forms for license identifiers the repositories report in varied
# casing/spelling. Keyed by lowercased input.
_LICENSE_ALIASES = {
    "mit": "MIT", "mit-license": "MIT", "the-mit-license": "MIT",
    "apache-2.0": "Apache-2.0", "apache2.0": "Apache-2.0", "apache 2.0": "Apache-2.0",
    "gpl-3.0": "GPL-3.0", "gpl-3.0-only": "GPL-3.0", "gpl3": "GPL-3.0", "gplv3": "GPL-3.0",
    "gpl-2.0": "GPL-2.0", "gpl-2.0-only": "GPL-2.0", "gpl2": "GPL-2.0",
    "lgpl-3.0": "LGPL-3.0", "lgpl-2.1": "LGPL-2.1",
    "bsd-2-clause": "BSD-2-Clause", "bsd-3-clause": "BSD-3-Clause",
    "mpl-2.0": "MPL-2.0", "isc": "ISC", "unlicense": "Unlicense",
    "cc0-1.0": "CC0-1.0", "cc-zero": "CC0-1.0", "cc0": "CC0-1.0", "zero": "CC0-1.0",
}

# Zenodo's own non-SPDX license categories — kept verbatim (lowercase).
_ZENODO_CATEGORIES = frozenset({
    "other-open", "other-pd", "other-nc", "other-closed", "other-at",
})


def _normalize_license_id(raw) -> Optional[str]:
    """Canonicalize a repository-declared license identifier.

    Maps known aliases (mit-license -> MIT), canonicalizes CC/SPDX casing
    (cc-by-4.0 -> CC-BY-4.0), keeps Zenodo other-* categories, and otherwise
    returns the deposit's value verbatim (it is repository-controlled, so even
    an unrecognized id is a real declaration). Returns None for empty input.
    """
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    low = s.lower()
    if low in _LICENSE_ALIASES:
        return _LICENSE_ALIASES[low]
    if low in _ZENODO_CATEGORIES:
        return low
    canon = re.sub(r"[\s_]+", "-", s).upper()
    if canon.startswith("CC-") or canon.startswith("CC0"):
        return canon
    return s


def _zenodo_name_type(creator: Dict) -> str:
    """Map a Zenodo creator's person/org type to a DataCite nameType.

    Newer Zenodo (InvenioRDM) records carry person_or_org.type
    ("personal"/"organizational"); some legacy records carry a flat "type".
    Default to "Personal" when nothing is declared.
    """
    raw = (creator.get("type")
           or creator.get("person_or_org", {}).get("type")
           or "")
    raw = str(raw).strip().lower()
    if raw in ("organizational", "organization", "org"):
        return "Organizational"
    return "Personal"


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


# DataCite relationType controlled vocabulary (keyed by normalized lowercase,
# underscores/spaces stripped). Full enum so valid relations aren't collapsed.
_RELATION_MAP = {
    "iscitedby": "IsCitedBy", "cites": "Cites",
    "issupplementto": "IsSupplementTo", "issupplementedby": "IsSupplementedBy",
    "iscontinuedby": "IsContinuedBy", "continues": "Continues",
    "isdescribedby": "IsDescribedBy", "describes": "Describes",
    "hasmetadata": "HasMetadata", "ismetadatafor": "IsMetadataFor",
    "hasversion": "HasVersion", "isversionof": "IsVersionOf",
    "hasversionof": "HasVersion",
    "isnewversionof": "IsNewVersionOf", "ispreviousversionof": "IsPreviousVersionOf",
    "ispartof": "IsPartOf", "haspart": "HasPart", "ispublishedin": "IsPublishedIn",
    "isreferencedby": "IsReferencedBy", "references": "References",
    "isdocumentedby": "IsDocumentedBy", "documents": "Documents",
    "iscompiledby": "IsCompiledBy", "compiles": "Compiles",
    "isvariantformof": "IsVariantFormOf", "isoriginalformof": "IsOriginalFormOf",
    "isidenticalto": "IsIdenticalTo", "isreviewedby": "IsReviewedBy", "reviews": "Reviews",
    "isderivedfrom": "IsDerivedFrom", "issourceof": "IsSourceOf",
    "isrequiredby": "IsRequiredBy", "requires": "Requires",
    "obsoletes": "Obsoletes", "isobsoletedby": "IsObsoletedBy",
}

_ID_TYPE_MAP = {
    "arxiv": "arXiv", "doi": "DOI", "url": "URL", "urn": "URN", "ark": "ARK",
    "isbn": "ISBN", "issn": "ISSN", "eissn": "EISSN", "pmid": "PMID", "pmcid": "PMCID",
    "handle": "Handle", "bibcode": "bibcode", "igsn": "IGSN", "w3id": "w3id",
    "ean13": "EAN13", "istc": "ISTC", "lissn": "LISSN", "lsid": "LSID",
    "purl": "PURL", "upc": "UPC",
}

_RESOURCE_TYPE_MAP = {
    "publication": "Text", "publication-article": "JournalArticle",
    "publication-preprint": "Preprint", "publication-conferencepaper": "ConferencePaper",
    "publication-book": "Book", "publication-section": "BookChapter",
    "publication-thesis": "Dissertation", "publication-report": "Report",
    "publication-workingpaper": "Preprint", "publication-deliverable": "Report",
    "publication-datamanagementplan": "OutputManagementPlan",
    "dataset": "Dataset", "software": "Software", "poster": "Poster",
    "presentation": "Text", "image": "Image", "image-figure": "Image",
    "image-photo": "Image", "image-diagram": "Image", "image-plot": "Image",
    "video": "Audiovisual", "audio": "Sound", "lesson": "Text",
    "physicalobject": "PhysicalObject", "model": "Model", "workflow": "Workflow",
    "other": "Other",
}


def _relation_type(raw) -> str:
    key = str(raw or "").lower().replace("_", "").replace(" ", "")
    return _RELATION_MAP.get(key, "References")


def _id_type(scheme, identifier="") -> str:
    t = _ID_TYPE_MAP.get(str(scheme or "").lower())
    if t:
        return t
    s = str(identifier)
    if re.match(r"^10\.\d{4,9}/", s):
        return "DOI"
    if s.startswith("http"):
        return "URL"
    return "Other"


def _resource_type_general(rt) -> Optional[str]:
    if not rt:
        return None
    key = str(rt).lower().strip()
    if key in _RESOURCE_TYPE_MAP:
        return _RESOURCE_TYPE_MAP[key]
    return _RESOURCE_TYPE_MAP.get(key.split("-")[0], "Text")


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
            result["types"] = {"resourceType": "Poster", "resourceTypeGeneral": "Poster"}
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

        handle = record.get("handle") or metadata.get("handle")
        if handle and str(handle).strip():
            identifiers.append({"identifier": str(handle).strip(), "identifierType": "Handle"})

        if identifiers:
            result["identifiers"] = identifiers

        # Creators (Zenodo already provides "Family, Given" format)
        creators = []
        for creator in metadata.get("creators", []):
            name = _clean_html(creator.get("name", ""))
            creator_entry = {
                "name": name,
                "nameType": _zenodo_name_type(creator),
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
                    "schemeURI": "https://orcid.org",
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

        # Version (deposit-curated)
        version = metadata.get("version")
        if version is not None and str(version).strip():
            result["version"] = str(version).strip()

        # Publication year
        pub_date = metadata.get("publication_date")
        if pub_date:
            try:
                year = int(pub_date[:4])
                result["publicationYear"] = year
            except (ValueError, TypeError):
                pass
            result["dates"] = [{"date": pub_date, "dateType": "Issued"}]

        # Resource type
        result["types"] = {
            "resourceType": "Poster",
            "resourceTypeGeneral": "Poster"
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

        # License — legacy metadata.license ({id} or str) and/or InvenioRDM
        # metadata.rights ([{id|title}, ...]). Normalize ids; dedup.
        rights_entries = []
        license_info = metadata.get("license")
        if isinstance(license_info, dict):
            lid = _normalize_license_id(license_info.get("id"))
            if lid:
                rights_entries.append({"rights": lid})
        elif isinstance(license_info, str):
            lid = _normalize_license_id(license_info)
            if lid:
                rights_entries.append({"rights": lid})
        for r in (metadata.get("rights") or []):
            if isinstance(r, dict):
                lid = _normalize_license_id(r.get("id") or r.get("title"))
                if lid:
                    rights_entries.append({"rights": lid})
        if rights_entries:
            seen = set()
            deduped = []
            for e in rights_entries:
                k = e["rights"].lower()
                if k not in seen:
                    seen.add(k)
                    deduped.append(e)
            result["rightsList"] = deduped

        # Conference/Meeting information
        conference = conference_from_meeting(metadata.get("meeting", {}))
        if conference:
            result["conference"] = conference

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

        # Related identifiers (depositor-declared relations; NOT the version graph
        # in metadata.relations). Full DataCite relationType enum; resourceTypeGeneral
        # from the related item's resource_type.
        related = metadata.get("related_identifiers", [])
        if related:
            valid_relations = []
            for r in related:
                ident = r.get("identifier")
                if not ident:
                    continue
                entry = {
                    "relatedIdentifier": ident,
                    "relatedIdentifierType": _id_type(r.get("scheme"), ident),
                    "relationType": _relation_type(r.get("relation")),
                }
                rtg = _resource_type_general(r.get("resource_type"))
                if rtg:
                    entry["resourceTypeGeneral"] = rtg
                valid_relations.append(entry)
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

        # Institutional Figshare portals (Leicester, Loughborough, Sheffield,
        # Middlebury, ...) mint a Handle instead of a DOI. Capture it so these
        # posters aren't identifier-less.
        handle = record.get("handle")
        if handle and str(handle).strip():
            identifiers.append({"identifier": str(handle).strip(), "identifierType": "Handle"})

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
                    "schemeURI": "https://orcid.org",
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

        # Version (Figshare article version int -> str)
        version = record.get("version")
        if version is not None and str(version).strip():
            result["version"] = str(version).strip()

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

        # Resource type
        result["types"] = {
            "resourceType": "Poster",
            "resourceTypeGeneral": "Poster"
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

        # License — normalize the Figshare license name to a canonical id.
        license_info = record.get("license")
        if isinstance(license_info, dict):
            lid = _normalize_license_id(license_info.get("name"))
            if lid:
                result["rightsList"] = [{"rights": lid}]

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
                if f.get("url"):
                    funder_entry["awardUri"] = f["url"]
                if funder_entry:
                    funders.append(funder_entry)
            if funders:
                result["fundingReferences"] = funders

        # Related identifiers — Figshare references[] (URL strings) + related_materials[]
        related = []
        seen_rel = set()
        for ref in record.get("references", []):
            if isinstance(ref, str) and ref.strip():
                ident = ref.strip()
                if ident.lower() in seen_rel:
                    continue
                seen_rel.add(ident.lower())
                related.append({
                    "relatedIdentifier": ident,
                    "relatedIdentifierType": _id_type(None, ident),
                    "relationType": "References",
                })
        for rm in record.get("related_materials", []):
            if not isinstance(rm, dict):
                continue
            ident = rm.get("identifier")
            if not ident or not str(ident).strip():
                continue
            ident = str(ident).strip()
            if ident.lower() in seen_rel:
                continue
            seen_rel.add(ident.lower())
            related.append({
                "relatedIdentifier": ident,
                "relatedIdentifierType": _id_type(rm.get("identifier_type"), ident),
                "relationType": _relation_type(rm.get("relation")),
            })
        if related:
            result["relatedIdentifiers"] = related

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
