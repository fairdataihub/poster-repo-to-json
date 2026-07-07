#!/usr/bin/env python3
"""
Normalizers for deposit-imported fields (beyond licenses and dates).

Same rule: canonicalize the value, drop junk *values*, never the record.
Each normalizer mutates a record in place and returns True if it changed.

Currently: conference. (creators, subjects, publisher, formats to follow.)
"""
import json
import re
import unicodedata

from .date_normalize import normalize_date_value, normalize_publication_year


def _clean_name(name) -> str:
    n = unicodedata.normalize("NFKD", str(name or ""))
    n = "".join(c for c in n if not unicodedata.combining(c))
    return re.sub(r"[^a-zA-Z ,]", " ", n).lower()


def name_tokens(name) -> set:
    """Accent-stripped alpha tokens (len>=2) of a name."""
    return {t for t in _clean_name(name).replace(",", " ").split() if len(t) >= 2}


def creator_surnames(name) -> set:
    """Best-guess surname tokens: the pre-comma segment ("Surname, Given"), or the
    last token for "Given Surname". Used to match an extraction author against
    existing creators so the same person isn't double-added under a different
    name form (initials/accents/order)."""
    n = _clean_name(name)
    if "," in n:
        toks = [t for t in n.split(",", 1)[0].split() if len(t) >= 2]
    else:
        toks = [t for t in n.split() if len(t) >= 2]
        toks = toks[-1:] if toks else []
    return set(toks)

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


def _valid_name_part(p) -> bool:
    return bool(re.search(r"[A-Za-z]{2,}", str(p))) and not creator_name_is_junk(p)


def _looks_given(p) -> bool:
    """A plausible given-name segment: 1-2 tokens, each a real (2+ letter) word."""
    toks = str(p).split()
    return bool(toks) and len(toks) <= 2 and all(
        len(re.sub(r"[^A-Za-z]", "", t)) >= 2 for t in toks)


# A trailing "author list" abbreviation that stands in for the un-named rest of
# the authors, not a real person ("and others", "et al", "colleagues", ...).
_LIST_REMNANT_RE = re.compile(
    r"^(?:and\s+|&\s*)?(?:others?|et\s*\.?\s*al\.?|colleagues?|"
    r"co[-\s]?workers?|co[-\s]?authors?|the\s+team)$", re.I)


def split_lumped_name(name):
    """Split a single lumped multi-author name into individual names, but ONLY
    when the structure is unambiguous. Returns a list of >=2 names (or a single
    name when a list-abbreviation remnant like "and others"/"et al" was the only
    thing dropped), else None (leave the name untouched)."""
    s = str(name).strip()
    if creator_name_is_junk(s):   # e.g. a work-package label, not an author list
        return None
    has_org = bool(_ORG_RE.search(s))
    # 1) explicit multi-author delimiters. Skipped when an org keyword is present
    # (a single org "... Agency for X and Y" must not split on "and"). Drop trailing
    # list-abbreviation remnants ("... and others", "... et al"), then split when
    # parts carry a "Family, Given" comma OR every part is a full multi-word name.
    if not has_org:
        for delim in (r"\s+and\s+", r"\s*&\s*", r"\s*;\s*"):
            raw = [p.strip() for p in re.split(delim, s) if p.strip()]
            if len(raw) < 2:
                continue
            parts = [p for p in raw if not _LIST_REMNANT_RE.match(p)]
            dropped = len(parts) != len(raw)
            if not parts or not all(_valid_name_part(p) for p in parts):
                continue
            multi = any("," in p for p in parts) or all(
                len(p.split()) >= 2 for p in parts)
            if len(parts) >= 2 and multi:
                return parts
            # a lone real author left after dropping a remnant: emit it only when it
            # is a confident full name / comma-form.
            if dropped and len(parts) == 1 and (
                    "," in parts[0] or len(parts[0].split()) >= 2):
                return parts
    # 2) comma-separated lists
    if s.count(",") >= 3:
        parts = [p.strip(" .") for p in s.split(",") if p.strip(" .")]
        # An org-bearing name may only split if EVERY part is itself an org (a
        # list of distinct institutions) — not a single org's address ("Univ of
        # California, Berkeley, Dept of Physics, USA").
        if has_org and not all(_ORG_RE.search(p) for p in parts):
            return None
        # 2a) list of multi-word full names (each part already a complete name)
        if all(len(p.split()) >= 2 and _valid_name_part(p) for p in parts):
            return parts
        # 2b) "Family, Given, Family, Given, ..." pairs (persons, single-token parts)
        if (not has_org and len(parts) >= 4 and len(parts) % 2 == 0
                and all(_looks_given(parts[i]) for i in range(1, len(parts), 2))
                and all(_valid_name_part(parts[i]) for i in range(0, len(parts), 2))):
            return [f"{parts[i]}, {parts[i + 1]}" for i in range(0, len(parts), 2)]
    return None


def normalize_lumped_creators(record: dict) -> bool:
    """Split any confidently-splittable lumped creator name into separate
    creators, in place. Ambiguous lumps are left as-is."""
    cres = record.get("creators")
    if not isinstance(cres, list):
        return False
    out, changed = [], False
    for c in cres:
        nm = c.get("name") if isinstance(c, dict) else None
        if isinstance(c, dict) and nm:
            parts = split_lumped_name(nm)
            if parts:
                out.extend({"name": p} for p in parts)
                changed = True
                continue
        out.append(c)
    if changed:
        record["creators"] = out
        return True
    return False


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
    "not available", "not provided", "unnamed conference", "others", "other",
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
    # Coerce a bare-string conference (LLM sometimes emits just the name, and the
    # merge only wraps it when the deposit also has a meeting) into a schema-valid
    # object so it is validated AND reachable by sanitize_conference_dates.
    coerced = False
    if isinstance(conf, str):
        s = conf.strip()
        if not s:
            if "conference" in record:
                record.pop("conference", None)
                return True
            return False
        conf = {"conferenceName": s}
        record["conference"] = conf
        coerced = True
    if not isinstance(conf, dict):
        return False

    changed = coerced
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


# A leading keyword-list header to strip: the compound "Keywords and subjects", or a
# header word (Keywords/Subject(s)/Topic(s)) followed by ':' OR a space-dash-space (so
# "Subject-Verb Agreement" is NOT stripped, but "Keywords: x" and "Keywords - x" are).
_SUBJECT_HEADER_RE = re.compile(
    r"^\s*(?:"
    r"key\s*words?\s+and\s+subjects?"
    r"|(?:key\s*words?|subjects?|topics?)(?::|\s+[-–—]\s+)"
    r")\s*",
    re.I,
)
_BLOB_MIN_CHARS = 80
_BLOB_MIN_TOKENS = 8
# Split a single-spaced blob right AFTER a parenthetical acronym ("... (XR) Next").
_ACRONYM_SPLIT_RE = re.compile(r"(\([A-Z0-9][A-Za-z0-9]{0,7}\))\s+(?=[A-Z])")


def _split_top_level(value):
    """Bracket-aware split on top-level ',' / ';' OR any run of 2+ spaces. Delimiters
    inside (), [], {} are ignored (taxonomy terms stay whole); single spaces are kept
    (a normal phrase like "Machine Learning" is not split)."""
    parts, depth, cur, i, n = [], 0, "", 0, len(value)
    while i < n:
        ch = value[i]
        if ch in "([{":
            depth += 1
            cur += ch
            i += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
            cur += ch
            i += 1
        elif depth == 0 and ch in ",;":
            parts.append(cur)
            cur = ""
            i += 1
        elif depth == 0 and ch == " " and i + 1 < n and value[i + 1] == " ":
            parts.append(cur)
            cur = ""
            while i < n and value[i] == " ":
                i += 1
        else:
            cur += ch
            i += 1
    parts.append(cur)
    return [p.strip() for p in parts if p.strip()]


