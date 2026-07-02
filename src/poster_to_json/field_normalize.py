#!/usr/bin/env python3
"""
Normalizers for deposit-imported fields (beyond licenses and dates).

Same rule: canonicalize the value, drop junk *values*, never the record.
Each normalizer mutates a record in place and returns True if it changed.

Currently: conference. (creators, subjects, publisher, formats to follow.)
"""
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


def split_lumped_name(name):
    """Split a single lumped multi-author name into individual names, but ONLY
    when the structure is unambiguous. Returns a list of >=2 names, else None
    (leave the name untouched)."""
    s = str(name).strip()
    # Never split an organisation name ("... Agency for X and Y, Center for Z").
    if _ORG_RE.search(s):
        return None
    # 1) explicit multi-author delimiters. Require a "Family, Given" comma in a
    # part, or 3+ multi-word parts, so org names ("Ben and Jerry Foundation")
    # aren't mistaken for an author list.
    for delim in (r"\s+and\s+", r"\s*&\s*", r"\s*;\s*"):
        parts = [p.strip() for p in re.split(delim, s) if p.strip()]
        if len(parts) >= 2 and all(_valid_name_part(p) for p in parts):
            if any("," in p for p in parts) or (
                    len(parts) >= 3 and all(len(p.split()) >= 2 for p in parts)):
                return parts
    # 2) comma-separated lists
    if s.count(",") >= 3:
        parts = [p.strip(" .") for p in s.split(",") if p.strip(" .")]
        # 2a) list of multi-word full names (each part is already a complete name)
        if all(len(p.split()) >= 2 and _valid_name_part(p) for p in parts):
            return parts
        # 2b) "Family, Given, Family, Given, ..." pairs (single-token parts)
        if (len(parts) >= 4 and len(parts) % 2 == 0
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
