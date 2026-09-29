"""
Turn citation text into DataCite ``References`` relations.

The platform shows a poster's references only through ``relatedIdentifiers``
(Related Resources), and a user sharing a poster adds each reference the same
way: one identifier, its type inferred from the value, relation "References".
Reference lists are otherwise free text: Zenodo's ``metadata.references`` is a
list of citation strings, and poster2json parses the poster's printed
reference list into ``{"authors", "year", "title"}`` entries. Neither reached
``relatedIdentifiers`` unless the DOI also happened to be scraped off the
poster, so most references never appeared on the site.

This reads one identifier out of each citation, in the order the platform's
share form infers identifier types (a DOI wins, even inside a doi.org URL;
then arXiv, PMID, and finally any other URL), and appends it as a References
relation, deduplicated against the record's existing relations and its own
identifiers. A citation with no identifier cannot be a relation (DataCite
requires one), so it is left as text, exactly as the share form would.
"""

import re
from typing import Iterable, List, Optional, Tuple

# A DOI anywhere in the text, including inside https://doi.org/... . Not preceded
# by a word character, dot or hyphen, so "210.1234/x" is not read as a DOI.
_DOI_RE = re.compile(r"(?<![\w.-])(10\.\d{4,9}/[^\s\"<>]+)", re.I)
_ARXIV_RE = re.compile(r"\barXiv:\s*((?:\d{4}\.\d{4,5}|[a-z-]+/\d{7})(?:v\d+)?)", re.I)
_PMID_RE = re.compile(r"\bPMID:?\s*(\d{4,9})\b", re.I)
_URL_RE = re.compile(r"https?://[^\s\"<>]+", re.I)
_TRAILING = ".,;:'\""
_PAIRS = (("(", ")"), ("[", "]"), ("{", "}"))


def _trim(value: str) -> str:
    """Drop trailing punctuation and any unbalanced closing bracket.

    Balanced brackets are kept: DOIs legitimately contain them
    (10.1016/S0140-6736(20)30183-5), while a citation often wraps a DOI or URL
    in parentheses, "(doi: 10.1/x)", whose closer is not part of it.
    """
    value = value.rstrip(_TRAILING)
    changed = True
    while changed:
        changed = False
        for opener, closer in _PAIRS:
            if value.endswith(closer) and value.count(closer) > value.count(opener):
                value = value[:-1].rstrip(_TRAILING)
                changed = True
    return value


def citation_identifier(text) -> Optional[Tuple[str, str]]:
    """The one identifier a citation carries, as (identifier, relatedIdentifierType)."""
    if not isinstance(text, str) or not text.strip():
        return None
    m = _DOI_RE.search(text)
    if m:
        doi = _trim(m.group(1))
        if "/" in doi and len(doi.split("/", 1)[1]) > 0:
            return doi, "DOI"
    m = _ARXIV_RE.search(text)
    if m:
        return m.group(1), "arXiv"
    m = _PMID_RE.search(text)
    if m:
        return m.group(1), "PMID"
    m = _URL_RE.search(text)
    if m:
        url = _trim(m.group(0))
        if "." in url.split("://", 1)[-1]:
            return url, "URL"
    return None


def _citation_text(ref) -> str:
    """Flatten a citation (Zenodo string or dict, poster2json dict) to one string."""
    if isinstance(ref, str):
        return ref
    if isinstance(ref, dict):
        return " ".join(str(v) for k, v in ref.items()
                        if k != "id" and isinstance(v, (str, int)))
    return ""


def _key(identifier) -> str:
    k = str(identifier or "").strip().lower()
    k = re.sub(r"^https?://(dx\.)?doi\.org/", "", k)
    return k.rstrip("/")


def add_reference_links(record: dict, references: Iterable) -> int:
    """Append a References relation for each citation that carries an identifier.

    Returns how many were added. Existing relations and the record's own
    identifiers are never duplicated; nothing existing is changed.
    """
    if not references:
        return 0
    related = record.get("relatedIdentifiers")
    if not isinstance(related, list):
        related = []
    seen = {_key(r.get("relatedIdentifier")) for r in related if isinstance(r, dict)}
    own = {_key(i.get("identifier")) for i in record.get("identifiers") or []
           if isinstance(i, dict)}
    added: List[dict] = []
    for ref in references:
        found = citation_identifier(_citation_text(ref))
        if not found:
            continue
        identifier, id_type = found
        k = _key(identifier)
        if not k or k in seen or k in own:
            continue
        seen.add(k)
        added.append({
            "relatedIdentifier": identifier,
            "relatedIdentifierType": id_type,
            "relationType": "References",
        })
    if added:
        record["relatedIdentifiers"] = related + added
    return len(added)


def link_poster_references(record: dict) -> bool:
    """Default merge step: link the references poster2json parsed off the poster."""
    return add_reference_links(record, record.get("references") or []) > 0
