#!/usr/bin/env python3
"""Add the ROR affiliations a repository registered with DataCite to each creator.

Zenodo and institutional Figshare portals register creator affiliations with DataCite,
often with a ROR id ("UL (United States)", https://ror.org/045n75910, for the UL Research
Institutes portal). These are only returned when DataCite is asked with ?affiliation=true,
which fetch_datacite.py never did, and enrich_from_datacite.py only ran on Zenodo records.
So Figshare posters carried no institution unless the poster's own header named it.

For every record with a DOI, the DataCite record is fetched with affiliations (cached).
DataCite creators are matched to record creators, and DataCite contributors to record
contributors, one to one: a pair is used only when same_author holds both ways, the
DataCite person matches exactly one record person and that record person matches exactly
one DataCite person ("Smith, J." against "Smith, John" and "Smith, Jane" is skipped).
For each affiliation of a matched person that has a valid ROR id:
  - an inactive (deprecated or withdrawn) id is replaced by its single active successor,
    or skipped when it has none;
  - the person already has that ROR id (any spelling, any scheme field): nothing to do;
  - an affiliation with the same name (DataCite or ROR names) has a different id: the id
    is replaced only when the name is exactly one of the ROR record's names, else it is a
    conflict and nothing is added;
  - an affiliation without an id has that name: the id is added to it;
  - one affiliation without an id is an abbreviation of the ROR display name ("Org Univ."):
    the id is added to it (no near-duplicate); several such: nothing is added;
  - otherwise a new affiliation is added, named by the ROR record's display name.
The ROR id comes from the repository, not from matching text, so it is used as given.
Ids missing from the local ROR dump keep the name DataCite has. Never removes anything.
Idempotent. Every skip is counted, also in --dry-run.

Usage (pubverse env):
    ~/myenv/bin/python backfill_datacite_ror_affiliations.py \
        --merged-dir /storage/poster-work/pre2025/merged \
        --cache-dir /storage/poster-work/datacite_aff_cache \
        --ror-data /storage/poster-work/ror-data/v2.9-2026-06-23-ror-data.json \
        [--fetch] [--workers 4] [--delay 0.125] [--refetch-missing-after-days 30] \
        --dry-run --show 30
"""
import argparse
import json
import logging
import re
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

_SRC = Path(__file__).resolve().parents[2] / "src"
if _SRC.exists():
    sys.path.insert(0, str(_SRC))
from poster_to_json.field_normalize import affiliation_tokens, same_author  # noqa: E402

ROR_PREFIX = "https://ror.org/"
_ROR_FIELDS = {"affiliationIdentifierScheme": "ROR", "schemeURI": "https://ror.org"}
_ROR_RE = re.compile(r"^(?:(?:https?://)?(?:www\.)?ror\.org/)?([0-9a-z]{9})$")
_UA = "posters.science-pipeline/0.40 (https://posters.science; mailto:joneill@calmi2.org)"
_ABBREV_STOP = frozenset({"the", "of", "and", "at", "for"})


class _RateLimit:
    """At most one request start per `interval` seconds, across all threads."""

    def __init__(self, interval):
        self.interval, self.next, self.lock = interval, 0.0, threading.Lock()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next)
            self.next = t + self.interval
        if t > now:
            time.sleep(t - now)


_LIMIT = _RateLimit(0.125)


def _safe(doi):
    return doi.replace("/", "_").replace(":", "_")


def _doi_of(d):
    for i in d.get("identifiers") or []:
        if isinstance(i, dict) and i.get("identifierType") == "DOI" and i.get("identifier"):
            return str(i["identifier"]).strip()
    return None


def _cache_state(p, refetch_missing_after_days):
    """("hit", attrs); ("missing", None) for a recent 404; ("stale", None) for a 404 older
    than `refetch_missing_after_days`; ("absent", None) when never fetched. A legacy "{}"
    404 marker is dated by its file time."""
    p = Path(p)
    if not p.exists():
        return "absent", None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return "absent", None
    if d and not d.get("_missing"):
        return "hit", d
    checked = (d or {}).get("_checked_at") or p.stat().st_mtime
    if time.time() - checked > refetch_missing_after_days * 86400:
        return "stale", None
    return "missing", None