def _split_keyword_blob(part):
    """Only a long BLOB (> _BLOB_MIN_CHARS chars or > _BLOB_MIN_TOKENS tokens) is split
    further, and only when it holds 2+ parenthetical-acronym boundaries (a list like
    "Extended Reality (XR) Virtual Reality (VR) ..."), so a single legit subject with
    one parenthetical is never split."""
    if len(part) <= _BLOB_MIN_CHARS and len(part.split()) <= _BLOB_MIN_TOKENS:
        return [part]
    if len(_ACRONYM_SPLIT_RE.findall(part)) < 2:
        return [part]
    pieces = _ACRONYM_SPLIT_RE.sub(r"\1\n", part).split("\n")
    return [p.strip() for p in pieces if p.strip()]


def split_subject(value: str):
    """Split a lumped keyword string into individual keywords. Strips a leading
    "Keywords and subjects"/"Keywords:"/"Subjects:"/"Topics:" header, then splits on
    top-level commas/semicolons AND runs of 2+ spaces (bracket-aware), then splits a
    remaining long keyword BLOB after repeated parenthetical acronyms. Conservative:
    single-spaced normal phrases and bracketed taxonomy terms stay whole."""
    s = _SUBJECT_HEADER_RE.sub("", value, count=1)
    out = []
    for part in _split_top_level(s):
        out.extend(_split_keyword_blob(part))
    return out


def normalize_subjects(record: dict) -> bool:
    """Split lumped keywords (header/comma/semicolon/2+ spaces/acronym-blob), drop junk
    (empty/placeholder/URL/email/letterless), and dedup."""
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
            nv = unicodedata.normalize("NFKC", v).strip()
            if nv != v:
                changed = True
            v = nv
            if (not v or _is_placeholder(v) or not _has_letter(v)
                    or _URL_RE.search(v) or _EMAIL_RE.search(v)):
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


_ORCID_RE = re.compile(r"\b\d{4}-\d{4}-\d{4}-\d{3}[\dxX]\b")


def creator_name_is_junk(name) -> bool:
    """A creator name that is clearly not a real author: placeholder, unfilled
    template, conference/institution-as-author, url/email, an ORCID string, or a
    name with no real (2+ letter) token (bare initials like "M., A" or "D")."""
    n = str(name).strip() if name is not None else ""
    if not n:
        return True
    if (_is_placeholder(name) or n.lower().startswith("conference")
            or _EMAIL_RE.search(n) or _URL_RE.search(n) or _TEMPLATE_NAME_RE.search(n)
            or _ORCID_RE.search(n)):
        return True
    if re.fullmatch(r"et\s*\.?\s*al\.?", n, re.I):   # "et al" alone is not an author
        return True
    if re.match(r"^\s*WP\s*\d", n, re.I) or "work package" in n.lower():
        return True                                  # project work-package label
    return not re.search(r"[A-Za-z]{2,}", n)


_ORG_RE = re.compile(
    r"\b(universit|institut|college|department|laborator|hospital|clinic|"
    r"centre|center|school|faculty|academ|gmbh|corporation|company|"
    r"foundation|ministry|council|society|associat)"
    r"|\b(ltd|inc|university|college)\b", re.I)


def split_name_affiliation(name):
    """If a creator name has an appended ' - Organisation' (affiliation text that
    bled into the name field), split it off. Returns (clean_name, affiliation)."""
    s = str(name)
    if " - " in s:
        left, right = s.rsplit(" - ", 1)
        if left.strip() and _ORG_RE.search(right):
            return left.strip(), right.strip()
    return s, None


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
        # strip role markers / trailing artifacts ("... on behalf of X", "Name*")
        nm2 = re.sub(r"\s+on behalf of\b.*$", "", cc.get("name", ""), flags=re.I)
        nm2 = re.sub(r"[\*†‡\s]+$", "", nm2).strip()
        if nm2 != cc.get("name", "") and re.search(r"[A-Za-z]{2,}", nm2):
            cc["name"] = nm2
            changed = True
        # affiliation text bled into the name field ("Name - University of X")
        clean_nm, bled_aff = split_name_affiliation(cc.get("name", ""))
        if bled_aff:
            cc["name"] = clean_nm
            changed = True
            if not cc.get("affiliation"):
                cc["affiliation"] = [{"name": bled_aff}]
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


def _name_signals(name):
    """Accent-stripped (full 2+ letter tokens, single-letter initials)."""
    toks = _clean_name(name).replace(",", " ").split()
    return (frozenset(t for t in toks if len(t) >= 2),
            frozenset(t for t in toks if len(t) == 1))


def _given_compatible(ga, gb) -> bool:
    """Positionally compare two given-name token lists: full tokens must match,
    an initial matches a token with the same first letter, and a shorter list
    (missing trailing middle names) is fine."""
    for x, y in zip(ga, gb):
        if len(x) == 1 or len(y) == 1:
            if x[0] != y[0]:
                return False
        elif x != y:
            return False
    return True


def same_author(a, b) -> bool:
    """True if two creator names denote the same person under different forms
    (initials vs full given, accents, middle initials, family/given order).
    Conservative: two names that each carry a distinct full given/family token
    are different."""
    # Both in "Family, Given" form: compare family exactly, given positionally
    # (handles middle initials like "G. C." vs "Gina C.").
    fa_f, fa_g, ha = _fam_given(a)
    fb_f, fb_g, hb = _fam_given(b)
    if ha and hb:
        fam_a = set(_clean_name(fa_f).split())
        fam_b = set(_clean_name(fb_f).split())
        if not fam_a or fam_a != fam_b:
            return False
        return _given_compatible(_clean_name(fa_g).split(), _clean_name(fb_g).split())

    fa, ia = _name_signals(a)
    fb, ib = _name_signals(b)
    if not fa or not fb or not (fa & fb):
        return False
    only_a, only_b = fa - fb, fb - fa
    if only_a and only_b:
        return False
    if not only_a and not only_b:
        # identical full tokens: distinguish only if both give conflicting initials
        return not (ia and ib and ia.isdisjoint(ib))
    # exactly one side has extra full tokens; the other is the abbreviated form,
    # so its initials must be consistent with those extra tokens' first letters
    extra = only_a or only_b
    abbr = ib if only_a else ia
    if abbr and not (abbr <= {t[0] for t in extra}):
        return False
    return True


def _fam_given(name):
    """(family, given, has_comma) preserving original characters."""
    s = str(name).strip()
    if "," in s:
        fam, giv = s.split(",", 1)
        return fam.strip(), giv.strip(), True
    return s, "", False


def _accents(s):
    return sum(1 for ch in str(s) if ord(ch) > 127)


def _full_tokens(s):
    return [t for t in _clean_name(s).replace(",", " ").split() if len(t) >= 2]


