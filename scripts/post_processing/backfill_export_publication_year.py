#!/usr/bin/env python3
"""Repair posterJson.publicationYear in a platform DB ndjson export.

The platform stamps a single export/ingest year on every record's
``publicationYear`` (poster2json deliberately does not set it, since it is
platform-owned). This restores the real year of each poster from the date
signals already present on the record, in priority order:

  1. ``posterJson.dates[]`` of dateType Issued > Published > Available > Created
  2. the envelope ``publishedAt`` timestamp (the deposit date)
  3. ``posterJson.conference.conferenceStartDate`` / ``conferenceYear``

Writes a corrected copy of the export (never mutates the input) and prints the
before/after year distribution. A record with no usable date signal is left
unchanged and counted "unresolved".

Usage:
    python backfill_export_publication_year.py --export-dir <dir> --out-dir <dir>
"""
import argparse
import glob
import json
import os
import re
from collections import Counter
from pathlib import Path

_YEAR = re.compile(r"(\d{4})")
_DATE_PRIORITY = {"Issued": 0, "Published": 1, "Available": 2, "Created": 3}
_MIN_YEAR, _MAX_YEAR = 1980, 2026


def _valid(y):
    return _MIN_YEAR <= y <= _MAX_YEAR


def recover_year(rec):
    pj = rec.get("posterJson") or {}
    best = None
    for d in (pj.get("dates") or []):
        if not isinstance(d, dict):
            continue
        m = _YEAR.match(str(d.get("date") or ""))
        pr = _DATE_PRIORITY.get(d.get("dateType"))
        if m and pr is not None:
            y = int(m.group(1))
            if _valid(y) and (best is None or pr < best[0]):
                best = (pr, y)
    if best:
        return best[1]
    m = _YEAR.match(str(rec.get("publishedAt") or ""))
    if m and _valid(int(m.group(1))):
        return int(m.group(1))
    conf = pj.get("conference") or {}
    for k in ("conferenceStartDate", "conferenceYear"):
        m = _YEAR.match(str(conf.get(k) or ""))
        if m and _valid(int(m.group(1))):
            return int(m.group(1))
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--export-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    before, after = Counter(), Counter()
    st = Counter()
    for ef in sorted(glob.glob(os.path.join(args.export_dir, "*.ndjson"))):
        out_lines = []
        for line in open(ef, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            st["records"] += 1
            rec = json.loads(line)
            pj = rec.get("posterJson")
            if isinstance(pj, dict):
                before[pj.get("publicationYear")] += 1
                y = recover_year(rec)
                if y is None:
                    st["unresolved"] += 1
                else:
                    if pj.get("publicationYear") != y:
                        st["changed"] += 1
                    pj["publicationYear"] = y
                    after[y] += 1
                rec["posterJson"] = pj
            out_lines.append(json.dumps(rec, ensure_ascii=False))
        Path(args.out_dir, os.path.basename(ef)).write_text("\n".join(out_lines) + "\n", encoding="utf-8")

    print("=== publicationYear repair ===")
    print(f"  records {st['records']} | changed {st['changed']} | unresolved {st['unresolved']}")
    print(f"  before: {dict(sorted(before.items(), key=lambda kv: str(kv[0])))}")
    print(f"  after (year: count): {dict(sorted((k, v) for k, v in after.items()))}")


if __name__ == "__main__":
    main()
