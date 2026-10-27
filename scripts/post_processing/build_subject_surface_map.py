#!/usr/bin/env python3
"""Build a CONSERVATIVE subject synonym map: surface variants only.

Subjects are a controlled-vocabulary-heavy field (ANZSRC Fields of Research, incl.
"... not elsewhere classified" labels) that must NOT be semantically merged or
rewritten. This groups subject terms only when they are identical after a surface
normalization -- lowercase, punctuation/hyphen -> space, collapse whitespace, and a
light trailing-plural stem per word (not on "-ss" words or all-caps acronyms: "mass" and
"mas", "NGSS" and "NGS" stay apart) -- so it folds `machine learning` / `Machine
Learning` / `machine-learning` and `genomics` / `genomic`, but never merges two
distinct concepts (e.g. `Computational Biology` stays; it does not fold into
`Bioinformatics and computational biology not elsewhere classified`). Canonical =
the most frequent actual surface form in the group.

Emits a variant->canonical pickle (apply with apply_synlustre.py --field subject)
plus a human-review TSV.

Usage (pubverse env):
    ~/myenv/bin/python build_subject_surface_map.py \
        --terms /storage/poster-work/syn_subject.pickle.terms.json \
        --out /storage/poster-work/syn_subject_surface.pickle \
        --review /storage/poster-work/syn_subject_surface_review.tsv
"""
import argparse
import glob
import json
import pickle
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpus_index import extraction_files  # noqa: E402


def _stem(w):
    """Light plural stem: drop a trailing ASCII "s" on a word longer than 3 chars, but not
    from "-ss" ("mass" is not "mas") or an all-caps acronym ("NGSS" is not "NGS")."""
    if len(w) > 3 and w.isascii() and w.lower().endswith("s") and not w.lower().endswith("ss") and not w.isupper():
        return w[:-1].lower()
    return w.lower()


def surface_key(s):
    # keep Unicode letters/digits (so non-Latin scripts are NOT stripped to empty);
    # fold case, turn punctuation/hyphens into spaces, collapse whitespace.
    k = unicodedata.normalize("NFKC", str(s))
    k = re.sub(r"[^\w]+", " ", k, flags=re.UNICODE).strip()
    return " ".join(_stem(w) for w in k.split())


def _old_surface_key(s):
    """surface_key before 0.41.0, which also stripped "-ss" words and acronyms."""
    k = re.sub(r"[^\w]+", " ", unicodedata.normalize("NFKC", str(s)).lower(), flags=re.UNICODE).strip()
    return " ".join(w[:-1] if len(w) > 3 and w.endswith("s") and w.isascii() else w for w in k.split())


def _subjects(value):
    out = []
    for x in value if isinstance(value, list) else [value]:
        n = x.get("subject") if isinstance(x, dict) else x
        if isinstance(n, str) and n.strip():
            out.append(n.strip())
    return out


def source_subjects(corpus, src, rid, snapshot_dir=None):
    """The subject texts a record's subjects came from: the pre-merge snapshot, the
    deposit (Zenodo keywords, Figshare tags and categories) and each extraction."""
    out = []
    if snapshot_dir:
        try:
            out += _subjects(json.loads((Path(snapshot_dir) / src / f"{rid}_complete.json")
                                        .read_text(encoding="utf-8")).get("subjects"))
        except Exception:
            pass
    try:
        m = json.loads((Path(corpus) / "metadata" / src / f"{rid}.json").read_text(encoding="utf-8"))
        md = m.get("metadata") or {}
        out += _subjects(md.get("keywords")) + _subjects(md.get("subjects")) + _subjects(m.get("tags"))
        out += [c.get("title") for c in m.get("categories") or [] if isinstance(c, dict) and c.get("title")]
    except Exception:
        pass
    for f in extraction_files(corpus, src, rid):
        try:
            out += _subjects(json.loads(Path(f).read_text(encoding="utf-8")).get("subjects"))
        except Exception:
            continue
    return set(out)


