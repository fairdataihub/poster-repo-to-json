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

_ND_PATTERN = re.compile(r"ccby(nc)?nd", re.IGNORECASE)

import unicodedata


def _lkey(s: str) -> str:
    """fair-ly-style folding: strip diacritics/case and every non-alphanumeric, so
    'CC BY 4.0', 'cc-by-4.0', 'CC_BY_4.0' all fold to the same key 'ccby40'."""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


# canonical alphanumeric key -> canonical license id, from both lists
_CANON_BY_KEY = {_lkey(lic): lic for lic in list(ALLOWED_LICENSES) + list(BLOCKED_LICENSES)}

# vernacular / version-less / alternate forms -> canonical id. (Only forms NOT already
# reachable by folding a list member; version-less CC defaults to the current 4.0.)
_LICENSE_ALIASES = {
    "cc0": "CC0-1.0", "cczero": "CC0-1.0", "creativecommonszero": "CC0-1.0",
    "publicdomain": "other-pd", "pd": "other-pd",
    "ccby": "CC-BY-4.0", "ccbysa": "CC-BY-SA-4.0", "ccbync": "CC-BY-NC-4.0",
    "ccbyncsa": "CC-BY-NC-SA-4.0", "ccbynd": "CC-BY-ND-4.0", "ccbyncnd": "CC-BY-NC-ND-4.0",
    "apache2": "Apache-2.0", "apachelicense20": "Apache-2.0", "asl20": "Apache-2.0",
    "gpl": "GPL-3.0", "gpl3": "GPL-3.0", "gplv3": "GPL-3.0",
    "gpl3orlater": "GPL-3.0", "gpl30orlater": "GPL-3.0", "gplv3orlater": "GPL-3.0",
    "gpl2": "GPL-2.0", "gplv2": "GPL-2.0", "lgpl3": "LGPL-3.0", "lgplv3": "LGPL-3.0",
    "mitlicense": "MIT", "bsd": "BSD-3-Clause", "bsd3": "BSD-3-Clause", "bsd2": "BSD-2-Clause",
    "allrightsreserved": "In Copyright", "copyright": "In Copyright",
}

# strip a trailing region/port code after a version number (cc-by-3.0-us -> cc-by-3.0)
_REGION_RE = re.compile(r"(\d)(us|uk|scotland|de|es|fr|it|nl|jp|au|ca|pt|br|igo|int|nz|za)$")


def normalize_license(val) -> str:
    """Return the canonical license id for a free-form license string, or None if
    unrecognized. Folds spacing/case/punctuation and resolves common vernacular forms."""
    if not isinstance(val, str) or not val.strip():
        return None
    k = _lkey(val)
    for key in (k, _REGION_RE.sub(r"\1", k)):
        if key in _CANON_BY_KEY:
            return _CANON_BY_KEY[key]
        if key in _LICENSE_ALIASES:
            return _LICENSE_ALIASES[key]
    return None


def classify_license(rights_list) -> str:
    """Classify a rightsList as 'allowed', 'blocked', or 'unknown'.

    Each license string is normalized (fair-ly folding + alias resolution) to a
    canonical id before matching, so format variants like 'CC BY 4.0' resolve to
    'CC-BY-4.0'. If ANY entry is allowed, returns 'allowed' (a single permissive
    license grants the derivative rights we need, even under dual licensing).
    Returns 'blocked' if none are allowed and at least one is blocked. Returns
    'unknown' if empty/null or nothing resolves -- enforcement treats it as blocked.
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
            canon = normalize_license(entry.get(field, ""))
            if canon is None:
                continue
            if canon in ALLOWED_LICENSES:
                return "allowed"
            if canon in BLOCKED_LICENSES or _ND_PATTERN.search(_lkey(canon)):
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