def _merge_name(group_names):
    """Build a single "Family, Given" name for a set of duplicate forms: the most
    complete (accent-preferring) family from any comma-form, plus the fullest
    given drawn from any form. Falls back to the fullest original if no
    comma-form anchors the family."""
    parsed = [_fam_given(nm) for nm in group_names]
    fam_cands = [f for (f, _g, hc) in parsed if hc and f]
    if not fam_cands:
        return max(group_names, key=lambda nm: (len(_full_tokens(nm)), _accents(nm), len(nm)))
    fam = max(fam_cands, key=lambda s: (len(_full_tokens(s)), _accents(s), len(s)))
    fam_tok = set(_clean_name(fam).split())
    givens = []
    for nm, (f, g, hc) in zip(group_names, parsed):
        if hc:
            givens.append(g)
        else:  # no comma: given = tokens that aren't the family
            rem = [t for t in nm.split() if _clean_name(t).strip() not in fam_tok]
            givens.append(" ".join(rem))
    givens = [g for g in givens if g and g.strip()]
    if not givens:
        return fam
    given = max(givens, key=lambda g: (len(_full_tokens(g)), _accents(g), len(g)))
    return f"{fam}, {given}"


def _merge_dicts(lists, key):
    out, seen = [], set()
    for lst in lists:
        if not isinstance(lst, list):
            continue
        for item in lst:
            k = str(item.get(key, "") if isinstance(item, dict) else item).strip().lower()
            if not k or k in seen:
                continue
            seen.add(k)
            out.append(item)
    return out


def dedup_creators(record: dict) -> bool:
    """Collapse duplicate authors that the original pipeline listed twice (deposit
    form + extraction form). Same-person forms are merged into one "Family, Given"
    entry (keeping the earliest position) with their ORCID/affiliations pooled.
    Lumped multi-author names are left untouched."""
    cres = record.get("creators")
    if not isinstance(cres, list) or len(cres) < 2:
        return False
    items = [(k, c) for k, c in enumerate(cres) if isinstance(c, dict) and c.get("name")]
    m = len(items)
    if m < 2:
        return False
    names = [c.get("name") for _, c in items]
    parent = list(range(m))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(m):
        if _name_is_lumped(names[i]):
            continue
        for j in range(i + 1, m):
            if _name_is_lumped(names[j]):
                continue
            if same_author(names[i], names[j]):
                parent[find(i)] = find(j)

    root_members = {}
    for i in range(m):
        root_members.setdefault(find(i), []).append(i)
    if all(len(v) == 1 for v in root_members.values()):
        return False

    drop_local = set()
    for members in root_members.values():
        if len(members) == 1:
            continue
        members = sorted(members)
        keeper = members[0]
        base = max(members, key=lambda x: (len(items[x][1].get("nameIdentifiers") or []),
                                           1 if items[x][1].get("affiliation") else 0,
                                           len(names[x])))
        merged = dict(items[base][1])
        merged["name"] = _merge_name([names[x] for x in members])
        nid = _merge_dicts([items[x][1].get("nameIdentifiers") for x in members], "nameIdentifier")
        aff = _merge_dicts([items[x][1].get("affiliation") for x in members], "name")
        if nid:
            merged["nameIdentifiers"] = nid
        else:
            merged.pop("nameIdentifiers", None)
        if aff:
            merged["affiliation"] = aff
        else:
            merged.pop("affiliation", None)
        items[keeper] = (items[keeper][0], merged)
        drop_local.update(members[1:])

    drop_cres = {items[x][0] for x in drop_local}
    replace = {items[x][0]: items[x][1] for x in range(m) if x not in drop_local}
    new_cres = [replace.get(k, c) for k, c in enumerate(cres) if k not in drop_cres]
    if new_cres != cres:
        record["creators"] = new_cres
        return True
    return False


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


_ORCID_SCHEME_URI = "https://orcid.org"
_ROR_SCHEME_URI = "https://ror.org"
_ISNI_SCHEME_URI = "https://isni.org"
_GND_SCHEME_URI = "https://d-nb.info/gnd/"
_SCHEMA_URL = "https://posters.science/schema/v0.2/poster_schema.json"
# Underscore-prefixed fields the posters.science auto-index ingestion consumes and
# that must survive align_schema's internal-field strip.
_INGESTION_KEPT_FIELDS = frozenset({"_license_blocked"})


def align_schema(record: dict) -> bool:
    """Phase-1 schema alignment (see SCHEMA_ALIGNMENT_PLAN.md): fixed resource
    type, ORCID/ROR schemeURI presence + casing, and stripping the fields the
    target schema marks None (rightsList sub-fields, Submitted/Presented dates,
    publisher identifier). Deterministic; used go-forward in merge() and as the
    corpus backfill."""
    changed = False

    # $schema -> canonical (the LLM extraction's older $schema leaks via merge)
    if record.get("$schema") != _SCHEMA_URL:
        record["$schema"] = _SCHEMA_URL
        changed = True

    # strip internal/debug fields that leaked from the extraction (_source, etc.),
    # but PRESERVE underscore fields the posters.science ingestion consumes
    # (add-extracted-posters.ts reads `_license_blocked` to serve a placeholder
    # thumbnail for content-blocked posters).
    for k in [k for k in record if isinstance(k, str)
              and k.startswith("_") and k not in _INGESTION_KEPT_FIELDS]:
        record.pop(k, None)
        changed = True

    # drop null/empty conference (omit rather than emit null)
    if "conference" in record and not record.get("conference"):
        record.pop("conference", None)
        changed = True

    # domain: the auto-index ingestion (add-extracted-posters.ts) reads `domain`,
    # but the schema field is `researchField`. Mirror researchField -> domain so the
    # platform's domain column populates; researchField stays for schema conformance.
    rf = record.get("researchField")
    if rf and record.get("domain") != rf:
        record["domain"] = rf
        changed = True

    # types -> Poster / Poster
    t = record.get("types")
    if isinstance(t, dict) and (t.get("resourceType") != "Poster"
                                or t.get("resourceTypeGeneral") != "Poster"):
        record["types"] = {"resourceType": "Poster", "resourceTypeGeneral": "Poster"}
        changed = True

    # creators: default nameType (deposit type / DataCite / tag_org_creators win
    # first; "otherwise Personal" per the target schema); ORCID + affiliation schemeURI
    for c in record.get("creators") or []:
        if not isinstance(c, dict):
            continue
        if c.get("name") and not c.get("nameType"):
            c["nameType"] = "Personal"
            changed = True
        for nid in c.get("nameIdentifiers") or []:
            if not isinstance(nid, dict) or nid.get("nameIdentifierScheme") != "ORCID":
                continue
            val = nid.get("nameIdentifier")
            if val and not str(val).startswith("http"):
                m = re.search(r"\d{4}-\d{4}-\d{4}-\d{3}[\dxX]", str(val))
                if m:
                    nid["nameIdentifier"] = f"https://orcid.org/{m.group(0)}"
                    changed = True
            if nid.get("schemeURI") != _ORCID_SCHEME_URI:
                nid["schemeURI"] = _ORCID_SCHEME_URI
                changed = True
        # affiliation shape: list of {name:...} objects; wrap bare strings, drop null
        aff_val = c.get("affiliation")
        if aff_val is None and "affiliation" in c:
            c.pop("affiliation", None)
            changed = True
        elif isinstance(aff_val, list):
            fixed = []
            for a in aff_val:
                if isinstance(a, dict) and a.get("name"):
                    fixed.append(a)
                elif isinstance(a, str) and a.strip():
                    fixed.append({"name": a.strip()})
                    changed = True
                else:
                    changed = True
            if fixed != aff_val:
                if fixed:
                    c["affiliation"] = fixed
                else:
                    c.pop("affiliation", None)
                changed = True
        for aff in c.get("affiliation") or []:
            if not isinstance(aff, dict):
                continue
            if "schemeUri" in aff:
                aff.pop("schemeUri", None)
                changed = True
            if aff.get("affiliationIdentifier") and aff.get("schemeURI") != _ROR_SCHEME_URI:
                aff["schemeURI"] = _ROR_SCHEME_URI
                changed = True

    # rightsList -> keep only `rights` (strip rightsUri/Identifier/Scheme/schemeUri)
    rl = record.get("rightsList")
    if isinstance(rl, list):
        new = []
        for e in rl:
            if not isinstance(e, dict):
                continue
            r = e.get("rights") or e.get("rightsIdentifier")
            if r:
                new.append({"rights": r})
        if new != rl:
            if new:
                record["rightsList"] = new
            else:
                record.pop("rightsList", None)
            changed = True

    # dates -> drop Submitted (share-time; Poster-Sharing-only). Presented is
    # derived from the conference by ensure_presented_date (kept for parity).
    dates = record.get("dates")
    if isinstance(dates, list):
        kept = [d for d in dates if not (isinstance(d, dict)
                and d.get("dateType") == "Submitted")]
        if kept != dates:
            if kept:
                record["dates"] = kept
            else:
                record.pop("dates", None)
            changed = True

    # publisher identifier -> empty pre-publish
    pub = record.get("publisher")
    if isinstance(pub, dict):
        for k in ("publisherIdentifier", "publisherIdentifierScheme", "schemeURI", "schemeUri"):
            if k in pub:
                pub.pop(k, None)
                changed = True

    # identifiers[] should carry only the poster's OWN identifiers; drop any that
    # also appear in relatedIdentifiers (extraction reference DOIs that leaked in).
    ids = record.get("identifiers")
    rel = record.get("relatedIdentifiers")
    if isinstance(ids, list) and isinstance(rel, list):
        relset = {str(r.get("relatedIdentifier", "")).strip() for r in rel
                  if isinstance(r, dict) and r.get("relatedIdentifier")}
        if relset:
            new_ids = [i for i in ids if not (isinstance(i, dict)
                       and str(i.get("identifier", "")).strip() in relset)]
            if new_ids != ids:
                record["identifiers"] = new_ids
                changed = True

    return changed


