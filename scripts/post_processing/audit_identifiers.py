#!/usr/bin/env python3
"""Audit, and optionally repair, ROR / Crossref Funder ids that name another organization
than their text.

An identifier attached to a name is a claim that the name means that organization. A
synonym merge that renamed an affiliation but kept its id, or an id attached by fuzzy
matching (older extractions), leaves "University College London" with the University of
London's ROR id or "University of Connecticut" with the University of New Haven's.

Checked: creators[]/contributors[].affiliation[] (affiliationIdentifier) and
fundingReferences[] (funderIdentifier; Crossref Funder ids resolved to ROR through the ROR
dump's fundref ids). A name is consistent with its id when it is one of the ROR record's
names (display, alias, label, acronym) case-, accent- and punctuation-insensitively, when
it contains one of them as a whole phrase ("Dept. of Physics, Example University"), or
when it names the same organization as one of them under the identity rule
(field_normalize.IdentityRule: "ADNET Systems Inc." for "Adnet Systems (United States)",
"Purdue University" for "Purdue University West Lafayette"). Otherwise it is a mismatch.
The same ROR id twice on one person is reported too.

With --fix (run after restore_lossy_affiliation_merges.py, which puts back names that a
merge renamed, and before backfill_datacite_ror_affiliations.py), a mismatched id is
replaced by the id of the one active ROR organization the name exactly names, and is
otherwise removed with its scheme fields; the name always stays. Identical affiliation
entries a removal leaves behind are merged into one. With --cache-dir, an id the record's
DOI registered with DataCite is never changed: the repository asserted it, and a renamed
organization ("St George's, University of London" in City St George's) keeps its id. A placeholder funder name
("Unknown Funder") with a resolvable id takes the id's ROR display name. Idempotent.

Usage (pubverse env):
    ~/myenv/bin/python audit_identifiers.py --merged-dir /storage/poster-work/pre2025/merged \
        --ror-data /storage/poster-work/ror-data/v2.15-2026-10-20-ror-data_schema_v2.json \
        --out /storage/poster-work/scratch/audit_ids_pre2025.tsv [--fix [--dry-run]]
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if _SRC.exists():
    sys.path.insert(0, str(_SRC))
from poster_to_json.field_normalize import IdentityRule, _ror_name_key  # noqa: E402

_ROR_RE = re.compile(r"^(?:(?:https?://)?(?:www\.)?ror\.org/)?([0-9a-z]{9})$")
_FUNDREF_RE = re.compile(r"10\.13039/(\d+)$")
_AFF_ID_FIELDS = ("affiliationIdentifier", "affiliationIdentifierScheme", "schemeURI", "schemeUri")
_FUNDER_ID_FIELDS = ("funderIdentifier", "funderIdentifierType", "schemeUri", "schemeURI")
_PLACEHOLDER_FUNDERS = {"unknown funder", "unknown", "n a", "none", "not applicable", "no funding"}


def ror_id(v):
    m = _ROR_RE.match(str(v or "").strip().lower().rstrip("/"))
    return m.group(1) if m else None


def fundref_id(v):
    m = _FUNDREF_RE.search(str(v or "").strip().lower().rstrip("/"))
    return m.group(1) if m else None


class Ror:
    """The parts of a ROR v2 dump the audit needs."""

    def __init__(self, path):
        orgs = json.loads(Path(path).read_text(encoding="utf-8"))
        from fairly.authorities.ror import identity_words_from_orgs
        place, generic = identity_words_from_orgs(orgs)
        self.names, self.display, self.status, self.fundref_of, self.successors = {}, {}, {}, {}, {}
        self.by_fundref, by_key = {}, {}
        for org in orgs:
            rid = ror_id(org.get("id"))
            if not rid:
                continue
            vals = [n.get("value") for n in org.get("names") or [] if n.get("value")]
            self.names[rid] = vals
            self.display[rid] = next((n["value"] for n in org.get("names") or []
                                      if "ror_display" in (n.get("types") or [])), vals[0] if vals else None)
            self.status[rid] = org.get("status") or "active"
            self.successors[rid] = [r for r in (ror_id(x.get("id")) for x in org.get("relationships") or []
                                                if str(x.get("type") or "").lower() == "successor") if r]
            for v in vals:
                by_key.setdefault(_ror_name_key(v), set()).add(rid)
            for ext in org.get("external_ids") or []:
                if str(ext.get("type")).lower() == "fundref":
                    for f in ext.get("all") or []:
                        f = str(f).split("10.13039/")[-1]
                        self.by_fundref.setdefault(f, rid)
                        self.fundref_of.setdefault(rid, ext.get("preferred") or f)
        self.by_key = {k: frozenset(v) for k, v in by_key.items() if k}
        long_names = {k: v for k, v in self.by_key.items() if len(k) > 3}
        self.rule = IdentityRule(place, generic, long_names)

    def exact_active(self, name):
        """The one active ROR id whose names include `name` exactly, else None."""
        ids = [r for r in self.by_key.get(_ror_name_key(name), ()) if self.status.get(r) == "active"]
        return ids[0] if len(ids) == 1 else None

    def classify(self, name, rid):
        vals = self.names.get(rid)
        if vals is None:
            return "id_not_in_dump"
        k = _ror_name_key(name)
        keys = {_ror_name_key(v) for v in vals}
        if k in keys:
            return "consistent"
        if any(n and len(n) > 3 and re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", k) for n in keys):
            return "contains"
        if any(not self.rule.guard(name, v) for v in vals):
            return "same_organization"
        return "mismatch"


def _person_affs(d):
    for role in ("creators", "contributors"):
        for c in d.get(role) or []:
            if isinstance(c, dict):
                yield c


def registrant_ids(d, cache_dir, ror=None):
    """ROR ids (and Crossref Funder ids) the record's DOI registered with DataCite, from the
    cache of backfill_datacite_ror_affiliations.py, with the successors of inactive ones
    (which that backfill writes instead): asserted by the repository, never "repaired"."""
    if not cache_dir:
        return set()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from backfill_datacite_ror_affiliations import _cache_state, _doi_of, _safe
    doi = _doi_of(d)
    if not doi:
        return set()
    state, attrs = _cache_state(Path(cache_dir) / f"{_safe(doi)}.json", 10 ** 6)
    if state != "hit":
        return set()
    out = set()
    for role in ("creators", "contributors"):
        for c in attrs.get(role) or []:
            for a in (c.get("affiliation") or []) if isinstance(c, dict) else []:
                r = ror_id(a.get("affiliationIdentifier")) if isinstance(a, dict) else None
                if r:
                    out.add(r)
    for f in attrs.get("fundingReferences") or []:
        v = f.get("funderIdentifier") if isinstance(f, dict) else None
        r = ror_id(v) or fundref_id(v)
        if r:
            out.add(r)
    if ror is not None:
        out |= {s for r in list(out) for s in ror.successors.get(r, ())}
    return out


def audit_record(d, ror, stats, rows, rec, fix=False, registered=frozenset()):
    """Count (and with `fix` repair) one record; returns the number of changes. Ids in
    `registered` (registrant_ids) are reported but never changed."""
    changed = 0
    for c in _person_affs(d):
        seen = Counter()
        for a in c.get("affiliation") or []:
            if not isinstance(a, dict) or not a.get("affiliationIdentifier"):
                continue
            rid = ror_id(a.get("affiliationIdentifier"))
            if not rid:
                stats["aff_bad_id"] += 1
                rows.append((rec, "aff_bad_id", c.get("name"), a.get("name"), a.get("affiliationIdentifier"), ""))
                continue
            cls = ror.classify(a.get("name"), rid)
            stats["aff_" + cls] += 1
            if cls not in ("mismatch", "id_not_in_dump"):
                seen[rid] += 1
                if len([r for r in ror.by_key.get(_ror_name_key(a.get("name")), ())
                        if ror.status.get(r) == "active"]) > 1:
                    stats["aff_ambiguous_name"] += 1        # reported only: "UPM" names several orgs
                    rows.append((rec, "aff_ambiguous_name", c.get("name"), a.get("name"), rid, ""))
                continue
            if rid in registered:
                stats["aff_registrant_id_kept"] += 1
                rows.append((rec, "aff_" + cls, c.get("name"), a.get("name"), rid, "registrant id kept"))
                seen[rid] += 1
                continue
            new = ror.exact_active(a.get("name"))
            action = f"id -> {new}" if new else "id removed"
            rows.append((rec, "aff_" + cls, c.get("name"), a.get("name"), rid, action if fix else ""))
            if fix:
                for k in _AFF_ID_FIELDS:
                    a.pop(k, None)
                if new:
                    a.update({"affiliationIdentifier": "https://ror.org/" + new,
                              "affiliationIdentifierScheme": "ROR", "schemeURI": "https://ror.org"})
                    seen[new] += 1
                stats["aff_fixed_" + ("replaced" if new else "removed")] += 1
                changed += 1
        dup = [r for r, n in seen.items() if n > 1]
        if dup:
            stats["duplicate_id"] += len(dup)
            rows.extend((rec, "duplicate_id", c.get("name"), "", r, "") for r in dup)
        if fix and isinstance(c.get("affiliation"), list):
            # removing a wrong id can leave two identical entries ("Institute for Genetics"
            # twice, each with a different wrong id); keep one
            uniq = []
            for a in c["affiliation"]:
                if a not in uniq:
                    uniq.append(a)
            if len(uniq) < len(c["affiliation"]):
                stats["aff_duplicates_removed"] += len(c["affiliation"]) - len(uniq)
                c["affiliation"] = uniq
                changed += 1
    for fr in d.get("fundingReferences") or []:
        if not isinstance(fr, dict) or not fr.get("funderIdentifier"):
            continue
        v = fr.get("funderIdentifier")
        rid = ror_id(v)
        if not rid:
            f = fundref_id(v)
            rid = ror.by_fundref.get(f) if f else None
            if f and not rid:
                stats["funder_unresolved"] += 1
                continue
        if not rid:
            stats["funder_bad_id"] += 1
            rows.append((rec, "funder_bad_id", "", fr.get("funderName"), v, ""))
            continue
        name = fr.get("funderName")
        if _ror_name_key(name) in _PLACEHOLDER_FUNDERS and ror.display.get(rid):
            stats["funder_placeholder_name"] += 1
            rows.append((rec, "funder_placeholder_name", "", name, f"{v} (ror {rid})",
                         f"name -> {ror.display[rid]}" if fix else ""))
            if fix:
                fr["funderName"] = ror.display[rid]
                changed += 1
            continue
        cls = ror.classify(name, rid)
        stats["funder_" + cls] += 1
        if cls not in ("mismatch", "id_not_in_dump"):
            continue
        if rid in registered or fundref_id(v) in registered:
            stats["funder_registrant_id_kept"] += 1
            rows.append((rec, "funder_" + cls, "", name, f"{v} (ror {rid})", "registrant id kept"))
            continue
        new = ror.exact_active(name)
        rows.append((rec, "funder_" + cls, "", name, f"{v} (ror {rid})",
                     (f"id -> {new}" if new else "id removed") if fix else ""))
        if fix:
            ftype = str(fr.get("funderIdentifierType") or "")
            for k in _FUNDER_ID_FIELDS:
                fr.pop(k, None)
            if new:
                fref = ror.fundref_of.get(new)
                if ftype.lower().startswith("crossref") and fref:
                    fr.update({"funderIdentifier": f"https://doi.org/10.13039/{fref}",
                               "funderIdentifierType": "Crossref Funder ID"})
                else:
                    fr.update({"funderIdentifier": "https://ror.org/" + new, "funderIdentifierType": "ROR",
                               "schemeUri": "https://ror.org/"})
            stats["funder_fixed_" + ("replaced" if new else "removed")] += 1
            changed += 1
    return changed


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merged-dir", required=True)
    ap.add_argument("--ror-data", required=True)
    ap.add_argument("--out", default=None, help="TSV of every mismatch (and the repair made)")
    ap.add_argument("--fix", action="store_true", help="repair mismatched ids (see above)")
    ap.add_argument("--cache-dir", default=None,
                    help="DataCite cache (backfill_datacite_ror_affiliations.py): ids the record's DOI "
                         "registered are never changed (a renamed organization keeps its registrant id)")
    ap.add_argument("--dry-run", action="store_true", help="with --fix: report the repairs, write nothing")
    args = ap.parse_args()
    ror = Ror(args.ror_data)
    stats, rows = Counter(), []
    for f in sorted(Path(args.merged_dir).rglob("*_complete.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            stats["errors"] += 1
            continue
        stats["records"] += 1
        rec = f"{f.parent.name}/{f.name[:-len('_complete.json')]}"
        n = audit_record(d, ror, stats, rows, rec, fix=args.fix, registered=registrant_ids(d, args.cache_dir, ror))
        if n:
            stats["records_fixed"] += 1
            if not args.dry_run:
                f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  [{'FIX' + (' DRY-RUN' if args.dry_run else '') if args.fix else 'AUDIT'}] {args.merged_dir}")
    for k in sorted(stats):
        print(f"  {k:28s} {stats[k]}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write("record\tkind\tperson\tname\tid\taction\n")
            for r in rows:
                fh.write("\t".join(str(x or "").replace("\t", " ") for x in r) + "\n")
        print(f"  wrote {len(rows)} rows -> {args.out}")


if __name__ == "__main__":
    main()