def fetch(doi, cache_dir, refetch_missing_after_days=30, network=True):
    """DataCite attributes for `doi` with affiliations, from cache or the API. None when
    DataCite has no record (a 404 is cached with its date and asked again once older than
    `refetch_missing_after_days`), or when the DOI is not cached and `network` is off."""
    p = Path(cache_dir) / f"{_safe(doi)}.json"
    state, attrs = _cache_state(p, refetch_missing_after_days)
    if state in ("hit", "missing") or not network:
        return attrs
    url = f"https://api.datacite.org/dois/{urllib.parse.quote(doi, safe='')}?affiliation=true"
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/vnd.api+json"})
    for attempt in range(5):
        try:
            _LIMIT.wait()
            with urllib.request.urlopen(req, timeout=30) as r:
                attrs = json.load(r)["data"]["attributes"]
            p.write_text(json.dumps(attrs, ensure_ascii=False), encoding="utf-8")
            return attrs
        except urllib.error.HTTPError as e:
            if e.code == 404:
                p.write_text(json.dumps({"_missing": True, "_checked_at": time.time()}), encoding="utf-8")
                return None
            if e.code in (429, 503):
                time.sleep(min(10 * 2 ** attempt, 120))
                continue
            time.sleep(5 * (attempt + 1))
        except Exception:
            time.sleep(5 * (attempt + 1))
    return None


def norm_ror_id(value):
    """https://ror.org/<id> for any spelling of a ROR id (http, www, trailing slash, upper
    case, bare id), else None."""
    m = _ROR_RE.match(str(value or "").strip().lower().rstrip("/"))
    return ROR_PREFIX + m.group(1) if m else None


def _exact(name):
    """Case- and space-insensitive form of a name."""
    return " ".join(unicodedata.normalize("NFKC", str(name or "")).casefold().split())


def _tok(name):
    return " ".join(affiliation_tokens(name))


def load_ror_names(path):
    """ROR id (https://ror.org/xxx) -> (display name, set of token-normalized names, status,
    successor ids, set of case/space-normalized names)."""
    out = {}
    for org in json.loads(Path(path).read_text(encoding="utf-8")):
        rid = norm_ror_id(org.get("id"))
        names = org.get("names") or []
        disp = next((n["value"] for n in names if "ror_display" in (n.get("types") or [])), None)
        if rid and disp:
            succ = tuple(r.get("id") for r in org.get("relationships") or []
                         if str(r.get("type") or "").lower() == "successor" and r.get("id"))
            out[rid] = (disp, {_tok(n.get("value")) for n in names}, org.get("status") or "active",
                        succ, {_exact(n.get("value")) for n in names})
    return out


def _ror_info(ror_names, rid):
    """(display, token names, status, successors, exact names), or None when `rid` is not
    in the dump. Also accepts the short (display, token names) form."""
    e = ror_names.get(rid)
    if e is None:
        return None
    return (e[0], set(e[1]), e[2] if len(e) > 2 else "active", tuple(e[3]) if len(e) > 3 else (),
            set(e[4]) if len(e) > 4 else {_exact(e[0])})


def _resolve(rid, ror_names, stats):
    """(id, ROR info) to use for `rid`: itself when active, its single active successor
    when inactive, else (None, None). Ids missing from the dump are kept, with no info."""
    info = _ror_info(ror_names, rid)
    if info is None:
        stats["ror_id_not_in_dump"] += 1
        return rid, None
    if info[2] == "active":
        return rid, info
    succ = list(dict.fromkeys(s for s in map(norm_ror_id, info[3]) if s))
    sinfo = _ror_info(ror_names, succ[0]) if len(succ) == 1 else None
    if sinfo is not None and sinfo[2] == "active":
        stats["inactive_ror_successor_used"] += 1
        return succ[0], sinfo
    stats["inactive_ror_skipped"] += 1
    return None, None