def ensure_presented_date(record: dict) -> bool:
    """Derive a `Presented` date from the conference dates, for parity with the
    Poster-Sharing path. Uses the conference start (single) or start/end range,
    only when a conference with a real start date exists and no Presented date is
    already present."""
    conf = record.get("conference")
    if not isinstance(conf, dict):
        return False
    start = conf.get("conferenceStartDate")
    if not start or _is_placeholder(start):
        return False
    dates = record.get("dates")
    if not isinstance(dates, list):
        dates = []
    if any(isinstance(d, dict) and d.get("dateType") == "Presented" for d in dates):
        return False
    end = conf.get("conferenceEndDate")
    date_val = f"{start}/{end}" if (end and not _is_placeholder(end) and end != start) else str(start)
    name = conf.get("conferenceName")
    entry = {"date": date_val, "dateType": "Presented"}
    entry["dateInformation"] = f"Presented at {name}" if name else "Conference presentation dates"
    record["dates"] = list(dates) + [entry]
    return True


def _year_int(s):
    s = str(s or "")
    return int(s[:4]) if s[:4].isdigit() else None


def strip_invalid_dates(record: dict, max_year: int = 2026, min_year: int = 1900) -> bool:
    """Hard rule: a date whose year falls outside [min_year, max_year] is not a
    valid date, so strip it (never carry a bogus 2029/9999). Applies to every
    dates[].date (each side of a range) and to conference start/end/year. Runs
    BEFORE reconcile_publication_year so publicationYear only derives from valid
    dates; an empty dates[] is dropped rather than kept."""
    def bad(v):
        if not v:
            return False
        for part in str(v).split("/"):
            y = _year_int(part)
            if y is not None and not (min_year <= y <= max_year):
                return True
        return False

    changed = False
    dates = record.get("dates")
    if isinstance(dates, list):
        kept = [d for d in dates if not (isinstance(d, dict) and bad(d.get("date")))]
        if len(kept) != len(dates):
            if kept:
                record["dates"] = kept
            else:
                record.pop("dates", None)
            changed = True
    conf = record.get("conference")
    if isinstance(conf, dict):
        for k in ("conferenceStartDate", "conferenceEndDate", "conferenceYear"):
            if bad(conf.get(k)):
                conf.pop(k, None)
                changed = True
    return changed


def normalize_name_identifiers(record: dict) -> bool:
    """URL-normalize creator nameIdentifiers for the ROR/ISNI/GND schemes and add
    their canonical DataCite schemeURI, mirroring align_schema's ORCID handling
    (which remains the authority for ORCID and is not duplicated here). A bare id
    becomes its canonical URL form; a value already in http(s) form keeps its value
    but still gains a schemeURI. The URL scheme and unknown schemes are left
    untouched (no fabricated schemeURI); affiliation identifiers are never touched.
    Idempotent: every write is guarded so a second call returns False."""
    changed = False
    for c in record.get("creators") or []:
        if not isinstance(c, dict):
            continue
        for nid in c.get("nameIdentifiers") or []:
            if not isinstance(nid, dict):
                continue
            val = nid.get("nameIdentifier")
            if not isinstance(val, str) or not val:
                continue
            scheme = nid.get("nameIdentifierScheme")
            if scheme == "ROR":
                scheme_uri = _ROR_SCHEME_URI
                if not val.startswith("http"):
                    ident = val.strip().strip("/").rsplit("/", 1)[-1]
                    if ident and val != f"https://ror.org/{ident}":
                        nid["nameIdentifier"] = f"https://ror.org/{ident}"
                        changed = True
            elif scheme == "ISNI":
                scheme_uri = _ISNI_SCHEME_URI
                if not val.startswith("http"):
                    ident = val.strip().replace(" ", "").replace("-", "")
                    if ident and val != f"https://isni.org/isni/{ident}":
                        nid["nameIdentifier"] = f"https://isni.org/isni/{ident}"
                        changed = True
            elif scheme == "GND":
                scheme_uri = _GND_SCHEME_URI
                if not val.startswith("http"):
                    ident = val.strip().strip("/").rsplit("/", 1)[-1]
                    if ident and val != f"https://d-nb.info/gnd/{ident}":
                        nid["nameIdentifier"] = f"https://d-nb.info/gnd/{ident}"
                        changed = True
            else:
                continue  # ORCID: align_schema owns it; URL/unknown: leave untouched
            if nid.get("schemeURI") != scheme_uri:
                nid["schemeURI"] = scheme_uri
                changed = True
    return changed


_ORCID_ID_RE = re.compile(r"(\d{4}-\d{4}-\d{4}-\d{3}[\dxX])")


def _orcid_checksum_ok(orcid: str) -> bool:
    """Validate an ORCID's ISO 7064 MOD 11-2 check digit (last char, may be 'X').
    All-zero ids and anything not 15 base digits + one check char are invalid."""
    d = orcid.replace("-", "").upper()
    if len(d) != 16 or not re.fullmatch(r"\d{15}[\dX]", d):
        return False
    if d[:15] == "0" * 15:
        return False
    total = 0
    for ch in d[:15]:
        total = (total + int(ch)) * 2
    check = (12 - total % 11) % 11
    expected = "X" if check == 10 else str(check)
    return expected == d[15]


