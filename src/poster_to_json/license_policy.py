#!/usr/bin/env python3
"""License policy for the posters.science corpus (package copy -- single source of truth).

Defines which licenses allow redistribution of poster2json-extracted content (text,
captions, structured sections) and which require stripping that content and keeping only
repository metadata. License strings are normalized (fold case/spacing/punctuation +
resolve aliases to canonical ids) before matching, so format variants like 'CC BY 4.0'
resolve to 'CC-BY-4.0'.

`enforce_license` is wired into MetadataMerger.merge so enforcement is a DEFAULT step of
the pipeline, not a separate manual pass. See docs/LICENSE_POLICY.md.
"""
import re
import unicodedata

# ============================
# ALLOWED LICENSES (whitelist) -- derivatives / redistribution permitted
# ============================
ALLOWED_LICENSES = frozenset({
    "CC0-1.0",
    "CC-BY-4.0", "CC-BY-3.0", "CC-BY-2.5", "CC-BY-2.0", "CC-BY-1.0",
    "CC-BY-SA-4.0", "CC-BY-SA-3.0", "CC-BY-SA-2.5", "CC-BY-SA-2.0", "CC-BY-SA-1.0",
    "CC-BY-NC-4.0", "CC-BY-NC-3.0", "CC-BY-NC-2.5", "CC-BY-NC-2.0", "CC-BY-NC-1.0",
    "CC-BY-NC-SA-4.0", "CC-BY-NC-SA-3.0", "CC-BY-NC-SA-2.5", "CC-BY-NC-SA-2.0", "CC-BY-NC-SA-1.0",
    "MIT", "Apache-2.0", "GPL-3.0", "GPL-2.0", "LGPL-3.0", "LGPL-2.1",
    "BSD-2-Clause", "BSD-3-Clause", "MPL-2.0", "ISC", "Unlicense",
    "other-open", "other-pd",
})

# ============================
# BLOCKED LICENSES (blocklist) -- no derivative/redistribution rights
# ============================
BLOCKED_LICENSES = frozenset({
    "CC-BY-ND-4.0", "CC-BY-ND-3.0", "CC-BY-ND-2.5", "CC-BY-ND-2.0", "CC-BY-ND-1.0",
    "CC-BY-NC-ND-4.0", "CC-BY-NC-ND-3.0", "CC-BY-NC-ND-2.5", "CC-BY-NC-ND-2.0", "CC-BY-NC-ND-1.0",
    "In Copyright", "all rights reserved", "All Rights Reserved",
    "Copyright not evaluated", "Copyright undetermined",
    "other-at", "other-closed", "other-nc", "other",
})

_ND_PATTERN = re.compile(r"ccby(nc)?nd", re.IGNORECASE)


def _lkey(s):
    """fair-ly-style folding: strip diacritics/case and every non-alphanumeric, so
    'CC BY 4.0', 'cc-by-4.0', 'CC_BY_4.0' all fold to the same key 'ccby40'."""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


_CANON_BY_KEY = {_lkey(lic): lic for lic in list(ALLOWED_LICENSES) + list(BLOCKED_LICENSES)}

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

_REGION_RE = re.compile(r"(\d)(us|uk|scotland|de|es|fr|it|nl|jp|au|ca|pt|br|igo|int|nz|za)$")


def normalize_license(val):
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

    Each license string is normalized to a canonical id before matching. If ANY entry
    is allowed, returns 'allowed' (dual licensing). Returns 'blocked' if none are allowed
    and at least one is blocked. Returns 'unknown' if empty/null or nothing resolves --
    enforcement treats 'unknown' the same as 'blocked'.
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


# Fields added by poster2json extraction (derived from poster content), stripped when the
# license blocks derivative redistribution.
EXTRACTED_CONTENT_FIELDS = ["content", "imageCaptions", "tableCaptions", "researchField", "domain"]


def strip_extracted_content(data: dict) -> dict:
    """Return a copy of a merged JSON with poster2json-derived content removed.

    Keeps repository metadata (identifiers, creators, titles, dates, publisher, rightsList,
    fundingReferences, and the deposit Abstract) but removes extracted content
    (sections/captions/researchField/domain) and the LLM-generated 'Other' description that
    constitute a derivative work. Sets _license_blocked.
    """
    result = dict(data)
    for field in EXTRACTED_CONTENT_FIELDS:
        result.pop(field, None)
    descs = result.get("descriptions")
    if isinstance(descs, list):
        kept = [d for d in descs if isinstance(d, dict) and d.get("descriptionType") == "Abstract"]
        if kept:
            result["descriptions"] = kept
        else:
            result.pop("descriptions", None)
    else:
        result.pop("descriptions", None)
    result["_license_blocked"] = True
    return result


def enforce_license(record: dict) -> bool:
    """Enforce the license policy on a merged record IN PLACE.

    If the license is not allowed (blocked or unknown/unlisted -> default-deny), strip the
    poster-derived content and set _license_blocked. No-op for allowed or already-stripped
    records. Returns True if the record was stripped. This is the default pipeline step
    (wired into MetadataMerger.merge)."""
    if record.get("_license_blocked"):
        return False
    if classify_license(record.get("rightsList")) == "allowed":
        return False
    stripped = strip_extracted_content(record)
    record.clear()
    record.update(stripped)
    return True
