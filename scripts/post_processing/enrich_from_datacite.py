#!/usr/bin/env python3
"""
Phase-3 enrichment: merge cached DataCite metadata into merged Zenodo records.

Per SCHEMA_ALIGNMENT_PLAN.md + locked decisions:
  - creators (matched by name): fill/upgrade structured givenName/familyName
    (prefer the fuller value; keeps the fullest-form display `name`), set nameType
    from DataCite (authoritative — wins over the tag_org_creators heuristic for
    authors DataCite covers), fill ORCID when missing (never overwrite).
  - fundingReferences: DEPOSIT-AUTHORITATIVE — replace with DataCite funding
    (ROR funder ids normalized), dropping LLM-only funders. Empty deposit funding
    removes any LLM funders.
  - relatedIdentifiers: union DataCite depositor-declared relations (version-graph
    self-links to Zenodo concept/versions filtered out) with existing ones.
  - subjects: union DataCite subjects with existing (dedup).
  - descriptions: if DataCite has an "Other" (summary), use it over the LLM one.
Affiliation ROR is NOT sourced here (DataCite affiliation is name-only); the
text-matcher remains the ROR source. align_schema is re-run at the end.

Usage:
    python enrich_from_datacite.py --merged-dir <m>/zenodo --cache-dir <c> --dry-run [--show N]
"""
import argparse
import json
import logging
import re
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

_SRC = Path(__file__).resolve().parents[2] / "src"
if _SRC.exists():
    sys.path.insert(0, str(_SRC))
from poster_to_json.field_normalize import same_author, align_schema  # noqa: E402

_VERSION_RELATIONS = {"IsVersionOf", "HasVersion", "IsNewVersionOf", "IsPreviousVersionOf"}


def _safe(doi):
    return doi.replace("/", "_").replace(":", "_")


def _doi_of(d):
    for i in (d.get("identifiers") or []):
        if isinstance(i, dict) and i.get("identifierType") == "DOI" and i.get("identifier"):
            return str(i["identifier"]).strip()
    return None


def _dc_name(c):
    return c.get("name") or ", ".join(x for x in (c.get("familyName"), c.get("givenName")) if x)


def enrich_creators(mcres, dc_cres):
    changed = False
    used = set()
    for mc in mcres:
        if not isinstance(mc, dict) or not mc.get("name"):
            continue
        match = None
        for i, dc in enumerate(dc_cres):
            if i in used:
                continue
            if same_author(mc["name"], _dc_name(dc)):
                match = (i, dc)
                break
        if not match:
            continue
        used.add(match[0])
        dc = match[1]
        for fld in ("givenName", "familyName"):
            dv = dc.get(fld)
            if dv and (not mc.get(fld) or len(str(dv)) > len(str(mc.get(fld)))) and mc.get(fld) != dv:
                mc[fld] = dv
                changed = True
        if dc.get("nameType") and mc.get("nameType") != dc["nameType"]:
            mc["nameType"] = dc["nameType"]
            changed = True
        if not mc.get("nameIdentifiers") and dc.get("nameIdentifiers"):
            mc["nameIdentifiers"] = dc["nameIdentifiers"]
            changed = True
    return changed


def map_funding(dc_fund):
    out = {}
    if dc_fund.get("funderName"):
        out["funderName"] = dc_fund["funderName"]
    fid = dc_fund.get("funderIdentifier")
    ftype = dc_fund.get("funderIdentifierType")
    if fid:
        if ftype == "ROR":
            out["funderIdentifier"] = fid if str(fid).startswith("http") else f"https://ror.org/{fid}"
            out["funderIdentifierType"] = "ROR"
            out["schemeUri"] = "https://ror.org/"
        else:
            out["funderIdentifier"] = fid
            if ftype:
                out["funderIdentifierType"] = ftype
    for k in ("awardNumber", "awardTitle", "awardUri"):
        if dc_fund.get(k):
            out[k] = dc_fund[k]
    return out


def depositor_related(dc_related):
    out = []
    for r in dc_related or []:
        if not isinstance(r, dict) or not r.get("relatedIdentifier"):
            continue
        rid = str(r["relatedIdentifier"])
        if r.get("relationType") in _VERSION_RELATIONS and re.search(r"10\.5281/zenodo", rid, re.I):
            continue  # version-graph self-link
        entry = {"relatedIdentifier": rid,
                 "relatedIdentifierType": r.get("relatedIdentifierType", "DOI"),
                 "relationType": r.get("relationType", "References")}
        if r.get("resourceTypeGeneral"):
            entry["resourceTypeGeneral"] = r["resourceTypeGeneral"]
        out.append(entry)
    return out