def drop_invalid_orcids(record: dict) -> bool:
    """Drop structurally-invalid ORCID nameIdentifiers from creators: those whose
    16-digit value fails the ISO 7064 MOD 11-2 check digit, are all-zeros, or carry
    no parseable ORCID. Only ORCID-scheme entries are candidates; a VALID ORCID is
    never removed. If a creator's nameIdentifiers list is emptied, the key is
    removed. Runs BEFORE align_schema's ORCID URL-normalization so it matches both
    bare and URL forms."""
    cres = record.get("creators")
    if not isinstance(cres, list):
        return False
    changed = False
    for c in cres:
        if not isinstance(c, dict):
            continue
        nids = c.get("nameIdentifiers")
        if not isinstance(nids, list):
            continue
        kept = []
        for nid in nids:
            if isinstance(nid, dict) and nid.get("nameIdentifierScheme") == "ORCID":
                m = _ORCID_ID_RE.search(str(nid.get("nameIdentifier", "")))
                if not m or not _orcid_checksum_ok(m.group(1)):
                    changed = True
                    continue
            kept.append(nid)
        if kept != nids:
            if kept:
                c["nameIdentifiers"] = kept
            else:
                c.pop("nameIdentifiers", None)
            changed = True
    return changed


def _has_letter(v) -> bool:
    """True iff the value contains at least one alphabetic character (Unicode-aware,
    so CJK/Cyrillic/Greek/Arabic names pass). The sole validity test for a creator
    name part: alphabetic short surnames ("Ng", "Li"), initials ("J."), and native-
    script names pass; pure digit/punctuation values ("2", "123", "-", "&") do not."""
    return v is not None and any(ch.isalpha() for ch in str(v))


# creator sub-fields whose letterless values are dropped in place (the `name`
# field, when letterless, drops the whole creator instead).
_CREATOR_LETTER_FIELDS = ("givenName", "familyName")


def drop_letterless_creator_fields(record: dict) -> bool:
    """Drop creator values that contain no letter (e.g. "2", "123", "-", "&"). A
    whole creator is dropped when its `name` is letterless (only while another
    creator remains -- never leave zero creators); an individual `givenName`/
    `familyName` is deleted when letterless, keeping the creator. Sole criterion is
    'has at least one letter', so real short surnames ("Ng", "Li") and native-script
    names are always kept. Idempotent."""
    cres = record.get("creators")
    if not isinstance(cres, list):
        return False
    changed = False
    processed = []
    for c in cres:
        if not isinstance(c, dict):
            processed.append(c)
            continue
        cc = c
        for part in _CREATOR_LETTER_FIELDS:
            if part in cc and not _has_letter(cc.get(part)):
                if cc is c:            # copy-on-write; keep untouched creators' identity
                    cc = dict(c)
                cc.pop(part, None)
                changed = True
        processed.append(cc)

    def _name_letterless(c) -> bool:
        return (isinstance(c, dict) and c.get("name") is not None
                and not _has_letter(c.get("name")))

    kept = [c for c in processed if not _name_letterless(c)]
    if not kept:                       # every name letterless -> never leave zero creators
        kept = processed
    if kept != processed:
        changed = True
    if changed:
        record["creators"] = kept
        return True
    return False


# ============ surgical affiliation-in-name cleanup ============
# High-precision institution/org markers (whole-word). Deliberately EXCLUDES stems
# that collide with real surnames ("Company", bare "AG"/"S.L"); whole-word matching
# never fires inside a surname (Torres-Company, Companys).
_INSTITUTION_MARKER_RE = re.compile(
    r"""(?xi)
    \bDept\b\.?
    | \b(?: Department | Universit\w* | Institut\w* | Hospital | Laborator\w*
          | Museum | Academy | Ministry | Observ\w* | Faculty | Consiglio
          | Commission | College | Foundation ) \b
    | \b(?: Centre | Center | Agency ) \s+ for \b
    | ;\s                                                  # real separator (not a "Jus;n" typo)
    | \b(?: GmbH | Ltd | Inc | SAS ) \b
    """,
)
# Sentence-final ". " boundary: period preceded by >=2 letters (so a lone initial
# "J." is not a boundary) and followed by whitespace.
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[^\W\d_]{2})\.\s+")
_ORG_SUFFIX_RE = re.compile(r"\b(?:GmbH|Ltd|Inc|SAS)\b", re.I)


def _looks_person_name(s) -> bool:
    s = str(s).strip()
    if not s or _INSTITUTION_MARKER_RE.search(s):
        return False
    if not re.search(r"[^\W\d_]{2,}", s):
        return False
    return len(s.split()) <= 6


def _family_is_lone_marker(name) -> bool:
    """A comma 'Family, Given' whose family is a single institution-marker token is a
    real person ('Hospital, Marc Antoni Pere'), never an org — regardless of the
    given-name shape/length. Protects such people from the affiliation detector."""
    fam, giv, has_comma = _fam_given(name)
    if not has_comma or not str(giv).strip():
        return False
    fam = str(fam).strip()
    return (len(fam.split()) == 1 and bool(_INSTITUTION_MARKER_RE.search(fam))
            and not _INSTITUTION_MARKER_RE.search(str(giv)))


def _is_clean_personal_name(name) -> bool:
    s = str(name).strip()
    if not s or ";" in s or ". " in s or _ORG_SUFFIX_RE.search(s):
        return False
    fam, giv, has_comma = _fam_given(s)
    if not has_comma or not giv or len(name_tokens(s)) > 3:
        return False
    return _looks_given(giv) and bool(re.search(r"[^\W\d_]{2,}", fam))


def _name_has_affiliation_marker(name) -> bool:
    n = str(name).strip() if name is not None else ""
    if not n or _is_clean_personal_name(n) or _family_is_lone_marker(n):
        return False
    return bool(_INSTITUTION_MARKER_RE.search(n))


def _is_definite_org(name) -> bool:
    """Strong organization signal for the (b) org-tag branch: a legal-form suffix, a
    ';' multi-part, 2+ marker hits, or an institution marker in org phrasing
    (of/for/the). A lone trailing marker word is NOT enough (precision over recall,
    so 'Given Surname' where the surname is a marker word is never flipped)."""
    s = str(name).strip()
    if _ORG_SUFFIX_RE.search(s) or ";" in s:
        return True
    if len(list(_INSTITUTION_MARKER_RE.finditer(s))) >= 2:
        return True
    return bool(_INSTITUTION_MARKER_RE.search(s)
                and re.search(r"\b(?:of|for|the)\b", s, re.I))


def split_person_affiliation(name):
    """Extract (person, affiliation) when a person's name carries an affiliation.
    Two forms: (1) sentence-final '. ' — 'Person Name. Affiliation'; (2) comma —
    'Surname, Affiliation' where the tail carries an institution marker and is NOT a
    plausible given name (so 'Davies, University of Bremen' splits but 'Davies, John'
    does not). The period form is tried first. Returns (None, None) for a pure org."""
    s = str(name).strip()
    for m in _SENTENCE_BOUNDARY_RE.finditer(s):
        left = s[:m.start()].strip(" .,")
        right = s[m.end():].strip(" .,")
        if not left or not right:
            continue
        if _looks_person_name(left) and _INSTITUTION_MARKER_RE.search(right):
            return left, right
    if "," in s:
        head, tail = s.split(",", 1)
        head = head.strip(" ."); tail = tail.strip(" .")
        if (head and tail and len(head.split()) <= 3 and _looks_person_name(head)
                and _INSTITUTION_MARKER_RE.search(tail) and not _looks_given(tail)):
            return head, tail
    return None, None


