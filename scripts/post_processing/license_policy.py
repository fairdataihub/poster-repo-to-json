#!/usr/bin/env python3
"""
License policy for the posters.science corpus.

Defines which licenses allow redistribution of poster2json-extracted
content (text, captions, structured sections) and which require us to
strip that content and keep only repository metadata.

Principle: if the license does not clearly grant permission to
redistribute derived content, we strip the extraction output. Structured
JSON extraction from a poster is a transformation/derivative work.
"""

import re

# ============================
# ALLOWED LICENSES (whitelist)
# ============================
# These explicitly permit derivative works. Extracted content is kept.

ALLOWED_LICENSES = frozenset({
    # Public domain
    "CC0-1.0",
    # Creative Commons - permissive (derivatives allowed)
    "CC-BY-4.0",
    "CC-BY-3.0",
    "CC-BY-2.5",
    "CC-BY-2.0",
    "CC-BY-1.0",
    "CC-BY-SA-4.0",
    "CC-BY-SA-3.0",
    "CC-BY-SA-2.5",
    "CC-BY-SA-2.0",
    "CC-BY-SA-1.0",
    "CC-BY-NC-4.0",
    "CC-BY-NC-3.0",
    "CC-BY-NC-2.5",
    "CC-BY-NC-2.0",
    "CC-BY-NC-1.0",
    "CC-BY-NC-SA-4.0",
    "CC-BY-NC-SA-3.0",
    "CC-BY-NC-SA-2.5",
    "CC-BY-NC-SA-2.0",
    "CC-BY-NC-SA-1.0",
    # Software licenses (permissive/copyleft, derivatives OK)
    "MIT",
    "Apache-2.0",
    "GPL-3.0",
    "GPL-2.0",
    "LGPL-3.0",
    "LGPL-2.1",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "MPL-2.0",
    "ISC",
    "Unlicense",
    # Zenodo non-SPDX categories
    "other-open",
    "other-pd",
})

# ============================
# BLOCKED LICENSES (blocklist)
# ============================
# These do NOT grant derivative/redistribution rights.
# Extracted content must be stripped; only repository metadata is kept.

BLOCKED_LICENSES = frozenset({
    # No-Derivatives: explicitly prohibits derivative works
    "CC-BY-ND-4.0",
    "CC-BY-ND-3.0",
    "CC-BY-ND-2.5",
    "CC-BY-ND-2.0",
    "CC-BY-ND-1.0",
    "CC-BY-NC-ND-4.0",
    "CC-BY-NC-ND-3.0",
    "CC-BY-NC-ND-2.5",
    "CC-BY-NC-ND-2.0",
    "CC-BY-NC-ND-1.0",
    # Restrictive / unknown terms
    "In Copyright",
    "all rights reserved",
    "All Rights Reserved",
    "Copyright not evaluated",
    "Copyright undetermined",
    "other-at",
    "other-closed",
    "other-nc",
    "other",
})

_ND_PATTERN = re.compile(r"CC-BY(-NC)?-ND", re.IGNORECASE)


_BLOCKED_LOWER = frozenset(b.lower() for b in BLOCKED_LICENSES)
_ALLOWED_UPPER = frozenset(a.upper() for a in ALLOWED_LICENSES)


def classify_license(rights_list) -> str:
    """Classify a rightsList as 'allowed', 'blocked', or 'unknown'.

    If ANY entry matches the whitelist, returns 'allowed' -- a single
    permissive license grants the derivative rights we need even if other
    entries are restrictive (dual licensing).

    Returns 'blocked' if no entry is allowed and at least one matches the
    blocklist or ND pattern.

    Returns 'unknown' if the list is empty/null or no entry matches either
    list. Enforcement treats 'unknown' the same as 'blocked'.
    """
    if not rights_list:
        return "blocked"

    if isinstance(rights_list, str):
        rights_list = [{"rights": rights_list}]

    has_blocked = False

    for entry in rights_list:
        if isinstance(entry, str):
            entry = {"rights": entry}
        if not isinstance(entry, dict):
            continue

        for field in ("rightsIdentifier", "rights"):
            val = entry.get(field, "")
            if not isinstance(val, str) or not val.strip():
                continue
            val = val.strip()

            if val in ALLOWED_LICENSES or val.upper() in _ALLOWED_UPPER:
                return "allowed"
            if val in BLOCKED_LICENSES or val.lower() in _BLOCKED_LOWER:
                has_blocked = True
            if _ND_PATTERN.search(val):
                has_blocked = True

    return "blocked" if has_blocked else "unknown"


# Fields added by poster2json extraction (derived from poster content).
# These are stripped when the license blocks derivative redistribution.
EXTRACTED_CONTENT_FIELDS = [
    "content",
    "imageCaptions",
    "tableCaptions",
    "researchField",
    "domain",  # mirror of researchField (also poster-derived)
]

# Descriptions: the deposit's OWN Abstract (descriptionType "Abstract") is open
# Zenodo/Figshare catalog metadata (published CC0) and is KEPT; only the
# LLM-generated summary (descriptionType "Other") is a derivative and is dropped.


def strip_extracted_content(data: dict) -> dict:
    """Remove poster2json-derived content from a merged JSON.

    Keeps repository metadata (identifiers, creators, titles, dates,
    publisher, rightsList, fundingReferences, and the deposit Abstract) but
    removes extracted content (sections/captions/researchField) and the
    LLM-generated "Other" description that constitute a derivative work.
    """
    result = dict(data)

    for field in EXTRACTED_CONTENT_FIELDS:
        result.pop(field, None)

    # Keep deposit Abstract(s); drop LLM-generated ("Other") descriptions.
    descs = result.get("descriptions")
    if isinstance(descs, list):
        kept = [d for d in descs if isinstance(d, dict)
                and d.get("descriptionType") == "Abstract"]
        if kept:
            result["descriptions"] = kept
        else:
            result.pop("descriptions", None)
    else:
        result.pop("descriptions", None)

    result["_license_blocked"] = True

    return result