def _union(fresh, existing, keyfn):
    out, seen = [], set()
    for r in (fresh or []) + (existing or []):
        k = keyfn(r)
        if k and k not in seen:
            seen.add(k)
            out.append(r)
    return out


def enrich(d, attrs):
    changed = False
    if enrich_creators(d.get("creators") or [], attrs.get("creators") or []):
        changed = True

    # funding: deposit-authoritative (drop LLM-only)
    dc_fund = [map_funding(x) for x in (attrs.get("fundingReferences") or []) if x.get("funderName")]
    if dc_fund != (d.get("fundingReferences") or []):
        if dc_fund:
            d["fundingReferences"] = dc_fund
            changed = True
        elif d.get("fundingReferences"):
            d.pop("fundingReferences", None)
            changed = True

    # relatedIdentifiers: union depositor-declared with existing
    dep = depositor_related(attrs.get("relatedIdentifiers"))
    if dep:
        new = _union(dep, d.get("relatedIdentifiers"),
                     lambda r: str(r.get("relatedIdentifier", "")).lower())
        if new != (d.get("relatedIdentifiers") or []):
            d["relatedIdentifiers"] = new
            changed = True

    # subjects: union
    dc_subj = [{"subject": s["subject"]} for s in (attrs.get("subjects") or [])
               if isinstance(s, dict) and s.get("subject")]
    if dc_subj:
        new = _union(dc_subj, d.get("subjects"),
                     lambda s: str(s.get("subject", "")).strip().lower())
        if new != (d.get("subjects") or []):
            d["subjects"] = new
            changed = True

    # summary (Other) description: prefer deposit's when present
    dc_other = next((x.get("description") for x in (attrs.get("descriptions") or [])
                     if x.get("descriptionType") == "Other" and x.get("description")), None)
    if dc_other:
        descs = d.get("descriptions") or []
        replaced = False
        for x in descs:
            if isinstance(x, dict) and x.get("descriptionType") == "Other":
                if x.get("description") != dc_other:
                    x["description"] = dc_other
                    changed = True
                replaced = True
                break
        if not replaced:
            descs.append({"description": dc_other, "descriptionType": "Other"})
            d["descriptions"] = descs
            changed = True

    if align_schema(d):
        changed = True
    return changed


def run(merged_dir, cache_dir, dry_run, limit, show):
    cache = Path(cache_dir)
    stats = {"scanned": 0, "enriched": 0, "no_cache": 0, "notfound": 0, "errors": 0}
    samples = []
    for f in sorted(Path(merged_dir).glob("*_complete.json")):
        if limit and stats["scanned"] >= limit:
            break
        stats["scanned"] += 1
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            stats["errors"] += 1
            continue
        doi = _doi_of(d)
        if not doi:
            continue
        cf = cache / f"{_safe(doi)}.json"
        if not cf.exists():
            stats["no_cache"] += 1
            continue
        try:
            cached = json.loads(cf.read_text(encoding="utf-8"))
        except Exception:
            stats["errors"] += 1
            continue
        if cached.get("_notfound") or cached.get("_error") is not None:
            stats["notfound"] += 1
            continue
        attrs = cached.get("attributes") or {}
        before = json.dumps(d, sort_keys=True) if show else None
        if enrich(d, attrs):
            stats["enriched"] += 1
            if show and len(samples) < show:
                samples.append(f.stem.replace("_complete", ""))
            if not dry_run:
                try:
                    f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
                except Exception as e:
                    logger.error(f"  write: {e}")
                    stats["errors"] += 1
    return stats, samples


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merged-dir", required=True)
    ap.add_argument("--cache-dir", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--show", type=int, default=0)
    args = ap.parse_args()

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    logger.info(f"[{mode}] enrich from DataCite  merged={args.merged_dir}")
    stats, samples = run(args.merged_dir, args.cache_dir, args.dry_run, args.limit, args.show)
    for s in samples:
        logger.info(f"  e.g. enriched {s}")
    for k in ("scanned", "enriched", "no_cache", "notfound", "errors"):
        logger.info(f"  {k:10s} {stats[k]}")


if __name__ == "__main__":
    main()
