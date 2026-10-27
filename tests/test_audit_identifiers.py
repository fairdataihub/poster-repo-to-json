"""audit_identifiers.py: ids that name another organization than their text."""
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _mod():
    spec = importlib.util.spec_from_file_location("audit_identifiers", ROOT / "scripts" / "post_processing" / "audit_identifiers.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _org(rid, *names, display=None, fundref=None, status="active", place="London", country="United Kingdom"):
    org = {"id": f"https://ror.org/{rid}", "status": status,
           "names": [{"value": display or names[0], "types": ["ror_display"]}]
           + [{"value": n, "types": ["alias"]} for n in names if n != (display or names[0])],
           "locations": [{"geonames_details": {"name": place, "country_name": country}}]}
    if fundref:
        org["external_ids"] = [{"type": "fundref", "all": [fundref], "preferred": fundref}]
    return org


UOL, UCL, ADNET, NMSU = "04cw6st05", "02jx3x895", "05we1n045", "00hpz7z43"


def _ror(tmp_path):
    dump = [_org(UOL, "University of London"), _org(UCL, "University College London", "UCL"),
            _org(ADNET, "Adnet Systems (United States)", place="Bethesda", country="United States"),
            _org(NMSU, "New Mexico State University", place="Las Cruces", country="United States"),
            _org("0aaaaaa11", "Example Funding Agency", fundref="501100000001")]
    p = tmp_path / "ror.json"
    p.write_text(json.dumps(dump), encoding="utf-8")
    ror = _mod().Ror(str(p))
    # word statistics of a five-organization dump are meaningless (every word would be
    # generic): use the identity rule's built-in generic words and these places
    from poster_to_json.field_normalize import IdentityRule
    ror.rule = IdentityRule(frozenset({"united", "states", "kingdom", "london", "mexico"}), None, ror.rule.ror_names)
    return ror


def _aff(name, rid):
    return {"name": name, "affiliationIdentifier": f"https://ror.org/{rid}", "affiliationIdentifierScheme": "ROR",
            "schemeURI": "https://ror.org"}


def test_classify(tmp_path):
    ror = _ror(tmp_path)
    assert ror.classify("University of London", UOL) == "consistent"
    assert ror.classify("Dept. of History, University of London", UOL) == "contains"
    assert ror.classify("ADNET Systems Inc.", ADNET) == "same_organization"
    assert ror.classify("University College London", UOL) == "mismatch"
    assert ror.classify("University of New Mexico", NMSU) == "mismatch"


def test_fix_replaces_with_the_exact_match_or_removes(tmp_path):
    mod, ror = _mod(), _ror(tmp_path)
    d = {"creators": [{"name": "Doe, Jane", "affiliation": [_aff("University College London", UOL),
                                                            _aff("University of New Mexico", NMSU),
                                                            _aff("ADNET Systems Inc.", ADNET)]}]}
    stats, rows = Counter(), []
    assert mod.audit_record(d, ror, stats, rows, "x", fix=True) == 2
    affs = d["creators"][0]["affiliation"]
    assert affs[0] == _aff("University College London", UCL)          # the exact ROR match
    assert affs[1] == {"name": "University of New Mexico"}            # no exact match: id removed
    assert affs[2] == _aff("ADNET Systems Inc.", ADNET)                # same organization: kept
    assert mod.audit_record(d, ror, Counter(), [], "x", fix=True) == 0  # idempotent


def test_funders(tmp_path):
    mod, ror = _mod(), _ror(tmp_path)
    d = {"fundingReferences": [
        {"funderName": "Unknown Funder", "funderIdentifier": "https://doi.org/10.13039/501100000001",
         "funderIdentifierType": "Crossref Funder ID"},
        {"funderName": "University of London", "funderIdentifier": "https://doi.org/10.13039/501100000001",
         "funderIdentifierType": "Crossref Funder ID"}]}
    stats = Counter()
    assert mod.audit_record(d, ror, stats, [], "x", fix=True) == 2
    assert d["fundingReferences"][0]["funderName"] == "Example Funding Agency"
    # the name exactly names another ROR organization, which has no fundref id: a ROR id
    assert d["fundingReferences"][1] == {"funderName": "University of London", "funderIdentifier": f"https://ror.org/{UOL}",
                                         "funderIdentifierType": "ROR", "schemeUri": "https://ror.org/"}
    assert stats["funder_placeholder_name"] == 1 and stats["funder_fixed_replaced"] == 1



def test_fix_merges_entries_a_removal_makes_identical(tmp_path):
    mod, ror = _mod(), _ror(tmp_path)
    d = {"creators": [{"name": "Doe, Jane", "affiliation": [_aff("University of New Mexico", NMSU),
                                                            _aff("University of New Mexico", UOL)]}]}
    stats = Counter()
    mod.audit_record(d, ror, stats, [], "x", fix=True)
    assert d["creators"][0]["affiliation"] == [{"name": "University of New Mexico"}]
    assert stats["aff_duplicates_removed"] == 1



def test_registrant_ids_are_never_changed(tmp_path):
    mod, ror = _mod(), _ror(tmp_path)
    d = {"creators": [{"name": "Doe, Jane", "affiliation": [_aff("University of New Mexico", NMSU)]}]}
    stats = Counter()
    assert mod.audit_record(d, ror, stats, [], "x", fix=True, registered={NMSU}) == 0
    assert d["creators"][0]["affiliation"] == [_aff("University of New Mexico", NMSU)]
    assert stats["aff_registrant_id_kept"] == 1
