#!/usr/bin/env python3
"""
Normalizers for deposit-imported fields (beyond licenses and dates).

Same rule: canonicalize the value, drop junk *values*, never the record.
Each normalizer mutates a record in place and returns True if it changed.

Currently: conference. (creators, subjects, publisher, formats to follow.)
"""
import re

from .date_normalize import normalize_date_value, normalize_publication_year

_URL_RE = re.compile(r"https?://|www\.", re.I)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")

_PLACEHOLDER = frozenset({
    "", "not specified", "unspecified", "not applicable", "n/a", "na",
    "none", "null", "unknown", "not found", "tbd", "tba", "-",
    "name of conference", "conference name", "conference name here",
    "conference name not found", "conference not found", "no conference",
    "not available", "not provided", "unnamed conference",
    "city, country", "location", "venue", "conference url",
    "conference organizer or institution name", "institution name",
})


def _is_placeholder(v) -> bool:
    """True for None or any placeholder/junk string value."""
    if v is None:
        return True
    if not isinstance(v, str):
        return False
    s = v.strip().lower()
    return s in _PLACEHOLDER or "not specified" in s or "not found" in s


# Conference sub-fields that hold dates -> ISO-normalize.
_CONF_DATE_FIELDS = ("conferenceStartDate", "conferenceEndDate")
# A conference object with none of these "real" fields is dropped.
_CONF_SIGNAL_FIELDS = (
    "conferenceName", "conferenceAcronym", "conferenceLocation",
    "conferenceStartDate", "conferenceEndDate", "conferenceYear", "conferenceUri",
)


def normalize_conference(record: dict, max_year: int = 2026) -> bool:
    """Drop placeholder conference sub-fields, ISO-normalize its dates, and drop
    the whole conference object if nothing meaningful remains."""
    conf = record.get("conference")
    if not isinstance(conf, dict):
        return False

    changed = False
    cleaned = {}
    for k, v in conf.items():
        if _is_placeholder(v):
            changed = True
            continue
        if k in _CONF_DATE_FIELDS and isinstance(v, str):
            nv = normalize_date_value(v)
            if nv is None:
                changed = True
                continue
            if nv != v:
                v = nv
                changed = True
        if k == "conferenceYear":
            ny = normalize_publication_year(v, max_year)
            if ny is None:
                changed = True
                continue
            if ny != v:
                v = ny
                changed = True
        cleaned[k] = v

    # Drop the object entirely if it carries no real signal.
    if not any(cleaned.get(f) for f in _CONF_SIGNAL_FIELDS):
        if "conference" in record:
            del record["conference"]
            return True
        return changed

    if cleaned != conf:
        record["conference"] = cleaned
        changed = True
    return changed


def _repository_of(record: dict):
    """Infer the source repository (Zenodo/Figshare) from the record's DOI."""
    for i in (record.get("identifiers") or []):
        if isinstance(i, dict) and i.get("identifierType") == "DOI":
            doi = str(i.get("identifier", "")).lower()
            if "zenodo" in doi:
                return "Zenodo"
            if "figshare" in doi:
                return "Figshare"
    return None


def normalize_publisher(record: dict, source: str = None) -> bool:
    """publisher = the source repository (Zenodo/Figshare), per DataCite convention.

    The depositor's/LLM's publisher (an institution, journal, PosterPresentations,
    etc.) is replaced with the repository. `source` (e.g. the "zenodo"/"figshare"
    corpus subdir) is authoritative when given; otherwise it's inferred from the
    DOI. If the repository can't be determined, the value is left unchanged.
    """
    repo = None
    if source:
        s = source.strip().lower()
        if s == "zenodo":
            repo = "Zenodo"
        elif s == "figshare":
            repo = "Figshare"
    if not repo:
        repo = _repository_of(record)
    if not repo:
        return False
    want = {"name": repo}
    if record.get("publisher") != want:
        record["publisher"] = want
        return True
    return False


def normalize_subjects(record: dict) -> bool:
    """Drop junk subjects (empty/placeholder/URL/email) and dedup. No splitting."""
    subs = record.get("subjects")
    if not isinstance(subs, list):
        return False
    seen = set()
    cleaned = []
    changed = False
    for s in subs:
        val = s.get("subject") if isinstance(s, dict) else s
        if not isinstance(val, str):
            changed = True
            continue
        v = val.strip()
        if not v or _is_placeholder(v) or _URL_RE.search(v) or _EMAIL_RE.search(v):
            changed = True
            continue
        key = v.lower()
        if key in seen:
            changed = True
            continue
        seen.add(key)
        entry = {"subject": v}
        if isinstance(s, dict):
            entry = {**s, "subject": v}
        if entry != s:
            changed = True
        cleaned.append(entry)
    if cleaned != subs:
        if cleaned:
            record["subjects"] = cleaned
        else:
            record.pop("subjects", None)
        return True
    return changed


def normalize_creators(record: dict) -> bool:
    """Drop clearly-junk creators and placeholder affiliations. Names/order are
    the deposit's authority (v0.4.0); lumped-but-real names are left as-is."""
    cres = record.get("creators")
    if not isinstance(cres, list):
        return False
    cleaned = []
    changed = False
    for c in cres:
        if not isinstance(c, dict):
            changed = True
            continue
        nm = c.get("name")
        n = str(nm).strip() if nm is not None else ""
        low = n.lower()
        if (not n or _is_placeholder(nm) or low.startswith("conference")
                or _EMAIL_RE.search(n) or _URL_RE.search(n)):
            changed = True
            continue
        cc = dict(c)
        if n != nm:
            cc["name"] = n
            changed = True
        for part in ("givenName", "familyName"):
            if part in cc and _is_placeholder(cc[part]):
                del cc[part]
                changed = True
        affs = cc.get("affiliation")
        if isinstance(affs, list):
            new_affs = []
            for a in affs:
                an = a.get("name") if isinstance(a, dict) else a
                if _is_placeholder(an) or (isinstance(an, str) and not an.strip()):
                    changed = True
                    continue
                new_affs.append(a)
            if new_affs != affs:
                if new_affs:
                    cc["affiliation"] = new_affs
                else:
                    cc.pop("affiliation", None)
                changed = True
        cleaned.append(cc)
    if cleaned != cres:
        if cleaned:
            record["creators"] = cleaned
        else:
            record.pop("creators", None)
        return True
    return changed


_FORMAT_MAP = {
    "text/html": "HTML", "html": "HTML", "pdf": "PDF", "application/pdf": "PDF",
    "png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "tif": "TIFF", "tiff": "TIFF",
    "xml": "XML", "csv": "CSV", "json": "JSON", "epub": "EPUB",
}
_FORMAT_JUNK = frozenset({"poster", "parfile", "file", "document", "other"})


def normalize_formats(record: dict) -> bool:
    """Canonicalize format tokens, drop non-format junk, dedup."""
    fmts = record.get("formats")
    if not isinstance(fmts, list):
        return False
    seen = set()
    cleaned = []
    changed = False
    for x in fmts:
        if not isinstance(x, str):
            changed = True
            continue
        v = x.strip()
        low = v.lower()
        if not v or _is_placeholder(v) or low in _FORMAT_JUNK:
            changed = True
            continue
        canon = _FORMAT_MAP.get(low, v.upper() if len(v) <= 5 else v)
        if canon in seen:
            changed = True
            continue
        seen.add(canon)
        if canon != v:
            changed = True
        cleaned.append(canon)
    if cleaned != fmts:
        if cleaned:
            record["formats"] = cleaned
        else:
            record.pop("formats", None)
        return True
    return changed