def _add_affiliation(creator: dict, aff_text: str) -> None:
    aff_text = str(aff_text).strip()
    if not aff_text:
        return
    affs = creator.get("affiliation")
    if not isinstance(affs, list):
        affs = []
    key = aff_text.lower()
    for a in affs:
        an = a.get("name") if isinstance(a, dict) else a
        if isinstance(an, str) and an.strip().lower() == key:
            return
    creator["affiliation"] = list(affs) + [{"name": aff_text}]


def normalize_affiliation_in_name(record: dict) -> bool:
    """Surgically fix creators whose NAME holds an affiliation/organization string:
    (a) 'Person Name. Affiliation' -> keep the person, move the tail into
    affiliation[]; (b) a DEFINITE organization -> set nameType='Organizational'.
    High precision: real surnames are never split, and a person whose family is a
    marker word is never tagged. Non-destructive (relocates/tags only, never drops).
    Idempotent."""
    cres = record.get("creators")
    if not isinstance(cres, list):
        return False
    out, changed = [], False
    for c in cres:
        if not isinstance(c, dict):
            out.append(c)
            continue
        n = str(c.get("name")).strip() if c.get("name") is not None else ""
        if not _name_has_affiliation_marker(n):
            out.append(c)
            continue
        person, aff = split_person_affiliation(n)
        if person and aff:                              # (a) affiliation bled into name
            cc = dict(c)
            cc["name"] = person
            _add_affiliation(cc, aff)
            out.append(cc)
            changed = True
        elif (_is_definite_org(n) and not _family_is_lone_marker(n)
              and c.get("nameType") != "Organizational"):   # (b) whole name is an org
            cc = dict(c)
            cc["nameType"] = "Organizational"
            out.append(cc)
            changed = True
        else:
            out.append(c)
    if changed:
        record["creators"] = out
        return True
    return False


def _creator_in_deposit(name, deposit_token_sets) -> bool:
    """True if a flagged creator plausibly corresponds to a raw deposit creator (so
    it must be KEPT). Conservative: keeps on any decent token overlap."""
    person, _ = split_person_affiliation(name)
    toks = name_tokens(person if person else name)
    if not toks:
        return True                       # indeterminate -> never drop
    for d in deposit_token_sets:
        if d and (toks <= d or d <= toks or len(toks & d) >= 2):
            return True
    return False


def _is_semicolon_person_list(name) -> bool:
    """A ';'-separated list whose parts are mostly NOT organizations (a lumped author
    list like 'Bidari R; Azlina D; Rafidah' that the splitter missed) — never dropped
    as an org."""
    s = str(name)
    if "; " not in s:
        return False
    parts = [p.strip() for p in s.split(";") if p.strip()]
    if len(parts) < 2:
        return False
    orgish = sum(1 for p in parts if _INSTITUTION_MARKER_RE.search(p))
    return orgish < len(parts)


def drop_llm_affiliation_creators(record: dict, deposit_creator_names) -> bool:
    """Backfill (needs raw DEPOSIT creator names): drop creators whose NAME is an
    affiliation/org string the LLM ADDED — carries an institution marker yet matches
    no raw deposit creator. Deposit-sourced org/affiliation creators are KEPT.
    DESTRUCTIVE, so guarded: no-op without positive deposit evidence, conservative
    token-overlap match, and NEVER empties creators. Run BEFORE
    normalize_affiliation_in_name (on intact names)."""
    if not deposit_creator_names:                 # never drop without deposit evidence
        return False
    cres = record.get("creators")
    if not isinstance(cres, list):
        return False
    dep = [name_tokens(x) for x in deposit_creator_names if x]
    if not any(dep):
        return False
    kept = []
    for c in cres:
        nm = str((c or {}).get("name") or "").strip()
        # Drop only a PURE organization (no extractable person — a 'Surname,
        # Affiliation' or 'Name. Affiliation' is split, not dropped) that carries an
        # institution marker and matches no deposit creator.
        drop = (_name_has_affiliation_marker(nm)
                and split_person_affiliation(nm) == (None, None)
                and not _is_semicolon_person_list(nm)
                and not _creator_in_deposit(nm, dep))
        if not drop:
            kept.append(c)
    if kept == cres or not kept:                  # no change, or would empty -> refuse
        return False
    record["creators"] = kept
    return True


def normalize_affiliation_names(record: dict) -> bool:
    """Clean and split creator affiliation NAMES in place. Per creator: NFKC-normalize
    each name; drop any entry whose name holds no letter (Unicode-aware via _has_letter,
    so CJK/accented names survive); and split a semicolon-lumped multi-institution name
    into separate entries. Splits ONLY on ';' (never comma -- a single-institution
    address like "University of California, Berkeley, CA, USA" stays one -- and never
    'and'/'&'), and ONLY when the entry has no affiliationIdentifier (a ROR-bearing
    lumped entry is kept intact). Identical resulting names within a creator are deduped
    (an identifier-bearing form wins). Input form preserved. Record-only, idempotent,
    never drops a creator."""
    cres = record.get("creators")
    if not isinstance(cres, list):
        return False
    changed = False
    out = []
    for c in cres:
        if not isinstance(c, dict) or not isinstance(c.get("affiliation"), list):
            out.append(c)
            continue
        affs = c["affiliation"]
        new_affs, new_hids, seen = [], [], {}
        for a in affs:
            if isinstance(a, dict):
                name, has_id = a.get("name"), bool(a.get("affiliationIdentifier"))
            elif isinstance(a, str):
                name, has_id = a, False
            else:
                name, has_id = None, False
            if not isinstance(name, str):
                new_affs.append(a)
                new_hids.append(False)
                continue
            parts = [name] if has_id else name.split(";")
            for part in parts:
                nv = unicodedata.normalize("NFKC", part).strip()
                if not _has_letter(nv):
                    continue
                key = nv.lower()
                entry = {**a, "name": nv} if isinstance(a, dict) else nv
                if key in seen:
                    pos = seen[key]
                    if has_id and not new_hids[pos]:
                        new_affs[pos], new_hids[pos] = entry, True
                    continue
                seen[key] = len(new_affs)
                new_affs.append(entry)
                new_hids.append(has_id)
        if new_affs != affs:
            changed = True
            cc = dict(c)
            if new_affs:
                cc["affiliation"] = new_affs
            else:
                cc.pop("affiliation", None)
            out.append(cc)
        else:
            out.append(c)
    if changed:
        record["creators"] = out
        return True
    return False


_TITLE_SECTION_HEADERS = frozenset({
    "aim", "aims", "introduction", "background", "methods", "method", "results",
    "conclusion", "conclusions", "discussion", "objective", "objectives",
    "summary", "abstract", "materials and methods", "results and discussion",
    "acknowledgements", "acknowledgments", "references", "overview", "purpose",
    "hypothesis",
})
# merger-level title placeholders (field_normalize must not import from merger).
_TITLE_PLACEHOLDERS = frozenset({
    "untitled poster", "poster title", "main poster title", "scientific poster",
    "untitled", "poster",
})
_TITLE_FILENAME_RE = re.compile(
    r"\.(pdf|docx?|pptx?|xlsx?|jpe?g|png|tiff?|gif|zip|csv|txt|eps|ai|svg)$", re.I)
_MAX_TITLE_LEN = 250
_MIN_TITLE_LEN = 5


def _alnum_count(s) -> int:
    return sum(1 for ch in str(s) if ch.isalnum())


