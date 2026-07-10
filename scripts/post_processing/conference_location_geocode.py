#!/usr/bin/env python3
"""Normalize conference locations by GEOCODING them, so spelling/format variants that
resolve to the same real place collapse and genuinely different places stay apart.

conference.conferenceLocation is free text ("Graz, Austria", "graz austria", "Graz,AT",
"Vienna, Austria"). A string embedding would merge "Graz" and "Vienna" (both "city in
Austria"); geocoding tells them apart and unifies the variants of each. This mirrors the
ROR split we use for institutions, but for places.

Stages (run in order; each is resumable / cached):
  --collect  scan the corpus, cache the distinct conferenceLocation strings + counts
  --geocode  geocode each distinct string via Nominatim (OSM), 1 req/sec, cached to JSON
  --build    from the geocode cache, emit variant->canonical ("City, Country") map + review
  --apply    rewrite conference.conferenceLocation in the corpus from the map

Usage (pubverse env; needs `geopy`):
    ~/myenv/bin/python conference_location_geocode.py --collect \
        --merged-glob '/storage/poster-work/*/merged/*/*.json' --work /storage/poster-work/loc
    ~/myenv/bin/python conference_location_geocode.py --geocode --work /storage/poster-work/loc
    ~/myenv/bin/python conference_location_geocode.py --build  --work /storage/poster-work/loc
    ~/myenv/bin/python conference_location_geocode.py --apply  --work /storage/poster-work/loc \
        --merged-glob '/storage/poster-work/*/merged/*/*.json'
"""
import argparse
import glob
import json
import pickle
import re
import unicodedata
from collections import Counter
from pathlib import Path


def _norm_q(s):
    s = unicodedata.normalize("NFKC", str(s)).strip()
    return re.sub(r"\s+", " ", s)


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


def geocode(work, sleep):
    from geopy.geocoders import Nominatim
    from geopy.extra.rate_limiter import RateLimiter
    terms = json.loads(Path(work + ".terms.json").read_text(encoding="utf-8"))
    cache_path = Path(work + ".geo.json")
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    geo = Nominatim(user_agent="posters-science-conf-loc/1.0", timeout=10)
    q = RateLimiter(geo.geocode, min_delay_seconds=sleep, swallow_exceptions=True)
    todo = [t for t in terms if t not in cache]
    print(f"[loc] geocoding {len(todo)} new / {len(terms)} total (cached {len(cache)})", flush=True)
    for i, t in enumerate(todo, 1):
        r = q(t, addressdetails=True, language="en")
        if r and getattr(r, "raw", None):
            a = r.raw.get("address", {})
            city = a.get("city") or a.get("town") or a.get("village") or a.get("municipality") \
                or a.get("state") or a.get("county")
            cache[t] = {"country": a.get("country"), "cc": a.get("country_code"),
                        "city": city, "lat": r.raw.get("lat"), "lon": r.raw.get("lon")}
        else:
            cache[t] = None
        if i % 50 == 0:
            cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            print(f"  ...{i}/{len(todo)}", flush=True)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    ok = sum(1 for v in cache.values() if v)
    print(f"[loc] geocoded {ok}/{len(cache)} resolved")


def build(work):
    terms = json.loads(Path(work + ".terms.json").read_text(encoding="utf-8"))
    cache = json.loads(Path(work + ".geo.json").read_text(encoding="utf-8"))
    # group distinct strings by resolved (country_code, city); canonical = "City, Country"
    groups = {}
    for t, g in cache.items():
        if not g or not g.get("city") or not g.get("country"):
            continue
        key = (g.get("cc"), g["city"].lower())
        groups.setdefault(key, {"canon": f"{g['city']}, {g['country']}", "members": []})
        groups[key]["members"].append(t)
    remap, clusters = {}, {}
    for key, grp in groups.items():
        canon = grp["canon"]
        members = grp["members"]
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
    print(f"[loc] {len(remap)} variants -> canonical across {len(clusters)} places -> {work}.map.pickle")


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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--geocode", action="store_true")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--work", required=True, help="path prefix for term/geo/map artifacts")
    ap.add_argument("--merged-glob")
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


if __name__ == "__main__":
    main()
