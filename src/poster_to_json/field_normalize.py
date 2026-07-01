#!/usr/bin/env python3
"""
Normalizers for deposit-imported fields (beyond licenses and dates).

Same rule: canonicalize the value, drop junk *values*, never the record.
Each normalizer mutates a record in place and returns True if it changed.

Currently: conference. (creators, subjects, publisher, formats to follow.)
"""
import re

from .date_normalize import normalize_date_value, normalize_publication_year

_PLACEHOLDER = frozenset({
    "", "not specified", "unspecified", "not applicable", "n/a", "na",
    "none", "null", "unknown", "not found", "tbd", "tba", "-",
    "name of conference", "conference name", "conference name here",
    "city, country", "location", "venue", "conference url",
    "conference organizer or institution name", "institution name",
})


def _is_placeholder(v) -> bool:
    return isinstance(v, str) and v.strip().lower() in _PLACEHOLDER


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
