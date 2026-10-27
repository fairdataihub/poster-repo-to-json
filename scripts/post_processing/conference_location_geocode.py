#!/usr/bin/env python3
"""Normalize conference locations by GEOCODING them, so spelling/format variants that
resolve to the same real place collapse and genuinely different places stay apart.

conference.conferenceLocation is free text ("Graz, Austria", "graz austria", "Graz,AT",
"Vienna, Austria"). A string embedding would merge "Graz" and "Vienna" (both "city in
Austria"); geocoding tells them apart and unifies the variants of each. This mirrors the
ROR split we use for institutions, but for places.

A geocoder returns a best guess for any text, so a result is used only when it verifiably
names the text's place (fairly.authorities.geocode.verify_place): it is a place, not a
street, building or venue; one of its names appears in the text; a country or US state
the text names agrees with it. The canonical is the place's own English name and country
("Milano" -> "Milan, Italy"), never a containing region. Anything else stays as written.
The first version took the geocoder's guess as is: "Conference Location" became
"Nizhniy Arkhyz, Russia", "CERN" "Chern, Russia", "Pisa, Italy" "Tuscany, Italy".

Stages (run in order; each is resumable / cached):
  --collect  scan the corpus, cache the distinct conferenceLocation strings + counts
  --geocode  geocode each distinct string via Nominatim (OSM), 1 req/sec, cached to JSON
             with the full answer (address and name details)
  --build    from the geocode cache, emit the verified variant->canonical map + review
  --apply    rewrite conference.conferenceLocation in the corpus from the map
  --repair   (corpus already rewritten by earlier maps, --old-map): find each record's
             written location in its own sources (--snapshot-dir, the Zenodo deposit's
             meeting place, the extractions), and set it to the new map's value for that
             text, or the text itself; a location template left in the text ("Conference
             Location", "Name of Location") is removed

Usage (pubverse env; needs `geopy` and fair-ly-accurate):
    ~/myenv/bin/python conference_location_geocode.py --collect \
        --merged-glob '/storage/poster-work/*/merged/*/*.json' --work /storage/poster-work/loc
    ~/myenv/bin/python conference_location_geocode.py --geocode --work /storage/poster-work/loc
    ~/myenv/bin/python conference_location_geocode.py --build  --work /storage/poster-work/loc
    ~/myenv/bin/python conference_location_geocode.py --repair --work /storage/poster-work/loc \
        --old-map /storage/poster-work/loc.map.pickle.v1 --corpus-dir /storage/poster-work/pre2025 \
        [--snapshot-dir /storage/poster-work/azure_snap/merged] [--dry-run]
"""
import argparse
import glob
import json
import pickle
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

from fairly.authorities.geocode import is_virtual, named_countries, verify_place

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpus_index import extraction_files  # noqa: E402

# A location field holding a poster template's placeholder, not a place.
_TEMPLATE_RE = re.compile(
    r"^\W*((name\s+of\s+(the\s+)?)?(conference|event|meeting|symposium)?\s*(location|venue|place|city)"
    r"(\s*,\s*(country|state))?|city\s*,\s*(state|country)(\s*,\s*country)?|location\s+here|tbd|tba)\W*$",
    re.I)
_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
          r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
          r"|spring|summer|fall|autumn|winter)\.?")       # a season and year is a date too
_DAY = r"\d{1,2}(?:st|nd|rd|th)?"
_DATE_ONLY_RE = re.compile(rf"^\W*(?:{_DAY}\W*)?(?:{_MONTH}\W*)?(?:{_DAY}\W*)?(?:\d{{4}})?\W*$", re.I)
_HAS_DATE_RE = re.compile(rf"(?<![a-z]){_MONTH}(?![a-z])|(?<!\d)\d{{4}}(?!\d)", re.I)


def _norm_q(s):
    s = unicodedata.normalize("NFKC", str(s)).strip()
    return re.sub(r"\s+", " ", s)


def _key(s):
    """Alphanumerics only, case-folded (collapse_exact's equivalence)."""
    return re.sub(r"[^0-9a-z]", "", unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore")
                  .decode().lower()) or str(s or "").casefold()


def _is_virtual(s):
    return is_virtual(s)


def is_template(s):
    """A placeholder or a bare date where a place should be."""
    s = str(s or "").strip()
    return bool(_TEMPLATE_RE.match(s)) or (bool(_HAS_DATE_RE.search(s)) and bool(_DATE_ONLY_RE.match(s)))