def repair_corpus(mapping, corpus, merged_subdir, snapshot_dir, dry_run):
    """Undo the merges of an applied map that the old stem made and the current one does
    not: a subject equal to such a canonical, absent from the record's sources, while a
    source has exactly one variant merged into it, gets that variant back."""
    bad = {}
    for v, c in mapping.items():
        if v != c and _old_surface_key(v) == _old_surface_key(c) and surface_key(v) != surface_key(c):
            bad.setdefault(c, set()).add(v)
    stats = Counter(bad_merges=sum(map(len, bad.values())))
    for src in ("zenodo", "figshare"):
        for f in sorted((Path(corpus) / merged_subdir / src).glob("*_complete.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                stats["errors"] += 1
                continue
            subs = [x for x in d.get("subjects") or [] if isinstance(x, dict) and x.get("subject") in bad]
            if not subs:
                continue
            srcs = source_subjects(corpus, src, f.name[: -len("_complete.json")], snapshot_dir)
            n = 0
            for x in subs:
                c = x["subject"]
                own = sorted(bad[c] & srcs)
                if c in srcs or len(own) != 1:
                    stats["kept"] += 1
                    continue
                x["subject"] = own[0]
                n += 1
            if n:
                stats["records_repaired"] += 1
                stats["subjects_repaired"] += n
                if not dry_run:
                    f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[surface] {'DRY-RUN ' if dry_run else ''}repair {corpus}: "
          + "  ".join(f"{k}={n}" for k, n in sorted(stats.items())))


def collect_terms(terms_path, merged_glob):
    if terms_path:
        return Counter(json.load(open(terms_path, encoding="utf-8")))
    c = Counter()
    for f in glob.glob(merged_glob):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        for s in d.get("subjects") or []:
            n = s.get("subject") if isinstance(s, dict) else s
            if isinstance(n, str) and n.strip():
                c[n.strip()] += 1
    return c


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--terms", help="terms.json cache (term -> freq); else scan --merged-glob")
    ap.add_argument("--merged-glob")
    ap.add_argument("--out", required=True)
    ap.add_argument("--review", required=True)
    ap.add_argument("--repair-corpus", default=None,
                    help="instead of building: undo the old stem's merges of the --out map in this corpus "
                         "(has metadata/, extractions/ and the merged dir)")
    ap.add_argument("--merged-subdir", default="merged")
    ap.add_argument("--snapshot-dir", default=None, help="with --repair-corpus: pre-merge copy of the merged dir")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.repair_corpus:
        repair_corpus(pickle.load(open(args.out, "rb")), args.repair_corpus, args.merged_subdir,
                      args.snapshot_dir, args.dry_run)
        return

    counts = collect_terms(args.terms, args.merged_glob)
    groups = defaultdict(list)
    for term in counts:
        k = surface_key(term)
        if k:                                          # skip punctuation-only / empty keys
            groups[k].append(term)

    mapping, clusters = {}, {}
    for key, members in groups.items():
        if len(members) < 2:
            continue
        rep = max(members, key=lambda m: counts[m])   # canonical = most frequent surface form
        for m in members:
            if m != rep:
                mapping[m] = rep
        clusters[rep] = sorted(members, key=lambda m: -counts[m])

    pickle.dump(mapping, open(args.out, "wb"))
    with open(args.review, "w", encoding="utf-8") as o:
        o.write("canonical\tfreq\tmerged_variants\n")
        for rep, members in sorted(clusters.items(), key=lambda kv: -sum(counts[m] for m in kv[1])):
            variants = " | ".join(f"{m}({counts[m]})" for m in members if m != rep)
            o.write(f"{rep}\t{counts[rep]}\t{variants}\n")
    print(f"[surface] {len(mapping)} variants -> canonical across {len(clusters)} groups "
          f"(of {len(counts)} distinct subjects)")


if __name__ == "__main__":
    main()