def _existing_ror(a):
    """Normalized ROR id of a record affiliation; the scheme field may be missing."""
    if not isinstance(a, dict):
        return None
    scheme = str(a.get("affiliationIdentifierScheme") or "").strip().upper()
    v = a.get("affiliationIdentifier")
    if scheme not in ("", "ROR") and "ror.org" not in str(v or "").lower():
        return None
    return norm_ror_id(v)


_ror_id = _existing_ror


def _datacite_ror(a):
    """(ROR id, None) for a DataCite affiliation, or (None, "no_id" | "bad")."""
    v = str(a.get("affiliationIdentifier") or "").strip()
    scheme = str(a.get("affiliationIdentifierScheme") or "").strip().upper()
    if not v or (scheme != "ROR" and "ror.org" not in v.lower()):
        return None, "no_id"
    rid = norm_ror_id(v)
    return (rid, None) if rid else (None, "bad")


def _abbrev_variant(name, disp):
    """True when `name` is `disp` with some words shortened ("Org Univ." for "Org
    University", "Dept" for "Department"): each short word starts with the same letter
    and its letters appear in order in the long one."""
    a = [t for t in affiliation_tokens(name) if t not in _ABBREV_STOP]
    b = [t for t in affiliation_tokens(disp) if t not in _ABBREV_STOP]
    if not a or len(a) != len(b) or a == b:
        return False

    def short_for(x, y):
        if x == y:
            return True
        if len(x) < 2 or len(x) >= len(y) or x[0] != y[0]:
            return False
        it = iter(y)
        return all(ch in it for ch in x)
    return all(short_for(x, y) for x, y in zip(a, b))


def _person(c):
    return c.get("name") or ", ".join(x for x in (c.get("familyName"), c.get("givenName")) if x)


def match_people(local, remote, stats):
    """One-to-one [(record person, DataCite person)]. A pair counts only when the names
    match (same_author both ways, or the same words in another order), the DataCite person
    matches exactly one record person, and that record person matches exactly one DataCite
    person; a person listed twice in the record counts once. Anything else is skipped and
    counted."""
    loc = [c for c in local if isinstance(c, dict) and c.get("name")]
    rem = []
    for c in remote:
        if not isinstance(c, dict):
            continue
        if c.get("nameType") == "Organizational":
            stats["organizational_skipped"] += 1
        elif _person(c):
            rem.append(c)
    pairs = [(i, j) for i, lc in enumerate(loc) for j, rc in enumerate(rem)
             if _same_person(str(lc["name"]), str(_person(rc)))]
    per_local = Counter(i for i, _ in pairs)
    out = []
    for j in range(len(rem)):
        mine = [i for i, jj in pairs if jj == j]
        # a person listed twice in the record (identical names) is one person
        if (len(mine) > 1 and len({_name_tokens(loc[i]["name"]) for i in mine}) == 1
                and all(per_local[i] == 1 for i in mine)):
            out.extend((loc[i], rem[j]) for i in mine)
            stats["duplicate_local_person"] += 1
            continue
        if not mine:
            stats["datacite_person_no_local_match"] += 1
        elif len(mine) > 1 or per_local[mine[0]] > 1:
            stats["ambiguous_match"] += 1
        else:
            out.append((loc[mine[0]], rem[j]))
    return out


def _name_tokens(name):
    """The multiset of a name's words, case and accents folded: "Correll Trnka, Anika" and
    "Trnka, Anika Correll" (a multi-word surname flipped on its last word) are equal."""
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    return tuple(sorted(re.findall(r"[a-z]+", s)))


def _same_person(a, b):
    """same_author both ways, or the same words in another order (two or more words)."""
    if same_author(a, b) and same_author(b, a):
        return True
    ta, tb = _name_tokens(a), _name_tokens(b)
    return len(ta) >= 2 and ta == tb


