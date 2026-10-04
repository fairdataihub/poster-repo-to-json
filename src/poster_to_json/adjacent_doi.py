"""Give a record with no DOI the DOI of its own published abstract.

Every delivered record should carry a DOI. Institutional Figshare posters
often have only a Handle, but some of them were also published as a meeting
abstract or short paper (BMC Neuroscience supplement abstracts, CHI extended
abstracts) and list that DOI among their related identifiers. When the title
registered for a related DOI matches the poster's own title, that DOI is the
closest one the poster has, and it becomes the record's DOI. A related DOI with
a different title is a cited work and stays a relation.

The lookup of registered titles is passed in, so this module does no network
access; scripts/post_processing/adjacent_doi.py supplies a cached resolver.
"""

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Callable, Optional

MATCH_RATIO = 0.9
MIN_TITLE_CHARS = 12
MAX_CANDIDATES = 30

_DOI_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)?(10\.\d{4,9}/\S+)$", re.I)


def _norm(text) -> str:
    t = unicodedata.normalize("NFKD", str(text or ""))
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def _main(text) -> str:
    """The title before a subtitle separator."""
    return _norm(re.split(r"[:|]", str(text or ""), maxsplit=1)[0])


def title_matches(poster_title, registered_title) -> bool:
    """True when two titles name the same work.

    Compares the full titles and the main titles (before a colon), so a poster
    titled "Scented Material: Changing Features of ..." matches the paper
    registered as "Scented Material". Titles shorter than MIN_TITLE_CHARS never
    match, so generic titles cannot pull in an unrelated DOI.
    """
    pairs = [(_norm(poster_title), _norm(registered_title)),
             (_main(poster_title), _norm(registered_title)),
             (_norm(poster_title), _main(registered_title))]
    for a, b in pairs:
        if len(a) < MIN_TITLE_CHARS or len(b) < MIN_TITLE_CHARS or len(b.split()) < 2:
            continue
        if a == b or SequenceMatcher(None, a, b).ratio() >= MATCH_RATIO:
            return True
    return False


def bare_doi(value) -> Optional[str]:
    """A related identifier as a bare DOI, or None. Trailing punctuation from
    text extraction (a closing parenthesis, a full stop) is trimmed."""
    m = _DOI_RE.match(str(value or "").strip())
    if not m:
        return None
    doi = m.group(1).rstrip(".,;")
    while doi.endswith(")") and doi.count(")") > doi.count("("):
        doi = doi[:-1]
    return doi


def has_doi(record: dict) -> bool:
    return any(isinstance(i, dict) and str(i.get("identifierType", "")).upper() == "DOI"
               and str(i.get("identifier", "")).strip() for i in record.get("identifiers") or [])


def poster_title(record: dict) -> str:
    for t in record.get("titles") or []:
        if isinstance(t, dict) and t.get("title") and not t.get("titleType"):
            return t["title"]
    return ""


def adopt_adjacent_doi(record: dict, registered_title: Callable[[str], Optional[str]]) -> Optional[str]:
    """If the record has no DOI and one of its related DOIs is registered under
    the poster's own title, make that DOI the record's first identifier, drop
    the relations that pointed at it, and return it. Otherwise return None and
    leave the record unchanged."""
    if has_doi(record):
        return None
    title = poster_title(record)
    if not title:
        return None
    rels = [r for r in record.get("relatedIdentifiers") or [] if isinstance(r, dict)]
    seen = set()
    for rel in rels:
        doi = bare_doi(rel.get("relatedIdentifier"))
        if not doi or doi.lower() in seen:
            continue
        seen.add(doi.lower())
        if len(seen) > MAX_CANDIDATES:
            break
        reg = registered_title(doi)
        if reg and title_matches(title, reg):
            record["identifiers"] = [{"identifier": doi, "identifierType": "DOI"}] + list(record.get("identifiers") or [])
            kept = [r for r in rels if (bare_doi(r.get("relatedIdentifier")) or "").lower() != doi.lower()]
            if kept:
                record["relatedIdentifiers"] = kept
            else:
                record.pop("relatedIdentifiers", None)
            return doi
    return None