def collect(merged_glob, work):
    c = Counter()
    for f in glob.glob(merged_glob):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        conf = d.get("conference") if isinstance(d, dict) else None
        loc = conf.get("conferenceLocation") if isinstance(conf, dict) else None
        if isinstance(loc, str) and _norm_q(loc):
            c[_norm_q(loc)] += 1
    Path(work + ".terms.json").write_text(json.dumps(dict(c), ensure_ascii=False), encoding="utf-8")
    print(f"[loc] {len(c)} distinct conference locations ({sum(c.values())} posters) -> {work}.terms.json")


def _raw(entry):
    """The Nominatim answer of a cache entry: {"raw": ..., "others": [...]} (this version),
    a bare raw answer, or None. Entries of the first version kept only city/country: no raw."""
    if not isinstance(entry, dict):
        return None
    if "raw" in entry:
        return entry["raw"]
    return entry if ("address" in entry or "namedetails" in entry) else None


def _others(entry):
    """The geocoder's next answers for a text that names no country (ambiguity check)."""
    return entry.get("others") or [] if isinstance(entry, dict) else []


def geocode(work, sleep):
    from geopy.geocoders import Nominatim
    terms = json.loads(Path(work + ".terms.json").read_text(encoding="utf-8"))
    cache_path = Path(work + ".geo.json")
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    geo = Nominatim(user_agent="posters-science-conf-loc/1.1", timeout=20)
    todo = [t for t in terms if not _is_virtual(t) and (
        t not in cache or (cache[t] and _raw(cache[t]) is None)
        or (cache[t] and not named_countries(t) and "others" not in cache[t]))]
    print(f"[loc] geocoding {len(todo)} new / {len(terms)} total (cached {len(cache)})", flush=True)
    import time
    last = 0.0
    for i, t in enumerate(todo, 1):
        wait = sleep - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        last = time.time()
        many = not named_countries(t)          # no country named: keep the next answers too
        try:
            r = geo.geocode(t, addressdetails=True, namedetails=True, language="en", exactly_one=not many,
                            **({"limit": 5} if many else {}))
            rs = [x for x in (r if many else [r]) or [] if x is not None]
            cache[t] = {"raw": rs[0].raw, "others": [x.raw for x in rs[1:]]} if rs else None
        except Exception:
            continue                      # asked again on the next run
        if i % 50 == 0:
            cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            print(f"  ...{i}/{len(todo)}", flush=True)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    print(f"[loc] geocoded {sum(1 for v in cache.values() if v)}/{len(cache)} resolved")


def build(work):
    terms = json.loads(Path(work + ".terms.json").read_text(encoding="utf-8"))
    cache = json.loads(Path(work + ".geo.json").read_text(encoding="utf-8"))
    groups, why = {}, Counter()
    for t in terms:
        if _is_virtual(t):
            why["virtual"] += 1
            continue
        raw = _raw(cache.get(t))
        if raw is None:
            why["no_answer" if t in cache else "not_geocoded"] += 1
            continue
        v = verify_place(t, raw, _others(cache.get(t)))
        if not v:
            why["not_verified"] += 1
            continue
        why["verified"] += 1
        key = (v["cc"], v["name"].lower())
        groups.setdefault(key, {"canon": v["canonical"], "members": []})["members"].append(t)
    remap, clusters = {}, {}
    for key, grp in groups.items():
        canon, members = grp["canon"], grp["members"]
        for m in members:
            if m != canon:
                remap[m] = canon
        if len(members) > 1 or (members and members[0] != canon):
            clusters[canon] = sorted(members, key=lambda m: -terms.get(m, 0))
    pickle.dump(remap, open(work + ".map.pickle", "wb"))
    with open(work + "_review.tsv", "w", encoding="utf-8") as o:
        o.write("canonical\tfreq\tvariants\n")
        for canon, members in sorted(clusters.items(), key=lambda kv: -sum(terms.get(m, 0) for m in kv[1])):
            o.write(f"{canon}\t{sum(terms.get(m,0) for m in members)}\t"
                    + " | ".join(f"{m}({terms.get(m,0)})" for m in members if m != canon) + "\n")
    print(f"[loc] {len(remap)} variants -> canonical across {len(clusters)} places -> {work}.map.pickle  "
          + "  ".join(f"{k}={n}" for k, n in sorted(why.items())))


def apply(work, merged_glob):
    remap = pickle.load(open(work + ".map.pickle", "rb"))
    changed = 0
    for f in glob.glob(merged_glob):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        conf = d.get("conference") if isinstance(d, dict) else None
        if not isinstance(conf, dict):
            continue
        loc = conf.get("conferenceLocation")
        if isinstance(loc, str) and _norm_q(loc) in remap:
            conf["conferenceLocation"] = remap[_norm_q(loc)]
            Path(f).write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
            changed += 1
    print(f"[loc] rewrote {changed} conference locations")


def _conf_loc(c):
    if isinstance(c, dict):
        return c.get("conferenceLocation") or c.get("place")
    return None