def title_is_bad_llm(title) -> bool:
    """True if a poster2json (LLM) title is CLEARLY wrong and should fall back to the
    deposit title: (i) too short (<=4 alphanumeric chars, or a single token <=5 chars);
    (ii) too long / a paragraph (>250 chars, or a >=12-word sentence ending in '.');
    (iii) a bare section-header word. A normal poster title is never flagged."""
    if not isinstance(title, str):
        return False
    s = title.strip()
    low = s.lower().rstrip(" .:;,").strip()
    if low in _TITLE_SECTION_HEADERS:
        return True
    if _alnum_count(s) <= 4:
        return True
    if len(s.split()) == 1 and len(s) <= 5:
        return True
    if len(s) > _MAX_TITLE_LEN:
        return True
    return False


def title_is_reasonable(title) -> bool:
    """True if a candidate DEPOSIT title is usable as a replacement: real, non-
    placeholder, non-filename, sane length (~5..250), and not itself a bad title."""
    if not isinstance(title, str):
        return False
    s = title.strip()
    if not (_MIN_TITLE_LEN <= len(s) <= _MAX_TITLE_LEN):
        return False
    if _is_placeholder(s) or _TITLE_FILENAME_RE.search(s):
        return False
    if s.lower() in _TITLE_PLACEHOLDERS:
        return False
    return not title_is_bad_llm(s)


def _title_str(entry):
    if isinstance(entry, dict):
        return entry.get("title")
    if isinstance(entry, str):
        return entry
    return None


def replace_bad_llm_title(record: dict, deposit_title=None) -> bool:
    """Fall back to the DEPOSIT title when titles[0] (LLM) is clearly bad (a section-
    header fragment or an abstract paragraph). Needs the raw deposit title passed in.
    No-op unless the deposit title is itself reasonable, so a good LLM title is never
    worsened and a bad/placeholder/missing deposit title never overwrites anything.
    Idempotent."""
    if not title_is_reasonable(deposit_title):
        return False
    titles = record.get("titles")
    if not isinstance(titles, list) or not titles:
        return False
    cur = _title_str(titles[0])
    if not title_is_bad_llm(cur):
        return False
    new = deposit_title.strip()
    if cur is not None and new == cur:
        return False
    entry = titles[0]
    titles[0] = {**entry, "title": new} if isinstance(entry, dict) else {"title": new}
    return True


_ISO_DATE_RE = re.compile(r"(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?")


def _iso_canonical(part):
    """Canonical zero-padded ISO ("YYYY"/"YYYY-MM"/"YYYY-MM-DD") for a strict ISO date
    part, else None (free text, year outside 1900..2100, or bad month/day)."""
    m = _ISO_DATE_RE.fullmatch(str(part).strip())
    if not m:
        return None
    y = int(m.group(1))
    if not (1900 <= y <= 2100):
        return None
    mo = int(m.group(2)) if m.group(2) is not None else None
    dd = int(m.group(3)) if m.group(3) is not None else None
    if mo is not None and not (1 <= mo <= 12):
        return None
    if dd is not None and not (1 <= dd <= 31):
        return None
    if mo is not None and dd is not None:
        return f"{y:04d}-{mo:02d}-{dd:02d}"
    if mo is not None:
        return f"{y:04d}-{mo:02d}"
    return f"{y:04d}"


def collapse_multidate_ranges(record: dict) -> bool:
    """Collapse a malformed multi-date dates[].date (3+ '/'-separated ISO parts) to a
    canonical 'min/max' range (or a single date if only one distinct date survives
    de-dup). Only strict ISO parts count; non-ISO parts are dropped. Valid single dates
    and 2-part ranges (<3 parts) are left untouched. Idempotent."""
    dates = record.get("dates")
    if not isinstance(dates, list):
        return False
    changed = False
    for d in dates:
        if not isinstance(d, dict):
            continue
        val = d.get("date")
        if not isinstance(val, str) or val.count("/") < 2:
            continue
        canon = [c for c in (_iso_canonical(p) for p in val.split("/")) if c]
        if not canon:
            continue
        distinct = set(canon)
        new = next(iter(distinct)) if len(distinct) == 1 else f"{min(canon)}/{max(canon)}"
        if new != val:
            d["date"] = new
            changed = True
    return changed


_VERSION_MAX_LEN = 25
# Promo/spam markers a real version never carries. The phone-number run uses a
# separator class WITHOUT '.' so dotted numeric versions (1.0.0.20190315) survive.
_VERSION_SPAM_RE = re.compile(
    r"[™®℠]"
    r"|\(\s*(?:tm|r|sm|c)\s*\)"
    r"|\d(?:[\s()+\-]*\d){8,}"
    r"|\b(?:contact|hotline|helpline|toll[-\s]?free|call\s+now|customer\s+"
    r"(?:service|care|support)|phone\s+num|complete\s+list\s+of|1[-\s]?800)\b",
    re.I,
)


def _version_is_junk(v: str) -> bool:
    s = v.strip()
    if not s:
        return True
    if re.match(r"https?://|www\.", s, re.I):
        return True
    if len(s) > _VERSION_MAX_LEN:
        return True
    return bool(_VERSION_SPAM_RE.search(s))


def normalize_version(record: dict) -> bool:
    """Drop a top-level `version` that is clearly not a version: a URL, an over-long
    sentence (>25 chars), or spam (trademark symbol / phone-number-like run / marketing
    phrasing). Short plausible versions ("1.0","v2","1.2.3","2019-03") are kept.
    Idempotent."""
    v = record.get("version")
    if not isinstance(v, str):
        return False
    if _version_is_junk(v):
        record.pop("version", None)
        return True
    return False


_RELID_PLACEHOLDER = frozenset({"n/a", "na", "url", "null", "none", "tax"})


def _relid_is_junk(rel_id) -> bool:
    """True if a relatedIdentifier is clearly junk: missing/non-string, a placeholder
    word, <=3 alphanumerics, an encoded comma (%2C = mashed author list), or an encoded
    space (%20) in a NON-URL value. A single %20 in an http(s) URL is a real path space
    and is kept."""
    if not isinstance(rel_id, str):
        return True
    s = rel_id.strip()
    if not s or s.lower() in _RELID_PLACEHOLDER:
        return True
    low = s.lower()
    if "%2c" in low:
        return True
    if "%20" in low and not low.startswith(("http://", "https://")):
        return True
    return len(re.sub(r"[^A-Za-z0-9]", "", s)) <= 3


def drop_junk_related_identifiers(record: dict) -> bool:
    """Drop relatedIdentifiers[] entries whose relatedIdentifier is junk (placeholder
    word / <=3 alnum / encoded-comma / encoded-space-in-non-URL). Clean enum sub-fields
    are never touched; non-dict entries preserved; an emptied list drops the key.
    Idempotent."""
    rels = record.get("relatedIdentifiers")
    if not isinstance(rels, list):
        return False
    kept = [r for r in rels if not (
        isinstance(r, dict) and _relid_is_junk(r.get("relatedIdentifier")))]
    if kept == rels:
        return False
    if kept:
        record["relatedIdentifiers"] = kept
    else:
        record.pop("relatedIdentifiers", None)
    return True


_DESC_JSON_BLOB_PREFIXES = ('{"references"', "{'references'")


def _description_is_junk(val) -> bool:
    """True if a description is junk: non-string, letterless, <=2 chars, or a raw JSON
    blob (references dump, or a value that json.loads-parses to a dict/list). A
    '{'-leading value that is NOT valid JSON is real prose and is kept."""
    if not isinstance(val, str):
        return True
    s = val.strip()
    if len(s) <= 2 or not _has_letter(s):
        return True
    if s.startswith(_DESC_JSON_BLOB_PREFIXES):
        return True
    if s[0] in "{[":
        try:
            parsed = json.loads(s)
        except (ValueError, TypeError):
            return False
        if isinstance(parsed, (dict, list)):
            return True
    return False


