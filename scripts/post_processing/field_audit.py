#!/usr/bin/env python3
"""
Read-only audit of the remaining deposit-imported fields across a merged corpus,
to plan the same normalize-and-drop-junk cleanup we did for licenses and dates.

Reports per field: coverage, value distributions, and suspected junk/anomalies
with samples. Fields covered: publisher, language, types, formats, subjects,
conference, creators, identifiers, relatedIdentifiers.

Usage:
    python field_audit.py --merged-dir /storage/poster-work/pre2025/merged
"""
import argparse
import json
import re
from collections import Counter
from pathlib import Path

_PLACEHOLDER = {
    "not specified", "unspecified", "unknown", "n/a", "na", "none", "null",
    "not found", "not applicable", "tbd", "tba", "name of conference",
    "conference name", "city, country", "institution name", "poster title",
    "main poster title", "untitled", "untitled poster", "-", "",
}
_URL_RE = re.compile(r"https?://|www\.", re.I)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def _ph(s):
    return isinstance(s, str) and s.strip().lower() in _PLACEHOLDER


def _sample(lst, n=6):
    return lst[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--merged-dir", required=True)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    files = sorted(Path(args.merged_dir).rglob("*.json"))
    total = 0

    cov = Counter()
    publisher = Counter()
    language = Counter()
    restype = Counter()
    fmt = Counter()

    subj_records = 0; subj_total = 0; subj_junk = []; subj_seen = set()
    conf_records = 0; conf_fields = Counter(); conf_junk_name = []; conf_field_junk = []
    cre_records = 0; cre_total = 0; cre_noname = 0; cre_junk = []; cre_aff_junk = []
    ident_types = Counter(); ident_junk = []
    rel_types = Counter()

    for f in files:
        if args.limit and total >= args.limit:
            break
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        total += 1
        for k in ("publisher", "language", "types", "formats", "subjects",
                  "conference", "creators", "identifiers", "relatedIdentifiers"):
            if d.get(k):
                cov[k] += 1

        pub = d.get("publisher")
        if isinstance(pub, dict):
            publisher[pub.get("name", "?")] += 1
        elif pub:
            publisher[str(pub)] += 1

        if d.get("language"):
            language[str(d["language"])] += 1

        t = d.get("types")
        if isinstance(t, dict):
            restype[f"{t.get('resourceType','?')} / {t.get('resourceTypeGeneral','?')}"] += 1

        for x in (d.get("formats") or []):
            fmt[str(x)] += 1

        subs = d.get("subjects") or []
        if subs:
            subj_records += 1
        for s in subs:
            subj_total += 1
            val = s.get("subject") if isinstance(s, dict) else s
            if not isinstance(val, str):
                continue
            subj_seen.add(val.strip().lower())
            if _ph(val) or len(val) > 80 or _URL_RE.search(val) or _EMAIL_RE.search(val) or val.count(",") >= 3:
                if len(subj_junk) < 25:
                    subj_junk.append(val[:90])

        conf = d.get("conference")
        if isinstance(conf, dict) and conf:
            conf_records += 1
            for ck, cv in conf.items():
                conf_fields[ck] += 1
                if isinstance(cv, str) and _ph(cv) and len(conf_field_junk) < 20:
                    conf_field_junk.append(f"{ck}={cv!r}")
            nm = conf.get("conferenceName", "")
            if _ph(nm) and len(conf_junk_name) < 12:
                conf_junk_name.append(nm)

        cres = d.get("creators") or []
        if cres:
            cre_records += 1
        for c in cres:
            if not isinstance(c, dict):
                continue
            cre_total += 1
            nm = c.get("name", "")
            if not nm or not str(nm).strip():
                cre_noname += 1
            elif _ph(nm) or _URL_RE.search(str(nm)) or _EMAIL_RE.search(str(nm)) or any(ch.isdigit() for ch in str(nm)):
                if len(cre_junk) < 20:
                    cre_junk.append(str(nm)[:70])
            for aff in (c.get("affiliation") or []):
                an = aff.get("name") if isinstance(aff, dict) else aff
                if isinstance(an, str) and (_ph(an) or len(an) > 200) and len(cre_aff_junk) < 15:
                    cre_aff_junk.append(an[:80])

        for i in (d.get("identifiers") or []):
            if isinstance(i, dict):
                ident_types[i.get("identifierType", "?")] += 1
                iv = i.get("identifier", "")
                if not iv or _ph(iv):
                    if len(ident_junk) < 12:
                        ident_junk.append(repr(iv))
        for r in (d.get("relatedIdentifiers") or []):
            if isinstance(r, dict):
                rel_types[r.get("relationType", "?")] += 1

    print(f"TOTAL records: {total}\n")
    print("coverage:", {k: cov[k] for k in cov})
    print("\n=== publisher ===", dict(publisher.most_common(10)))
    print("\n=== language ===", dict(language.most_common(15)))
    print("\n=== types ===", dict(restype.most_common(8)))
    print("\n=== formats ===", dict(fmt.most_common(12)))
    print(f"\n=== subjects ===  records={subj_records} entries={subj_total} distinct={len(subj_seen)}")
    print("  suspicious samples:", _sample(subj_junk, 15))
    print(f"\n=== conference === records={conf_records}")
    print("  field coverage:", dict(conf_fields.most_common()))
    print("  placeholder conferenceName count-sample:", _sample(conf_junk_name))
    print("  placeholder field values:", _sample(conf_field_junk, 15))
    print(f"\n=== creators === records={cre_records} entries={cre_total} no_name={cre_noname}")
    print("  junk-name samples:", _sample(cre_junk, 15))
    print("  junk-affiliation samples:", _sample(cre_aff_junk, 10))
    print(f"\n=== identifiers === types={dict(ident_types)} junk={_sample(ident_junk)}")
    print(f"=== relatedIdentifiers === relationTypes={dict(rel_types.most_common(12))}")


if __name__ == "__main__":
    main()
