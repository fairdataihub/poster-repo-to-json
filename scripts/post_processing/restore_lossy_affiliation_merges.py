#!/usr/bin/env python3
"""Undo affiliation renames that changed which organization is meant.

The affiliation synlustre map sends each variant to its cluster's most frequent name. When
that name means another organization, the merge rewrote the author's affiliation:
"Library & Researcher Information Services" -> "Library", "New Mexico State University"
-> "University of New Mexico" (keeping New Mexico State's ROR id), "The Hockaday School"
-> "The Hotchkiss School".

The evidence is the record's own pre-merge state, a snapshot of the merged corpus taken
before the map was applied (--snapshot-dir). For each creator, the snapshot person is the
one that matches the creator (same_author), unambiguously: one match, or the one at the
creator's own position. An affiliation the creator has now but neither the snapshot
person nor any of the record's sources (deposit, extractions) has was renamed after the
snapshot. Its pre-merge wording is a snapshot affiliation the creator no longer has, paired
on evidence: the map sends it there, it sits at the same position of an equally long list
(the map renamed in place), or it carried the same ROR id (the map kept ids). An entry with
no such evidence, such as one added later, is never paired, and a junk wording a later
cleanup removed on purpose ("1", "N/A") is never put back. When the rename changes
which organization is meant (affiliation_merge_is_lossy: fairly's identity rule plus the
ROR names), the pre-merge wording is put back; a harmless rename ("The University of
Manchester" -> "University of Manchester") is kept. Without snapshot evidence nothing is
changed. Never drops a creator or an affiliation. Idempotent.

A ROR id on the renamed affiliation is the one the pre-merge entry had (the map renamed
names and kept ids) or one attached later to the new name. The restored affiliation takes
the snapshot entry's own id; failing that the current id stays only when the restored name
is exactly (case and spacing aside) one of that ROR record's names; otherwise the id,
scheme and schemeURI are removed. Run this before backfill_datacite_ror_affiliations.py,
which then adds the registrant's ROR affiliation.

Usage (pubverse env):
    ~/myenv/bin/python restore_lossy_affiliation_merges.py \
        --corpus-dir /storage/poster-work/pre2025 \
        --snapshot-dir /storage/poster-work/azure_snap/merged \
        --synlustre /storage/poster-work/syn_affiliation.pickle \
        --ror-data /storage/poster-work/ror-data/v2.15-2026-10-20-ror-data_schema_v2.json \
        [--merged-subdir merged] --dry-run --show 30
"""
import argparse
import json
import logging
import pickle
import sys
import unicodedata
from collections import Counter
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

_SRC = Path(__file__).resolve().parents[2] / "src"
if _SRC.exists():
    sys.path.insert(0, str(_SRC))