def drop_junk_descriptions(record: dict) -> bool:
    """Drop junk descriptions[] entries (letterless / <=2 chars / raw JSON blob). Real
    prose (even long / native-script) is kept; an emptied list drops the key; dropping
    the Abstract but keeping an Other is fine. Idempotent."""
    descs = record.get("descriptions")
    if not isinstance(descs, list):
        return False
    kept = [d for d in descs
            if not _description_is_junk(d.get("description") if isinstance(d, dict) else d)]
    if len(kept) == len(descs):
        return False
    if kept:
        record["descriptions"] = kept
    else:
        record.pop("descriptions", None)
    return True


def reconcile_publication_year(record: dict) -> bool:
    """publicationYear must follow the deposit-authoritative Issued date (else the
    Presented date). Corrects LLM years that survived the merge (the 2025-
    hallucination era left publicationYear disagreeing with a correct Issued date)."""
    dates = record.get("dates") or []

    def first(dt):
        return next((d.get("date") for d in dates if isinstance(d, dict)
                     and d.get("dateType") == dt and d.get("date")), None)

    src = first("Issued") or first("Presented")
    if not src:
        return False
    y = normalize_publication_year(str(src)[:4])
    if y is None:
        return False
    if record.get("publicationYear") != y:
        record["publicationYear"] = y
        return True
    return False


def sanitize_conference_dates(record: dict) -> bool:
    """Drop hallucinated future conference dates and any Presented date derived
    from them. A conference/presentation year more than one year after the
    deposit publicationYear is not credible (LLM 2025-hallucination era). Run
    AFTER reconcile_publication_year so the anchor year is deposit-authoritative."""
    py = record.get("publicationYear")
    if not isinstance(py, int):
        return False
    cap = py + 1
    changed = False
    conf = record.get("conference")
    if isinstance(conf, dict):
        for k in ("conferenceStartDate", "conferenceEndDate", "conferenceYear"):
            y = _year_int(conf.get(k))
            if y and y > cap:
                conf.pop(k, None)
                changed = True
    dates = record.get("dates")
    if isinstance(dates, list):
        kept = [d for d in dates if not (isinstance(d, dict)
                and d.get("dateType") == "Presented"
                and (_year_int(d.get("date")) or 0) > cap)]
        if len(kept) != len(dates):
            record["dates"] = kept
            changed = True
    return changed


_FUNDER_ACK_RE = re.compile(
    r"\b(?:supported by|funded (?:by|in part|through)|"
    r"this (?:study|research|work|report|project) (?:was|is|were)|"
    r"we (?:thank|acknowledge|are grateful|gratefully))\b", re.I)


def drop_junk_funding(record: dict) -> bool:
    """Clean fundingReferences[]. Drop an entry whose funderName is junk -- no letter, or
    an acknowledgement sentence the LLM misread as a funder ("This study was supported by
    ...") -- UNLESS it carries a funderIdentifier (a deposit-anchored funder is kept). Real
    funder names, even long official agency names, are kept (the signal is acknowledgement
    phrasing, not length). Also clears an awardNumber with no alphanumeric char (numeric
    grant numbers are kept). Idempotent."""
    funds = record.get("fundingReferences")
    if not isinstance(funds, list):
        return False
    changed = False
    kept = []
    for fr in funds:
        if not isinstance(fr, dict):
            kept.append(fr)
            continue
        fn = fr.get("funderName")
        has_id = bool(fr.get("funderIdentifier"))
        name_junk = isinstance(fn, str) and (not _has_letter(fn) or _FUNDER_ACK_RE.search(fn))
        if name_junk and not has_id:
            changed = True
            continue
        an = fr.get("awardNumber")
        if isinstance(an, str) and not re.search(r"[A-Za-z0-9]", an):
            fr = {k: v for k, v in fr.items() if k != "awardNumber"}
            changed = True
        kept.append(fr)
    if not changed:
        return False
    if kept:
        record["fundingReferences"] = kept
    else:
        record.pop("fundingReferences", None)
    return True


def clean_conference_junk(record: dict) -> bool:
    """Clear junk sub-fields of the flattened conference object: a conferenceName that is
    no-letter or <=2 alphanumeric chars (a real name is longer), and a conferenceAcronym
    that is no-letter, a single char, or >30 chars (a full title misread as an acronym).
    The conference object and its dates are otherwise kept. Idempotent."""
    conf = record.get("conference")
    if not isinstance(conf, dict):
        return False
    changed = False
    cn = conf.get("conferenceName")
    if isinstance(cn, str) and (not _has_letter(cn) or len(re.sub(r"[^A-Za-z0-9]", "", cn)) <= 2):
        conf.pop("conferenceName", None)
        changed = True
    ca = conf.get("conferenceAcronym")
    if isinstance(ca, str) and (not _has_letter(ca) or len(re.sub(r"[^A-Za-z0-9]", "", ca)) <= 1
                                or len(ca) > 30):
        conf.pop("conferenceAcronym", None)
        changed = True
    return changed


def drop_junk_sections(record: dict) -> bool:
    """Clean content.sections[] (LLM-extracted). Drop a section whose title AND content
    are both junk (no letter / empty). Strip a no-letter junk sectionTitle ("0") while
    keeping its content. Demote an over-long (>200 char) sectionTitle -- really content
    mis-split into the title slot -- into sectionContent when content is empty, else drop
    the redundant title. Real sections and real content are kept. Idempotent."""
    content = record.get("content")
    if not isinstance(content, dict):
        return False
    sections = content.get("sections")
    if not isinstance(sections, list):
        return False
    changed = False
    kept = []
    for s in sections:
        if not isinstance(s, dict):
            kept.append(s)
            continue
        title = s.get("sectionTitle")
        body = s.get("sectionContent")
        body_ok = isinstance(body, str) and _has_letter(body) and len(body.strip()) > 2
        title_letter = isinstance(title, str) and _has_letter(title)
        title_junk = isinstance(title, str) and not _has_letter(title)
        title_overlong = title_letter and len(title.strip()) > 200
        if not title_letter and not body_ok:
            changed = True
            continue
        if title_junk:
            s = {k: v for k, v in s.items() if k != "sectionTitle"}
            changed = True
        elif title_overlong:
            # an over-long "title" is really content mis-slotted: DEMOTE it into
            # sectionContent (never drop -- preserve every character of real text).
            merged = title if not (isinstance(body, str) and body.strip()) \
                else title.rstrip() + "\n\n" + body
            s = {**s, "sectionContent": merged}
            s.pop("sectionTitle", None)
            changed = True
        kept.append(s)
    if not changed:
        return False
    if kept:
        content["sections"] = kept
    else:
        content.pop("sections", None)
    return True


def drop_junk_captions(record: dict) -> bool:
    """Drop junk entries from imageCaptions[] and tableCaptions[] (LLM-extracted): a
    caption that is no-letter (punctuation / bullet blob) or <=2 chars. Real captions,
    even long ones, are kept. An emptied list drops its key. Idempotent."""
    changed = False
    for key in ("imageCaptions", "tableCaptions"):
        caps = record.get(key)
        if not isinstance(caps, list):
            continue
        kept = []
        for c in caps:
            cap = c.get("caption") if isinstance(c, dict) else c
            if isinstance(cap, str) and (not _has_letter(cap) or len(cap.strip()) <= 2):
                continue
            kept.append(c)
        if len(kept) != len(caps):
            changed = True
            if kept:
                record[key] = kept
            else:
                record.pop(key, None)
    return changed
