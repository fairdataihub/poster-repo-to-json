#!/usr/bin/env python3
"""Final exact-collapse pass across fields.

Merges field values that are IDENTICAL after aggressive normalization -- strip
diacritics, lowercase, and drop every non-alphanumeric character (all whitespace and
punctuation). This catches stray duplicates that differ only by weird/random spacing
(double spaces, non-breaking / zero-width spaces, tabs), casing, or punctuation, which
the surface/semantic passes can miss.

It is deterministic and conservative: two values merge only when their alphanumeric
content is byte-identical, so genuinely different entities are never merged
("University of California, San Diego" and "... Berkeley" have different keys). Canonical
= the most frequent surface form in each collapse group. Non-Latin scripts are preserved
(Unicode alphanumerics are kept, not stripped).

Runs for: publisher | funder | affiliation | subject | location (or 'all').

Usage (pubverse env):
    ~/myenv/bin/python collapse_exact.py --field all --merged-dir <m> [--dry-run] [--show N]
"""
import argparse
import json
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

FIELDS = ["publisher", "funder", "affiliation", "subject", "location"]


def collapse_key(s):
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))   # strip diacritics
    return "".join(c.lower() for c in s if c.isalnum())         # keep alnum only (drops all ws/punct)


def field_values(d, field):
    if field == "publisher":
        p = d.get("publisher")
        n = p.get("name") if isinstance(p, dict) else p
        if isinstance(n, str):
            yield n
    elif field == "funder":
        for fr in d.get("fundingReferences") or []:
            if isinstance(fr, dict) and isinstance(fr.get("funderName"), str):
                yield fr["funderName"]
    elif field == "affiliation":
        for cr in d.get("creators") or []:
            if isinstance(cr, dict):
                for a in cr.get("affiliation") or []:
                    n = a.get("name") if isinstance(a, dict) else a
                    if isinstance(n, str):
                        yield n
    elif field == "subject":
        for s in d.get("subjects") or []:
            n = s.get("subject") if isinstance(s, dict) else s
            if isinstance(n, str):
                yield n
    elif field == "location":
        conf = d.get("conference")
        if isinstance(conf, dict) and isinstance(conf.get("conferenceLocation"), str):
            yield conf["conferenceLocation"]


def build_remap(files, field):
    counts = Counter()
    for f in files:
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        for v in field_values(d, field):
            if v.strip():
                counts[v] += 1
    groups = defaultdict(list)
    for term in counts:
        k = collapse_key(term)
        if k:
            groups[k].append(term)
    remap = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        rep = max(members, key=lambda m: counts[m])
        for m in members:
            if m != rep:
                remap[m] = rep
    return remap, counts


def _canon(name, remap):
    return remap.get(name, name) if isinstance(name, str) else name


def apply_field(d, field, remap):
    changed = False
    if field == "publisher":
        p = d.get("publisher")
        n = p.get("name") if isinstance(p, dict) else p
        if isinstance(n, str) and n in remap:
            d["publisher"] = {"name": remap[n]}
            changed = True
    elif field == "funder":
        arr = d.get("fundingReferences")
        if isinstance(arr, list):
            seen, kept = set(), []
            for fr in arr:
                if isinstance(fr, dict) and isinstance(fr.get("funderName"), str):
                    rep = _canon(fr["funderName"], remap)
                    if rep != fr["funderName"]:
                        fr["funderName"] = rep
                        changed = True
                    key = (rep, fr.get("awardNumber"))
                    if key in seen:
                        changed = True
                        continue
                    seen.add(key)
                kept.append(fr)
            if kept != arr:
                d["fundingReferences"] = kept
    elif field == "affiliation":
        for cr in d.get("creators") or []:
            if not isinstance(cr, dict) or not isinstance(cr.get("affiliation"), list):
                continue
            seen, kept = set(), []
            for a in cr["affiliation"]:
                if isinstance(a, dict) and isinstance(a.get("name"), str):
                    rep = _canon(a["name"], remap)
                    if rep != a["name"]:
                        a["name"] = rep
                        changed = True
                    key = (rep, a.get("affiliationIdentifier"))
                    if key in seen:
                        changed = True
                        continue
                    seen.add(key)
                kept.append(a)
            if kept != cr["affiliation"]:
                cr["affiliation"] = kept
    elif field == "subject":
        arr = d.get("subjects")
        if isinstance(arr, list):
            seen, kept = set(), []
            for s in arr:
                n = s.get("subject") if isinstance(s, dict) else s
                if isinstance(n, str):
                    rep = _canon(n, remap)
                    if rep != n:
                        s = {**s, "subject": rep} if isinstance(s, dict) else rep
                        changed = True
                    k = collapse_key(rep)
                    if k in seen:
                        changed = True
                        continue
                    seen.add(k)
                kept.append(s)
            if kept != arr:
                d["subjects"] = kept
    elif field == "location":
        conf = d.get("conference")
        if isinstance(conf, dict) and isinstance(conf.get("conferenceLocation"), str):
            rep = _canon(conf["conferenceLocation"], remap)
            if rep != conf["conferenceLocation"]:
                conf["conferenceLocation"] = rep
                changed = True
    return changed


def run_field(files, field, dry_run, show):
    remap, counts = build_remap(files, field)
    samples = [(m, r) for m, r in list(remap.items())[:show]]
    changed = 0
    for f in files:
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        if apply_field(d, field, remap):
            changed += 1
            if not dry_run:
                Path(f).write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  [{field}] {len(remap)} stray variants collapsed; {changed} records changed"
          + (" (dry-run)" if dry_run else ""))
    for m, r in samples:
        print(f"      {m!r} -> {r!r}")
    return len(remap)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--field", default="all", choices=FIELDS + ["all"])
    ap.add_argument("--merged-dir", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--show", type=int, default=8)
    args = ap.parse_args()
    files = [str(p) for p in Path(args.merged_dir).rglob("*.json")]
    fields = FIELDS if args.field == "all" else [args.field]
    print(f"collapse_exact over {len(files)} files, fields={fields}")
    for field in fields:
        run_field(files, field, args.dry_run, args.show)


if __name__ == "__main__":
    main()