from poster_to_json.field_normalize import (  # noqa: E402
    _has_letter, _is_placeholder, affiliation_merge_is_lossy, load_identity_rule, same_author,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpus_index import extraction_files  # noqa: E402
from backfill_datacite_ror_affiliations import (  # noqa: E402
    _exact, _ror_info, load_ror_names, norm_ror_id,
)

_ID_FIELDS = ("affiliationIdentifier", "affiliationIdentifierScheme", "schemeURI")


def _clean(s):
    return unicodedata.normalize("NFKC", str(s)).strip()


def _aff_names(value):
    """(affiliation string, ROR id or None) pairs from a deposit or extraction creator
    (string, list of strings or of {"name": ..., "affiliationIdentifier": ...}).
    Semicolon-lumped strings are split, as the merger does; their parts carry no id."""
    items = value if isinstance(value, list) else [value]
    out = []
    for a in items:
        n = a.get("name") if isinstance(a, dict) else a
        if not isinstance(n, str):
            continue
        parts = [_clean(p) for p in n.split(";") if _clean(p)]
        rid = norm_ror_id(a.get("affiliationIdentifier")) if isinstance(a, dict) and len(parts) == 1 else None
        out.extend((p, rid) for p in parts)
    return out


def _entry(a):
    """(name, ROR id or None) for an affiliation given as a string or a pair."""
    return (a, None) if isinstance(a, str) else (a[0], a[1])


def source_creators(corpus, src, rid, snapshot_dir=None):
    """[(name, [(affiliation, ROR id)], source, position)] from the pre-merge snapshot of
    the merged record (source "snapshot"), the deposit metadata ("deposit") and each
    extraction ("extraction:<file>")."""
    people = []
    if snapshot_dir:
        try:
            snap = json.loads((Path(snapshot_dir) / src / f"{rid}_complete.json").read_text(encoding="utf-8"))
            for i, c in enumerate(snap.get("creators") or []):
                if isinstance(c, dict):
                    people.append((c.get("name"), _aff_names(c.get("affiliation")), "snapshot", i))
        except Exception:
            pass
    meta = Path(corpus) / "metadata" / src / f"{rid}.json"
    try:
        m = json.loads(meta.read_text(encoding="utf-8"))
        if src == "zenodo":
            for i, c in enumerate((m.get("metadata") or {}).get("creators") or []):
                people.append((c.get("name"), _aff_names(c.get("affiliation")), "deposit", i))
        else:   # Figshare deposits carry no affiliation; keep names for matching
            for i, c in enumerate(m.get("authors") or []):
                people.append((c.get("full_name"), [], "deposit", i))
    except Exception:
        pass
    for f in extraction_files(corpus, src, rid):
        try:
            e = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        for i, c in enumerate(e.get("creators") or []):
            if isinstance(c, dict):
                people.append((c.get("name"), _aff_names(c.get("affiliation")), f"extraction:{Path(f).name}", i))
    return people


def snapshot_person(i, creators, people):
    """The snapshot person of creator `i`: the only one matching it, or the one at its own
    position; None when there is none or the match is ambiguous."""
    name = str(creators[i].get("name") or "")
    hits = [p for p in people if len(p) > 2 and p[2] == "snapshot" and p[0] and same_author(name, str(p[0]))]
    if len(hits) == 1:
        others = [str(c.get("name") or "") for k, c in enumerate(creators) if k != i and isinstance(c, dict)]
        if not any(o and same_author(o, str(hits[0][0])) for o in others) or hits[0][3] == i:
            return hits[0]
        return None
    at_pos = [p for p in hits if p[3] == i]
    return at_pos[0] if len(at_pos) == 1 else None


def sourced_names(i, creators, people):
    """Every affiliation the record's sources (deposit, extractions) give any person that
    matches creator `i`; generous on purpose, since a name found here is kept."""
    name = str(creators[i].get("name") or "")
    return {_entry(a)[0] for p in people if not (len(p) > 2 and p[2] == "snapshot")
            and p[0] and same_author(name, str(p[0])) for a in p[1]}


def pre_merge_wording(now, pos, a, affs, snap_affs, missing, syn, ror=None):
    """The snapshot wording an affiliation was renamed from, on evidence only: the map sends
    it there; or it sits at the same position of a list whose length did not change (the
    map renamed in place); or it carried the same ROR id (the map kept ids). The last two
    never pair an entry named exactly as its own ROR id (a DataCite affiliation): its name
    and id agree, so nothing says it was renamed."""
    cands = [v for v in missing if syn.get(v) == now]
    if cands:
        return cands, "map"
    rid = norm_ror_id(a.get("affiliationIdentifier"))
    info = _ror_info(ror, rid) if ror and rid else None
    if info and _exact(now) in info[4]:
        return [], None
    if len(snap_affs) == len(affs) and snap_affs[pos][0] in missing:
        return [snap_affs[pos][0]], "position"
    if rid:
        cands = [n for n, r in snap_affs if r == rid and n in missing]
        if cands:
            return cands, "ror_id"
    return [], None


def _junk(v):
    """A wording a later cleanup step removed on purpose: no letter, two characters or
    fewer, a placeholder ("1", "1, 2", "N/A")."""
    v = str(v or "").strip()
    return len(v) <= 2 or not _has_letter(v) or _is_placeholder(v)


def restore_record(d, people, rule, syn, samples, show, ror=None, stats=None):
    """Put back pre-merge affiliations that a rename turned into another organization;
    returns the number of affiliations restored. `people` is source_creators() output,
    `rule` an IdentityRule (or place words), `syn` the synonym map (variant -> canonical,
    used to pair a renamed affiliation with its pre-merge wording), `ror`
    load_ror_names() output, without which a restored affiliation keeps an id only when
    the snapshot gives one. Skips are counted in `stats`."""
    stats = Counter() if stats is None else stats
    changed = 0
    creators = d.get("creators") or []
    for i, cr in enumerate(creators):
        if not isinstance(cr, dict) or not isinstance(cr.get("affiliation"), list):
            continue
        affs = [a for a in cr["affiliation"] if isinstance(a, dict) and a.get("name")]
        if not affs:
            continue
        snap = snapshot_person(i, creators, people)
        if snap is None:
            stats["no_snapshot_person"] += 1
            continue
        snap_affs = [_entry(a) for a in snap[1]]
        snap_names = [n for n, _ in snap_affs]
        sourced = sourced_names(i, creators, people)
        current = {a["name"] for a in affs}
        missing = [n for n in dict.fromkeys(snap_names) if n not in current]
        for pos, a in enumerate(affs):
            now = a["name"]
            if now in snap_names or now in sourced:
                continue
            cands, how = pre_merge_wording(now, pos, a, affs, snap_affs, missing, syn, ror)
            cands = [v for v in cands if not _junk(v)]
            if not cands:
                stats["unexplained"] += 1
                continue
            v = cands[0]
            if not affiliation_merge_is_lossy(v, now, rule):
                stats["harmless_rename_kept"] += 1
                continue
            if show is not None and len(samples) < show:
                samples.append(f"{now!r} -> {v!r}")
            a["name"] = v
            src_id = next((r for n, r in snap_affs if n == v and r), None)
            rid = norm_ror_id(a.get("affiliationIdentifier"))
            info = _ror_info(ror, rid) if ror and rid else None
            if src_id:
                a.update({"affiliationIdentifier": src_id, "affiliationIdentifierScheme": "ROR",
                          "schemeURI": "https://ror.org"})
                stats["ror_id_from_snapshot"] += 1
            elif any(k in a for k in _ID_FIELDS):
                if info and _exact(v) in info[4]:
                    stats["ror_id_kept"] += 1
                else:
                    for k in _ID_FIELDS:
                        a.pop(k, None)
                    stats["ror_id_dropped"] += 1
            missing.remove(v)
            stats["paired_by_" + how] += 1
            changed += 1
    return changed


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus-dir", required=True, help="has metadata/, extractions/ and the merged dir")
    ap.add_argument("--merged-subdir", default="merged")
    ap.add_argument("--snapshot-dir", required=True,
                    help="pre-merge copy of the merged dir (zenodo/, figshare/): the evidence of a rename")
    ap.add_argument("--only", default=None, help="process one record id, e.g. figshare/27063577")
    ap.add_argument("--synlustre", required=True)
    ap.add_argument("--ror-data", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--show", type=int, default=0)
    args = ap.parse_args()

    syn = pickle.load(open(args.synlustre, "rb"))
    rule = load_identity_rule(args.ror_data)
    ror = load_ror_names(args.ror_data)
    logger.info(f"[{'DRY-RUN' if args.dry_run else 'LIVE'}] restore lossy affiliation renames  "
                f"corpus={args.corpus_dir} snapshot={args.snapshot_dir}")

    stats, samples = Counter(), []
    show = args.show or None
    for src in ("zenodo", "figshare"):
        for f in sorted((Path(args.corpus_dir) / args.merged_subdir / src).glob("*_complete.json")):
            if args.only and args.only != f"{src}/{f.name[:-len('_complete.json')]}":
                continue
            stats["scanned"] += 1
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                stats["errors"] += 1
                continue
            rid = f.name[: -len("_complete.json")]
            if not (Path(args.snapshot_dir) / src / f.name).exists():
                stats["not_in_snapshot"] += 1
                continue
            n = restore_record(d, source_creators(args.corpus_dir, src, rid, args.snapshot_dir), rule, syn,
                               samples, show, ror, stats)
            if not n:
                continue
            stats["records_restored"] += 1
            stats["affiliations_restored"] += n
            if not args.dry_run:
                f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    for s in samples:
        logger.info(f"  e.g. {s}")
    for k in ("scanned", "not_in_snapshot", "records_restored", "affiliations_restored", "harmless_rename_kept",
              "paired_by_map", "paired_by_position", "paired_by_ror_id", "unexplained", "no_snapshot_person",
              "ror_id_from_snapshot", "ror_id_kept", "ror_id_dropped",
              "errors"):
        logger.info(f"  {k:24s} {stats[k]}")


if __name__ == "__main__":
    main()