def source_locations(corpus, src, rid, snapshot_dir=None):
    """The location texts a record's conference came from: the pre-geocoding snapshot,
    the Zenodo deposit's meeting place, and each extraction."""
    out = []
    if snapshot_dir:
        try:
            snap = json.loads((Path(snapshot_dir) / src / f"{rid}_complete.json").read_text(encoding="utf-8"))
            out.append(_conf_loc(snap.get("conference")))
        except Exception:
            pass
    try:
        m = json.loads((Path(corpus) / "metadata" / src / f"{rid}.json").read_text(encoding="utf-8"))
        md = m.get("metadata") or {}
        out.append(_conf_loc(md.get("meeting")))
        out.append(_conf_loc((m.get("custom_fields") or md.get("custom_fields") or {}).get("meeting:meeting")))
    except Exception:
        pass
    for f in extraction_files(corpus, src, rid):
        try:
            out.append(_conf_loc(json.loads(Path(f).read_text(encoding="utf-8")).get("conference")))
        except Exception:
            continue
    return list(dict.fromkeys(_norm_q(x) for x in out if isinstance(x, str) and _norm_q(x)))


def repair_record(d, sources, old_map, new_map, stats):
    """Set the record's location to the new map's value of the text it was written from.
    `old_map` is the map (or list of maps) applied earlier. Returns (old, new) when it
    changed."""
    old_maps = old_map if isinstance(old_map, (list, tuple)) else [old_map]
    conf = d.get("conference")
    loc = conf.get("conferenceLocation") if isinstance(conf, dict) else None
    if not isinstance(loc, str) or not loc.strip():
        return None
    now = _norm_q(loc)
    if now in sources or any(_key(s) == _key(now) for s in sources):
        texts = [now]                                     # written as the source has it
    else:
        texts = [s for s in sources if any(_key(m.get(s)) == _key(now) for m in old_maps)]
        if not texts:
            stats["unexplained"] += 1                     # changed by something else; leave it
            return None
    if any(is_template(t) for t in texts):
        conf.pop("conferenceLocation", None)
        stats["template_removed"] += 1
        return loc, None
    targets = {new_map.get(t, t) for t in texts}
    if len(targets) != 1:
        stats["ambiguous"] += 1
        return None
    new = targets.pop()
    if new == loc:
        stats["unchanged"] += 1
        return None
    conf["conferenceLocation"] = new
    stats["repaired"] += 1
    return loc, new


def repair(work, old_map_paths, corpus, merged_subdir, snapshot_dir, dry_run, show):
    old_map = [pickle.load(open(p, "rb")) for p in old_map_paths]
    new_map = pickle.load(open(work + ".map.pickle", "rb"))
    stats, samples = Counter(), []
    for src in ("zenodo", "figshare"):
        for f in sorted((Path(corpus) / merged_subdir / src).glob("*_complete.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                stats["errors"] += 1
                continue
            stats["scanned"] += 1
            rid = f.name[: -len("_complete.json")]
            ch = repair_record(d, source_locations(corpus, src, rid, snapshot_dir), old_map, new_map, stats)
            if not ch:
                continue
            if len(samples) < show:
                samples.append(f"{src}/{rid}: {ch[0]!r} -> {ch[1]!r}")
            if not dry_run:
                f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    for s in samples:
        print(f"  e.g. {s}")
    print(f"[loc] {'DRY-RUN ' if dry_run else ''}repair {corpus}: "
          + "  ".join(f"{k}={n}" for k, n in sorted(stats.items())))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--geocode", action="store_true")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--repair", action="store_true")
    ap.add_argument("--work", required=True, help="path prefix for term/geo/map artifacts")
    ap.add_argument("--merged-glob")
    ap.add_argument("--old-map", nargs="+", help="--repair: the map(s) earlier runs applied")
    ap.add_argument("--corpus-dir", help="--repair: has metadata/, extractions/ and the merged dir")
    ap.add_argument("--merged-subdir", default="merged")
    ap.add_argument("--snapshot-dir", default=None, help="--repair: pre-geocoding copy of the merged dir")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--show", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=1.1, help="seconds between geocode calls (Nominatim policy >=1)")
    args = ap.parse_args()
    if args.collect:
        collect(args.merged_glob, args.work)
    if args.geocode:
        geocode(args.work, args.sleep)
    if args.build:
        build(args.work)
    if args.apply:
        apply(args.work, args.merged_glob)
    if args.repair:
        if not (args.old_map and args.corpus_dir):
            ap.error("--repair needs --old-map and --corpus-dir")
        repair(args.work, args.old_map, args.corpus_dir, args.merged_subdir, args.snapshot_dir, args.dry_run,
               args.show)


if __name__ == "__main__":
    main()