def _enrich_person(cr, dcc, ror_names, stats, samples, show):
    changed = 0
    raw = cr.get("affiliation")
    affs = raw if isinstance(raw, list) else ([raw] if isinstance(raw, str) and raw.strip() else [])
    affs = [{"name": a} if isinstance(a, str) else a for a in affs]
    # every ROR id DataCite gives this person: one of them on a same-name entry is no
    # conflict for another (two ids of one person never take each other's place)
    dc_ids = set()
    for a in dcc.get("affiliation") or []:
        if isinstance(a, dict) and _datacite_ror(a)[0]:
            r, _ = _resolve(_datacite_ror(a)[0], ror_names, Counter())
            if r:
                dc_ids.add(r)
    for a in dcc.get("affiliation") or []:
        if isinstance(a, str):
            a = {"name": a}
        if not isinstance(a, dict):
            continue
        rid, why = _datacite_ror(a)
        if why == "no_id":
            if a.get("name"):
                stats["affiliation_name_without_ror"] += 1
            continue
        if why == "bad":
            stats["bad_ror_id"] += 1
            continue
        rid, info = _resolve(rid, ror_names, stats)
        if rid is None or any(_existing_ror(x) == rid for x in affs):
            continue
        dc_name = a.get("name")
        disp = info[0] if info else dc_name
        if not disp:
            continue
        known = (info[1] if info else set()) | ({_tok(dc_name)} if dc_name else set())
        known.discard("")
        same = [x for x in affs if isinstance(x, dict) and _tok(x.get("name")) in known]
        other = [x for x in same if x.get("affiliationIdentifier") and _existing_ror(x) not in dc_ids]
        if not other and any(x.get("affiliationIdentifier") for x in same):
            same = [x for x in same if not x.get("affiliationIdentifier")]
        bare = [x for x in same if not x.get("affiliationIdentifier")]
        if other:
            # the same name already carries a different id: take the registrant's id only
            # when the name is exactly one of that ROR record's names
            if len(other) == 1 and info and _exact(other[0].get("name")) in info[4]:
                other[0].update({"affiliationIdentifier": rid, **_ROR_FIELDS})
                what = f"replaced id on {other[0].get('name')!r}"
            else:
                stats["conflict"] += 1
                continue
        elif bare:
            bare[0].update({"affiliationIdentifier": rid, **_ROR_FIELDS})
            what = f"id on {bare[0].get('name')!r}"
        elif abbr := [x for x in affs if isinstance(x, dict) and not x.get("affiliationIdentifier")
                      and _abbrev_variant(x.get("name"), disp)]:
            if len(abbr) > 1:
                stats["near_duplicate_skipped"] += 1
                continue
            # the registrant's id for this person, on the person's own shortened wording
            abbr[0].update({"affiliationIdentifier": rid, **_ROR_FIELDS})
            stats["id_on_abbreviation"] += 1
            what = f"id on abbreviation {abbr[0].get('name')!r}"
        else:
            affs.append({"name": disp, "affiliationIdentifier": rid, **_ROR_FIELDS})
            what = f"added {disp!r}"
        changed += 1
        if show is not None and len(samples) < show:
            samples.append(f"{cr['name']}: {what} {rid}")
    if changed:
        cr["affiliation"] = affs
    return changed


def enrich_record(d, dc, ror_names, samples, show, stats=None):
    """Add DataCite ROR affiliations to the record's creators and contributors; returns
    the number of affiliations added or identified. Skips are counted in `stats`."""
    stats = Counter() if stats is None else stats
    changed = 0
    for key in ("creators", "contributors"):
        local = d.get(key) if isinstance(d.get(key), list) else []
        for cr, dcc in match_people(local, dc.get(key) or [], stats):
            changed += _enrich_person(cr, dcc, ror_names, stats, samples, show)
    return changed


