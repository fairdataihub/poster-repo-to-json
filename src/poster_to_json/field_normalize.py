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
_ET_AL_RE = re.compile(r"\bet\s*\.?\s*al\b", re.I)
# Unfilled poster-template author placeholders (e.g. "LastName, FirstName").
_TEMPLATE_NAME_RE = re.compile(
    r"\b(lastname|firstname|first ?name|last ?name|author ?name|your ?name|"
    r"full ?name|name ?here|forename)\b", re.I)


def _name_is_lumped(name) -> bool:
    """A single creator name that actually crams in multiple authors."""
    if not isinstance(name, str):
        return False
    n = name.strip()
    return n.count(",") >= 3 or bool(_ET_AL_RE.search(n)) or (len(n) > 60 and n.count(",") >= 2)


def creators_are_lumped(creators) -> bool:
    """Deposit creators where authors are crammed into one/few name fields."""
    real = [c for c in (creators or []) if isinstance(c, dict) and c.get("name")]
    return bool(real) and any(_name_is_lumped(c.get("name")) for c in real)


def _dedup_creators(creators):
    seen = set()
    out = []
    for c in creators or []:
        if not isinstance(c, dict):
            continue
        nm = str(c.get("name", "")).strip()
        key = nm.lower()
        if nm and key not in seen:
            seen.add(key)
            out.append(c)
    return out


def extraction_creators_clean(ext_creators) -> bool:
    """True if the extraction has properly-separated authors usable to replace a
    lumped deposit: dedups to >=2 distinct names, none of them itself lumped."""
    ded = _dedup_creators(ext_creators)
    if len(ded) < 2:
        return False
    return not any(_name_is_lumped(c.get("name")) for c in ded)


def creator_addable_to_union(creator) -> bool:
    """A creator worth unioning in from the extraction: a clean, single,
    non-junk, non-lumped author name."""
    if not isinstance(creator, dict):
        return False
    name = creator.get("name")
    return bool(name) and not creator_name_is_junk(name) and not _name_is_lumped(name)


def resolve_lumped_creators(deposit_creators, ext_creators):
    """Return the better creator list. When the deposit crammed authors into one
    field but the extraction cleanly separated them, prefer the extraction;
    otherwise keep the deposit."""
    if creators_are_lumped(deposit_creators) and extraction_creators_clean(ext_creators):
        return _dedup_creators(ext_creators)
    return deposit_creators

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


def split_subject(value: str):
    """Split a lumped keyword string into individual keywords on top-level commas
    and semicolons. Delimiters inside brackets are ignored, so taxonomy terms
    like "Business Information Management (incl. Records, Knowledge) not elsewhere
    classified" stay whole."""
    parts = []
    depth = 0
    cur = ""
    for ch in value:
        if ch in "([{":
            depth += 1
            cur += ch
        elif ch in ")]}":
            depth = max(0, depth - 1)
            cur += ch
        elif ch in ",;" and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


def normalize_subjects(record: dict) -> bool:
    """Split lumped keywords (top-level comma/semicolon), drop junk
    (empty/placeholder/URL/email), and dedup."""
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
        pieces = split_subject(val)
        if pieces != [val.strip()]:
            changed = True
        for v in pieces:
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
            cleaned.append(entry)
    if cleaned != subs:
        if cleaned:
            record["subjects"] = cleaned
        else:
            record.pop("subjects", None)
        return True
    return changed


def creator_name_is_junk(name) -> bool:
    """A creator name that is clearly not a real author: placeholder, unfilled
    template, conference/institution-as-author, or a url/email."""
    n = str(name).strip() if name is not None else ""
    if not n:
        return True
    return (_is_placeholder(name) or n.lower().startswith("conference")
            or bool(_EMAIL_RE.search(n)) or bool(_URL_RE.search(n))
            or bool(_TEMPLATE_NAME_RE.search(n)))


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
        if creator_name_is_junk(nm):
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
