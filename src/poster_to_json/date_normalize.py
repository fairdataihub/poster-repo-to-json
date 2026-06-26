#!/usr/bin/env python3
"""
Normalize date fields imported from repository metadata to canonical forms.

Rule (same as the license cleanup): convert each value to its canonical format,
and drop junk *values* — never the record.

  publicationYear -> 4-digit int in [1900, max_year], else None (dropped).
  dates[].date    -> ISO 8601: "YYYY", "YYYY-MM", "YYYY-MM-DD", or a
                     "start/end" range of those. Free-text like
                     "16-18 December 2019" is parsed; junk like
                     "Not specified" / "null" / "N/A" returns None (entry
                     dropped).
"""
import re

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}
# Longest names first so "march" matches before "mar", etc.
_MONTH_NAMES = sorted(_MONTHS, key=len, reverse=True)

_JUNK = frozenset({
    "", "not specified", "notspecified", "not found", "notfound", "null",
    "none", "n/a", "na", "n", "a", "unknown", "tbd", "tba", "-", "/",
})


def _is_junk(s: str) -> bool:
    return s.strip().lower() in _JUNK


def _strip_ordinals(s: str) -> str:
    return re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", s, flags=re.IGNORECASE)


def _iso(y: int, m=None, d=None) -> str:
    if m and d:
        return f"{y:04d}-{m:02d}-{d:02d}"
    if m:
        return f"{y:04d}-{m:02d}"
    return f"{y:04d}"


def normalize_publication_year(value, max_year: int = 2026):
    """Return a 4-digit int year in [1900, max_year], else None."""
    if value is None:
        return None
    try:
        y = int(str(value).strip()[:4])
    except (ValueError, TypeError):
        return None
    if 1900 <= y <= max_year:
        return y
    return None


def normalize_date_part(part: str):
    """Normalize a single date token to ISO or a 'start/end' sub-range, or None."""
    if not part:
        return None
    s = _strip_ordinals(part.strip())
    if _is_junk(s):
        return None

    m = re.fullmatch(r"(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", s)
    if m:
        y = int(m.group(1))
        if not (1900 <= y <= 2100):
            return None
        mo = int(m.group(2)) if m.group(2) else None
        d = int(m.group(3)) if m.group(3) else None
        if mo and not (1 <= mo <= 12):
            return None
        if d and not (1 <= d <= 31):
            d = None
        return _iso(y, mo, d)

    ym = re.search(r"\b(19|20)\d{2}\b", s)
    if not ym:
        return None
    year = int(ym.group(0))

    month = None
    low = s.lower()
    for name in _MONTH_NAMES:
        if re.search(r"\b" + name + r"\b", low):
            month = _MONTHS[name]
            break

    without_year = re.sub(r"\b(19|20)\d{2}\b", "", s)
    days = [int(x) for x in re.findall(r"\b(\d{1,2})\b", without_year)]
    days = [d for d in days if 1 <= d <= 31]

    if year and month and len(days) >= 2:
        return f"{_iso(year, month, days[0])}/{_iso(year, month, days[1])}"
    if year and month and len(days) == 1:
        return _iso(year, month, days[0])
    if year and month:
        return _iso(year, month)
    return _iso(year)


def normalize_date_value(raw):
    """Normalize a dates[].date value (possibly a 'start/end' range) to ISO, or None."""
    if raw is None:
        return None
    s = str(raw).strip()
    if _is_junk(s):
        return None
    # ISO timestamp -> date only
    if "T" in s and re.match(r"\d{4}-\d{2}-\d{2}T", s):
        s = s.split("T")[0]

    halves = []
    for h in s.split("/"):
        h = h.strip()
        if h and h not in halves:
            halves.append(h)

    pieces = []
    for h in halves:
        n = normalize_date_part(h)
        if not n:
            continue
        for piece in n.split("/"):
            if piece not in pieces:
                pieces.append(piece)

    if not pieces:
        return None
    if len(pieces) == 1:
        return pieces[0]
    if len(pieces) == 2:
        return f"{pieces[0]}/{pieces[1]}"
    return f"{min(pieces)}/{max(pieces)}"


def normalize_record_dates(record: dict, max_year: int = 2026) -> bool:
    """Normalize publicationYear and dates[] in place. Returns True if changed."""
    changed = False

    if "publicationYear" in record:
        py = record["publicationYear"]
        npy = normalize_publication_year(py, max_year)
        if npy != py:
            if npy is None:
                del record["publicationYear"]
            else:
                record["publicationYear"] = npy
            changed = True

    dates = record.get("dates")
    if isinstance(dates, list):
        new_dates = []
        for d in dates:
            if not isinstance(d, dict):
                continue
            nd = normalize_date_value(d.get("date"))
            if nd is None:
                changed = True  # dropped a junk entry
                continue
            if nd != d.get("date"):
                d = dict(d)
                d["date"] = nd
                changed = True
            new_dates.append(d)
        if new_dates != dates:
            if new_dates:
                record["dates"] = new_dates
            else:
                del record["dates"]
                changed = True

    return changed