_COUNTERS = ("scanned", "no_doi", "not_cached", "no_datacite", "fetch_failed", "records_changed",
             "affiliations_added_or_identified", "datacite_person_no_local_match", "ambiguous_match",
             "organizational_skipped", "duplicate_local_person", "affiliation_name_without_ror", "bad_ror_id", "conflict",
             "near_duplicate_skipped", "id_on_abbreviation", "inactive_ror_successor_used", "inactive_ror_skipped",
             "ror_id_not_in_dump", "errors")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merged-dir", required=True)
    ap.add_argument("--cache-dir", required=True)
    ap.add_argument("--ror-data", required=True)
    ap.add_argument("--fetch", action="store_true", help="query DataCite for DOIs not in the cache")
    ap.add_argument("--delay", type=float, default=0.125,
                    help="minimum seconds between two DataCite requests across all workers "
                         "(default 0.125 = 8 requests/s; DataCite asks for under 10/s)")
    ap.add_argument("--workers", type=int, default=4, help="parallel DataCite requests when prefetching")
    ap.add_argument("--refetch-missing-after-days", type=float, default=30,
                    help="ask DataCite again for DOIs it answered 404 for more than this many days ago")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--show", type=int, default=0)
    args = ap.parse_args()
    if args.delay < 0.1:
        ap.error("--delay below 0.1 s would exceed 10 DataCite requests/s")
    _LIMIT.interval = args.delay
    Path(args.cache_dir).mkdir(parents=True, exist_ok=True)
    ror_names = load_ror_names(args.ror_data)
    logger.info(f"[{'DRY-RUN' if args.dry_run else 'LIVE'}] {len(ror_names)} ROR records; merged={args.merged_dir}")

    def cache_path(doi):
        return Path(args.cache_dir) / f"{_safe(doi)}.json"

    files = sorted(Path(args.merged_dir).rglob("*_complete.json"))
    if args.fetch:                               # fill the cache in parallel first
        from concurrent.futures import ThreadPoolExecutor
        dois = set()
        for f in files:
            try:
                doi = _doi_of(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue
            if doi and _cache_state(cache_path(doi), args.refetch_missing_after_days)[0] in ("absent", "stale"):
                dois.add(doi)
        logger.info(f"  prefetching {len(dois)} DOIs with {args.workers} workers, "
                    f"at most {1 / args.delay:.1f} requests/s")
        with ThreadPoolExecutor(args.workers) as ex:
            for i, _ in enumerate(ex.map(lambda d: fetch(d, args.cache_dir, args.refetch_missing_after_days),
                                         sorted(dois)), 1):
                if i % 2000 == 0:
                    logger.info(f"  ... fetched {i}/{len(dois)}")

    stats, samples = Counter(), []
    show = args.show or None
    for f in files:
        stats["scanned"] += 1
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            stats["errors"] += 1
            continue
        doi = _doi_of(d)
        if not doi:
            stats["no_doi"] += 1
            continue
        state, dc = _cache_state(cache_path(doi), args.refetch_missing_after_days)
        if state in ("absent", "stale"):
            if not args.fetch:
                stats["not_cached"] += 1
                continue
            dc = fetch(doi, args.cache_dir, args.refetch_missing_after_days)
            if not dc:
                again = _cache_state(cache_path(doi), args.refetch_missing_after_days)[0]
                stats["no_datacite" if again == "missing" else "fetch_failed"] += 1
                continue
        if not dc:
            stats["no_datacite"] += 1
            continue
        n = enrich_record(d, dc, ror_names, samples, show, stats)
        if n:
            stats["records_changed"] += 1
            stats["affiliations_added_or_identified"] += n
            if not args.dry_run:
                f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
        if stats["scanned"] % 2000 == 0:
            logger.info(f"  ... {stats['scanned']} scanned, {stats['records_changed']} changed")
    for s in samples:
        logger.info(f"  e.g. {s}")
    for k in _COUNTERS:
        logger.info(f"  {k:34s} {stats[k]}")


if __name__ == "__main__":
    main()
