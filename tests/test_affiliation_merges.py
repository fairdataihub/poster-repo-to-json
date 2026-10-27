"""Lossy affiliation synonym merges and DataCite ROR affiliations."""
import importlib.util
import json
import os
import sys
import time
import urllib.error
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from poster_to_json.field_normalize import affiliation_merge_is_lossy  # noqa: E402


def _script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "post_processing" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GEO = frozenset({"antwerp", "belgium", "stockholm", "sweden", "united", "kingdom", "los", "angeles",
                 "california"})


def test_lossy_merges_are_flagged():
    for variant, canonical in [
        ("Library & Researcher Information Services", "Library"),
        ("University Library", "Library"),
        ("Department of Microbiology, University of California", "Department of Microbiology"),
        ("The Hockaday School", "The Hotchkiss School"),
        ("Eurofins (France)", "Euro-Funding"),
        ("Key Laboratory of Crop Physiology", "Kay Laboratory of Crop Physiology"),
        ("Wroclaw University", "Wroclaw University of Economics and Business"),
    ]:
        assert affiliation_merge_is_lossy(variant, canonical, GEO), (variant, canonical)


def test_harmless_merges_pass():
    for variant, canonical in [
        ("University of Antwerp, Antwerp, Belgium", "University of Antwerp"),
        ("University of Heidelberg", "Heidelberg University"),
        ("The University of Manchester", "University of Manchester"),
        ("University of California, Los Angeles (UCLA)", "University of California, Los Angeles"),
        ("Wageningen University and Research", "Wageningen University & Research"),
        ("U.S. Environmental Protection Agency", "Environmental Protection Agency"),
        ("KTH Royal Institute of Technology, Stockholm, Sweden", "KTH Royal Institute of Technology"),
        ("Example GmbH, Musterstrasse 12, 10115", "Example GmbH"),
        ("Stanford Univ.", "Stanford University"),
        ("Dept. of Physics, Example Inst.", "Department of Physics, Example Institute"),
        ("University Libraries", "University Library"),
    ]:
        assert not affiliation_merge_is_lossy(variant, canonical, GEO), (variant, canonical)


def test_datacite_ror_affiliation_is_added():
    mod = _script("backfill_datacite_ror_affiliations")
    ror = {"https://ror.org/045n75910": ("UL Research Institutes",
                                         {"ul research institutes", "ulri", "underwriters laboratories"})}
    rec = {"creators": [{"name": "Cheever, Erin",
                         "affiliation": [{"name": "Library & Researcher Information Services"}]}]}
    dc = {"creators": [{"name": "Cheever, Erin", "nameType": "Personal", "affiliation": [
        {"name": "UL (United States)", "affiliationIdentifier": "https://ror.org/045n75910",
         "affiliationIdentifierScheme": "ROR", "schemeUri": "https://ror.org/"}]}]}
    assert mod.enrich_record(rec, dc, ror, [], None) == 1
    affs = rec["creators"][0]["affiliation"]
    assert affs[0] == {"name": "Library & Researcher Information Services"}
    assert affs[1]["name"] == "UL Research Institutes"
    assert affs[1]["affiliationIdentifier"] == "https://ror.org/045n75910"
    assert mod.enrich_record(rec, dc, ror, [], None) == 0                  # idempotent


def test_datacite_ror_id_attaches_to_an_exact_name():
    mod = _script("backfill_datacite_ror_affiliations")
    ror = {"https://ror.org/01aj84f44": ("Aarhus University", {"aarhus university"})}
    rec = {"creators": [{"name": "Hansen, Ida", "affiliation": [{"name": "Aarhus University"}]}]}
    dc = {"creators": [{"name": "Hansen, Ida", "affiliation": [
        {"name": "Aarhus University", "affiliationIdentifier": "01aj84f44",
         "affiliationIdentifierScheme": "ROR"}]}]}
    assert mod.enrich_record(rec, dc, ror, [], None) == 1
    assert rec["creators"][0]["affiliation"] == [{
        "name": "Aarhus University", "affiliationIdentifier": "https://ror.org/01aj84f44",
        "affiliationIdentifierScheme": "ROR", "schemeURI": "https://ror.org"}]


def test_datacite_skips_unmatched_or_organizational_creators():
    mod = _script("backfill_datacite_ror_affiliations")
    ror = {"https://ror.org/045n75910": ("UL Research Institutes", set())}
    rec = {"creators": [{"name": "Cheever, Erin"}]}
    aff = [{"name": "UL", "affiliationIdentifier": "https://ror.org/045n75910",
            "affiliationIdentifierScheme": "ROR"}]
    dc = {"creators": [{"name": "Someone, Else", "affiliation": aff},
                       {"name": "UL Research Institutes", "nameType": "Organizational", "affiliation": aff}]}
    assert mod.enrich_record(rec, dc, ror, [], None) == 0


# --- adversarial review fixes ---------------------------------------------------------

ROR_A, ROR_B, ROR_Z = "https://ror.org/0aaaaaa11", "https://ror.org/0bbbbbb22", "https://ror.org/0zzzzzz99"


def _ror_entry(disp, *aliases, status="active", successors=()):
    """A load_ror_names() value."""
    names = (disp,) + aliases
    return (disp, {" ".join(n.lower().replace(",", " ").split()) for n in names}, status, successors,
            {" ".join(n.casefold().split()) for n in names})


def _aff(name, rid, scheme="ROR"):
    a = {"name": name, "affiliationIdentifier": rid}
    if scheme:
        a["affiliationIdentifierScheme"] = scheme
    return a


def _dc(name, *affs, **kw):
    return dict({"name": name, "affiliation": list(affs)}, **kw)


_ROR_A_FULL = {"affiliationIdentifier": ROR_A, "affiliationIdentifierScheme": "ROR", "schemeURI": "https://ror.org"}


def test_ror_id_normalization():
    mod = _script("backfill_datacite_ror_affiliations")
    for v in ["03yrm5c26", "https://ror.org/03yrm5c26", "http://ror.org/03yrm5c26", "http://ror.org/03yrm5c26/",
              "https://ror.org/03yrm5c26/", "https://ror.org/03YRM5C26", "ror.org/03yrm5c26",
              " https://ROR.org/03yrm5c26", "https://www.ror.org/03yrm5c26"]:
        assert mod.norm_ror_id(v) == "https://ror.org/03yrm5c26", v
    for v in ["", None, "https://ror.org/", "https://ror.org/aaa", "https://example.org/03yrm5c26",
              "grid.1234.5", "https://ror.org/03yrm5c26x"]:
        assert mod.norm_ror_id(v) is None, v


def test_datacite_matches_one_to_one():
    mod = _script("backfill_datacite_ror_affiliations")
    ror = {ROR_A: _ror_entry("Org A"), ROR_B: _ror_entry("Org B")}
    rec = {"creators": [{"name": "Smith, J."}]}
    dc = {"creators": [_dc("Smith, John", _aff("Org A", ROR_A)), _dc("Smith, Jane", _aff("Org B", ROR_B))]}
    stats = Counter()
    assert mod.enrich_record(rec, dc, ror, [], None, stats) == 0
    assert rec == {"creators": [{"name": "Smith, J."}]}
    assert stats["ambiguous_match"] == 2
    # a DataCite person without affiliations still makes the match ambiguous
    rec = {"creators": [{"name": "Smith, J."}]}
    dc = {"creators": [_dc("Smith, John", _aff("Org A", ROR_A)), _dc("Smith, Jane")]}
    assert mod.enrich_record(rec, dc, ror, [], None) == 0
    # two record people for one DataCite person
    rec = {"creators": [{"name": "Smith, John"}, {"name": "Smith, J."}]}
    dc = {"creators": [_dc("Smith, John", _aff("Org A", ROR_A))]}
    assert mod.enrich_record(rec, dc, ror, [], None) == 0
    # unique both ways, one DataCite name given as family/given only
    rec = {"creators": [{"name": "Smith, John"}, {"name": "Doe, Jane"}]}
    dc = {"creators": [{"givenName": "John", "familyName": "Smith", "affiliation": [_aff("Org A", ROR_A)]},
                       _dc("Doe, Jane", _aff("Org B", ROR_B))]}
    assert mod.enrich_record(rec, dc, ror, [], None) == 2
    assert rec["creators"][0]["affiliation"][0]["affiliationIdentifier"] == ROR_A
    assert rec["creators"][1]["affiliation"][0]["affiliationIdentifier"] == ROR_B
    assert mod.enrich_record(rec, dc, ror, [], None) == 0                  # idempotent


def test_datacite_bad_ror_ids_are_counted_not_written():
    mod = _script("backfill_datacite_ror_affiliations")
    ror = {ROR_A: _ror_entry("Org A")}
    rec = {"creators": [{"name": "Smith, John"}]}
    dc = {"creators": [_dc("Smith, John", _aff("Org A", "http://ror.org/0AAAAAA11/"),
                           _aff("Org X", "https://ror.org/"), _aff("Org Y", "https://ror.org/aaa"))]}
    stats = Counter()
    assert mod.enrich_record(rec, dc, ror, [], None, stats) == 1
    assert rec["creators"][0]["affiliation"] == [dict(_ROR_A_FULL, name="Org A")]
    assert stats["bad_ror_id"] == 2


def test_datacite_existing_ids_and_conflicts():
    mod = _script("backfill_datacite_ror_affiliations")
    ror = {ROR_A: _ror_entry("Org A University", "Org A"), ROR_Z: _ror_entry("Zed Institute")}
    dc = {"creators": [_dc("Smith, John", _aff("Org A", ROR_A))]}
    # the same id in any spelling, with or without a scheme field: nothing to add
    for existing in [_aff("Org A", ROR_A, scheme=None), _aff("Org A", "https://ror.org/0aaaaaa11/"),
                     _aff("Something", "http://ror.org/0AAAAAA11", scheme="ror")]:
        rec = {"creators": [{"name": "Smith, John", "affiliation": [dict(existing)]}]}
        assert mod.enrich_record(rec, dc, ror, [], None) == 0, existing
        assert rec["creators"][0]["affiliation"] == [existing]
    # the same name with another id, the name exactly a ROR name of the registrant id: replaced
    rec = {"creators": [{"name": "Smith, John", "affiliation": [_aff("org  A", ROR_Z)]}]}
    assert mod.enrich_record(rec, dc, ror, [], None) == 1
    assert rec["creators"][0]["affiliation"] == [dict(_ROR_A_FULL, name="org  A")]
    assert mod.enrich_record(rec, dc, ror, [], None) == 0                  # idempotent
    # the same name (DataCite's wording only) with another id: conflict, no second entry
    dc2 = {"creators": [_dc("Smith, John", _aff("Org A (Lab)", ROR_A))]}
    rec = {"creators": [{"name": "Smith, John", "affiliation": [_aff("Org A Lab", ROR_Z)]}]}
    stats = Counter()
    assert mod.enrich_record(rec, dc2, ror, [], None, stats) == 0
    assert rec["creators"][0]["affiliation"] == [_aff("Org A Lab", ROR_Z)]
    assert stats["conflict"] == 1
    # an id-less abbreviation of the ROR display name gets the id, no near-duplicate is added
    rec = {"creators": [{"name": "Smith, John", "affiliation": [{"name": "Org A Univ."}]}]}
    stats = Counter()
    assert mod.enrich_record(rec, dc, ror, [], None, stats) == 1
    assert rec["creators"][0]["affiliation"] == [dict(_ROR_A_FULL, name="Org A Univ.")]
    assert stats["id_on_abbreviation"] == 1
    assert mod.enrich_record(rec, dc, ror, [], None) == 0                  # idempotent
    # two such abbreviations: which one is meant is unclear, nothing is added
    rec = {"creators": [{"name": "Smith, John", "affiliation": [{"name": "Org A Univ."}, {"name": "Org A Uni"}]}]}
    stats = Counter()
    assert mod.enrich_record(rec, dc, ror, [], None, stats) == 0
    assert stats["near_duplicate_skipped"] == 1
    assert mod._abbrev_variant("Dept. of Natl. Physics", "Department of National Physics")
    assert not mod._abbrev_variant("Org B Univ.", "Org A University")
    assert not mod._abbrev_variant("Org A", "Org A University")


def test_datacite_contributors_are_enriched():
    mod = _script("backfill_datacite_ror_affiliations")
    ror = {ROR_A: _ror_entry("Org A")}
    rec = {"creators": [{"name": "Doe, Jane"}],
           "contributors": [{"name": "Smith, John", "contributorType": "Editor"}]}
    dc = {"creators": [_dc("Doe, Jane")],
          "contributors": [_dc("Smith, John", _aff("Org A", ROR_A), contributorType="Editor")]}
    assert mod.enrich_record(rec, dc, ror, [], None) == 1
    assert rec["contributors"][0]["affiliation"] == [dict(_ROR_A_FULL, name="Org A")]
    assert "affiliation" not in rec["creators"][0]
    assert mod.enrich_record(rec, dc, ror, [], None) == 0


def test_datacite_skip_counters():
    mod = _script("backfill_datacite_ror_affiliations")
    ror = {ROR_A: _ror_entry("Org A")}
    rec = {"creators": [{"name": "Smith, John"}]}
    grid = {"name": "Org G", "affiliationIdentifier": "grid.1.2", "affiliationIdentifierScheme": "GRID"}
    dc = {"creators": [_dc("Smith, John", {"name": "Org A"}, grid),
                       _dc("Someone, Else", _aff("Org A", ROR_A)),
                       _dc("Org A", _aff("Org A", ROR_A), nameType="Organizational")]}
    stats = Counter()
    assert mod.enrich_record(rec, dc, ror, [], None, stats) == 0
    assert stats["affiliation_name_without_ror"] == 2
    assert stats["datacite_person_no_local_match"] == 1
    assert stats["organizational_skipped"] == 1


def test_datacite_inactive_ror_ids():
    mod = _script("backfill_datacite_ror_affiliations")
    ror = {ROR_A: _ror_entry("Old Org", status="inactive", successors=("https://ror.org/0bbbbbb22/",)),
           ROR_B: _ror_entry("New Org"),
           ROR_Z: _ror_entry("Gone Org", status="withdrawn")}
    rec = {"creators": [{"name": "Smith, John"}]}
    dc = {"creators": [_dc("Smith, John", _aff("Old Org", ROR_A), _aff("Gone Org", ROR_Z))]}
    stats = Counter()
    assert mod.enrich_record(rec, dc, ror, [], None, stats) == 1
    assert rec["creators"][0]["affiliation"] == [{"name": "New Org", "affiliationIdentifier": ROR_B,
                                                  "affiliationIdentifierScheme": "ROR", "schemeURI": "https://ror.org"}]
    assert stats["inactive_ror_successor_used"] == 1 and stats["inactive_ror_skipped"] == 1
    assert mod.enrich_record(rec, dc, ror, [], None) == 0                  # idempotent


def test_load_ror_names_reads_status_and_successors(tmp_path):
    mod = _script("backfill_datacite_ror_affiliations")
    p = tmp_path / "ror.json"
    p.write_text(json.dumps([
        {"id": "https://ror.org/0aaaaaa11", "status": "inactive",
         "names": [{"value": "Old Org", "types": ["ror_display", "label"]}, {"value": "OO", "types": ["acronym"]}],
         "relationships": [{"type": "successor", "id": "https://ror.org/0bbbbbb22", "label": "New Org"}]},
        {"id": "https://ror.org/0bbbbbb22", "status": "active",
         "names": [{"value": "New Org", "types": ["ror_display"]}]}]), encoding="utf-8")
    ror = mod.load_ror_names(p)
    assert ror[ROR_A][0] == "Old Org" and ror[ROR_A][2] == "inactive"
    assert ror[ROR_A][3] == ("https://ror.org/0bbbbbb22",) and "oo" in ror[ROR_A][4]
    assert ror[ROR_B][2] == "active"


def test_datacite_404_cache_expires(tmp_path, monkeypatch):
    mod = _script("backfill_datacite_ror_affiliations")
    calls = []

    def not_found(req, timeout=None):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
    monkeypatch.setattr(mod.urllib.request, "urlopen", not_found)
    monkeypatch.setattr(mod._LIMIT, "interval", 0)
    p = tmp_path / "10.1_x.json"
    assert mod.fetch("10.1/x", tmp_path) is None and len(calls) == 1
    marker = json.loads(p.read_text(encoding="utf-8"))
    assert marker["_missing"] is True and marker["_checked_at"] > 0
    assert mod.fetch("10.1/x", tmp_path) is None and len(calls) == 1      # fresh 404: from cache
    p.write_text(json.dumps({"_missing": True, "_checked_at": time.time() - 31 * 86400}), encoding="utf-8")
    assert mod._cache_state(p, 30)[0] == "stale"
    assert mod.fetch("10.1/x", tmp_path, 30, network=False) is None and len(calls) == 1
    assert mod.fetch("10.1/x", tmp_path, 30) is None and len(calls) == 2  # asked again
    p.write_text("{}", encoding="utf-8")                                   # legacy marker, dated by mtime
    assert mod._cache_state(p, 30)[0] == "missing"
    old = time.time() - 40 * 86400
    os.utime(p, (old, old))
    assert mod._cache_state(p, 30)[0] == "stale"
    p.write_text(json.dumps({"creators": []}), encoding="utf-8")
    assert mod._cache_state(p, 30) == ("hit", {"creators": []})


def test_datacite_default_rate_stays_under_10_per_second():
    mod = _script("backfill_datacite_ror_affiliations")
    assert mod._LIMIT.interval >= 0.1
    lim = mod._RateLimit(0.05)
    t0 = time.monotonic()
    with ThreadPoolExecutor(6) as ex:
        list(ex.map(lambda _: lim.wait(), range(7)))
    assert time.monotonic() - t0 >= 0.29                                   # 6 gaps of 0.05 s


def test_identity_rule_uses_ror_names(tmp_path):
    # exact ROR names of different organizations differ whatever their words; the
    # fairly rule alone lets word order pass
    from poster_to_json.field_normalize import load_identity_rule
    dump = [
        {"id": "https://ror.org/01yc7t268", "names": [{"value": "Washington University in St. Louis", "types": ["ror_display"]},
                                                      {"value": "Washington University", "types": ["alias"]},
                                                      {"value": "WUSTL", "types": ["acronym"]}],
         "locations": [{"geonames_details": {"name": "St Louis", "country_name": "United States",
                                             "country_subdivision_name": "Missouri"}}]},
        {"id": "https://ror.org/00cvxb145", "names": [{"value": "University of Washington", "types": ["ror_display"]},
                                                      {"value": "UW", "types": ["acronym"]}],
         "locations": [{"geonames_details": {"name": "Seattle", "country_name": "United States",
                                             "country_subdivision_name": "Washington"}}]},
    ]
    path = tmp_path / "ror.json"
    path.write_text(json.dumps(dump), encoding="utf-8")
    rule = load_identity_rule(str(path))
    assert affiliation_merge_is_lossy("Washington University", "University of Washington", rule)
    assert affiliation_merge_is_lossy("The Washington University", "university of washington", rule)
    assert not affiliation_merge_is_lossy("University of Washington", "The University of Washington", rule)
    assert "uw" not in rule.ror_names                  # short acronyms are not names here
    assert affiliation_merge_is_lossy("Library & Researcher Information Services", "Library", rule)


# --- restore: snapshot evidence -------------------------------------------------------

def _snap(name, *affs, pos=0):
    return (name, [a if isinstance(a, tuple) else (a, None) for a in affs], "snapshot", pos)


def test_restore_puts_back_the_pre_merge_wording():
    mod = _script("restore_lossy_affiliation_merges")
    syn = {"Library & Researcher Information Services": "Library", "University Library": "Library"}
    rec = {"creators": [{"name": "Cheever, Erin", "affiliation": [{"name": "Library"}]},
                        {"name": "Doe, Jane", "affiliation": [{"name": "Library"}]}]}
    people = [_snap("Cheever, Erin", "Library & Researcher Information Services", pos=0),
              _snap("Doe, Jane", "Library", pos=1)]                 # her pre-merge wording was "Library"
    stats = Counter()
    assert mod.restore_record(rec, people, GEO, syn, [], None, None, stats) == 1
    assert rec["creators"][0]["affiliation"] == [{"name": "Library & Researcher Information Services"}]
    assert rec["creators"][1]["affiliation"] == [{"name": "Library"}]
    assert mod.restore_record(rec, people, GEO, syn, [], None) == 0        # idempotent


def test_restore_needs_snapshot_evidence():
    mod = _script("restore_lossy_affiliation_merges")
    syn = {"Library & Researcher Information Services": "Library"}
    rec = {"creators": [{"name": "Cheever, Erin", "affiliation": [{"name": "Library"}]}]}
    stats = Counter()
    # only an extraction says the long name: no evidence the record was renamed
    people = [("Cheever, Erin", [("Library & Researcher Information Services", None)], "extraction:x", 0)]
    assert mod.restore_record(rec, people, GEO, syn, [], None, None, stats) == 0
    assert stats["no_snapshot_person"] == 1
    # a source (e.g. a newer extraction) says the current name: kept
    people = [_snap("Cheever, Erin", "Library & Researcher Information Services"),
              ("Cheever, Erin", [("Library", None)], "extraction:new", 0)]
    assert mod.restore_record(rec, people, GEO, syn, [], None, None, stats) == 0
    assert rec["creators"][0]["affiliation"] == [{"name": "Library"}]


def test_restore_keeps_harmless_renames_and_counts_unexplained():
    mod = _script("restore_lossy_affiliation_merges")
    syn = {"The University of Manchester": "University of Manchester"}
    stats = Counter()
    rec = {"creators": [{"name": "Doe, Jane", "affiliation": [{"name": "University of Manchester"}]}]}
    assert mod.restore_record(rec, [_snap("Doe, Jane", "The University of Manchester")], GEO, syn, [], None, None,
                              stats) == 0
    assert stats["harmless_rename_kept"] == 1
    rec = {"creators": [{"name": "Doe, Jane", "affiliation": [{"name": "Somewhere"}]}]}
    assert mod.restore_record(rec, [_snap("Doe, Jane", "A", "B")], GEO, {}, [], None, None, stats) == 0
    assert stats["unexplained"] == 1                                       # two candidates, no map pairing


def test_restore_ror_ids():
    mod = _script("restore_lossy_affiliation_merges")
    syn = {"The Hockaday School": "The Hotchkiss School"}
    rec = lambda: {"creators": [{"name": "Doe, Jane", "affiliation": [dict(_ROR_A_FULL, name="The Hotchkiss School")]}]}
    ror = {ROR_A: _ror_entry("The Hotchkiss School")}
    # the snapshot entry's own id wins
    r, stats = rec(), Counter()
    assert mod.restore_record(r, [_snap("Doe, Jane", ("The Hockaday School", ROR_B))], GEO, syn, [], None, ror, stats) == 1
    assert r["creators"][0]["affiliation"] == [{"name": "The Hockaday School", "affiliationIdentifier": ROR_B,
                                                "affiliationIdentifierScheme": "ROR", "schemeURI": "https://ror.org"}]
    assert stats["ror_id_from_snapshot"] == 1
    # no snapshot id: the canonical's id goes, unless the restored name is one of its ROR names
    r, stats = rec(), Counter()
    assert mod.restore_record(r, [_snap("Doe, Jane", "The Hockaday School")], GEO, syn, [], None, ror, stats) == 1
    assert r["creators"][0]["affiliation"] == [{"name": "The Hockaday School"}] and stats["ror_id_dropped"] == 1
    r, stats = rec(), Counter()
    ror2 = {ROR_A: _ror_entry("The Hotchkiss School", "the  hockaday SCHOOL")}
    assert mod.restore_record(r, [_snap("Doe, Jane", "The Hockaday School")], GEO, syn, [], None, ror2, stats) == 1
    assert r["creators"][0]["affiliation"][0]["affiliationIdentifier"] == ROR_A and stats["ror_id_kept"] == 1


def test_restore_name_kept_with_wrong_ror_name():
    # the map renamed New Mexico State to New Mexico and kept NMSU's id: put the name back
    mod = _script("restore_lossy_affiliation_merges")
    nmsu = "https://ror.org/00hpz7z43"
    syn = {"New Mexico State University": "University of New Mexico"}
    rec = {"creators": [{"name": "Doe, Jane", "affiliation": [{"name": "University of New Mexico", "affiliationIdentifier": nmsu,
                                                               "affiliationIdentifierScheme": "ROR", "schemeURI": "https://ror.org"}]}]}
    people = [_snap("Doe, Jane", ("New Mexico State University", nmsu))]
    assert mod.restore_record(rec, people, frozenset({"mexico"}), syn, [], None) == 1
    assert rec["creators"][0]["affiliation"][0]["name"] == "New Mexico State University"
    assert rec["creators"][0]["affiliation"][0]["affiliationIdentifier"] == nmsu


def test_restore_snapshot_person_must_be_unambiguous():
    mod = _script("restore_lossy_affiliation_merges")
    syn = {"Library & Researcher Information Services": "Library", "Smith Lab, Dept of Physics": "Library"}
    stats = Counter()
    rec = {"creators": [{"name": "Smith, J.", "affiliation": [{"name": "Library"}]}]}
    people = [_snap("Smith, John", "Smith Lab, Dept of Physics", pos=1), _snap("Smith, Jane", "Library & Researcher Information Services", pos=2)]
    assert mod.restore_record(rec, people, GEO, syn, [], None, None, stats) == 0
    assert stats["no_snapshot_person"] == 1
    # the person at the creator's own position breaks a tie
    rec = {"creators": [{"name": "Smith, J.", "affiliation": [{"name": "Library"}]},
                        {"name": "Smith, J.", "affiliation": [{"name": "Library"}]}]}
    people = [_snap("Smith, J.", "Smith Lab, Dept of Physics", pos=0),
              _snap("Smith, J.", "Library & Researcher Information Services", pos=1)]
    assert mod.restore_record(rec, people, GEO, syn, [], None) == 2
    assert [c["affiliation"][0]["name"] for c in rec["creators"]] == [
        "Smith Lab, Dept of Physics", "Library & Researcher Information Services"]



def test_restore_pairs_only_on_evidence():
    mod = _script("restore_lossy_affiliation_merges")
    stats = Counter()
    # an affiliation added after the snapshot (DataCite, end of the list) is never paired with
    # a snapshot wording that a harmless rename left behind
    rec = {"creators": [{"name": "Doe, Jane", "affiliation": [{"name": "University of Manchester"},
                                                              _aff("UL Research Institutes", ROR_A)]}]}
    syn = {"The University of Manchester": "University of Manchester"}
    assert mod.restore_record(rec, [_snap("Doe, Jane", "The University of Manchester")], GEO, syn, [], None, None,
                              stats) == 0
    assert rec["creators"][0]["affiliation"][1]["name"] == "UL Research Institutes"
    # same position of an equally long list: a rename the map on disk does not know
    rec = {"creators": [{"name": "Doe, Jane", "affiliation": [{"name": "Institute for Nuclear Research"}]}]}
    assert mod.restore_record(rec, [_snap("Doe, Jane", "National Centre for Nuclear Research")], GEO, {}, [], None) == 1
    assert rec["creators"][0]["affiliation"][0]["name"] == "National Centre for Nuclear Research"
    # same ROR id, list length changed
    rec = {"creators": [{"name": "Doe, Jane", "affiliation": [_aff("University of New Mexico", ROR_A), {"name": "X Lab"}]}]}
    people = [_snap("Doe, Jane", ("New Mexico State University", ROR_A))]
    assert mod.restore_record(rec, people, frozenset({"mexico"}), {}, [], None) == 1
    assert rec["creators"][0]["affiliation"][0]["name"] == "New Mexico State University"



def test_datacite_two_ids_of_one_person_do_not_swap():
    mod = _script("backfill_datacite_ror_affiliations")
    ror = {ROR_A: _ror_entry("Org A"), ROR_B: _ror_entry("Org A Institute", "Org A")}
    dc = {"creators": [_dc("Smith, John", _aff("Org A", ROR_A), _aff("Org A", ROR_B))]}
    rec = {"creators": [{"name": "Smith, John"}]}
    first = mod.enrich_record(rec, dc, ror, [], None)
    ids = sorted(a["affiliationIdentifier"] for a in rec["creators"][0]["affiliation"])
    assert first >= 1 and ROR_A in ids
    assert mod.enrich_record(rec, dc, ror, [], None) == 0                  # stable on the next pass



def test_datacite_matches_flipped_surnames_and_listed_twice():
    mod = _script("backfill_datacite_ror_affiliations")
    ror = {ROR_A: _ror_entry("Org A")}
    dc = {"creators": [_dc("Correll Trnka, Anika", _aff("Org A", ROR_A))]}
    rec = {"creators": [{"name": "Trnka, Anika Correll"}]}
    assert mod.enrich_record(rec, dc, ror, [], None) == 1
    dc2 = {"creators": [_dc("Irving-Bell, Dawne", _aff("Org A", ROR_A))]}
    rec2 = {"creators": [{"name": "Irving-Bell, Dawne"}, {"name": "Irving-Bell, Dawne"}]}
    stats = Counter()
    assert mod.enrich_record(rec2, dc2, ror, [], None, stats) == 2
    assert stats["duplicate_local_person"] == 1



def test_restore_leaves_an_entry_named_as_its_own_ror_id_unless_the_map_explains_it():
    mod = _script("restore_lossy_affiliation_merges")
    ror = {ROR_A: _ror_entry("UL Research Institutes")}
    # equal-length list, an entry named exactly as its ROR id: no evidence of a rename
    rec = {"creators": [{"name": "Doe, Jane", "affiliation": [_aff("UL Research Institutes", ROR_A)]}]}
    assert mod.restore_record(rec, [_snap("Doe, Jane", "Something Else")], GEO, {}, [], None, ror) == 0
    # the map itself says it was a merge: restored
    syn = {"Something Else Entirely": "UL Research Institutes"}
    assert mod.restore_record(rec, [_snap("Doe, Jane", "Something Else Entirely")], GEO, syn, [], None, ror) == 1



def test_restore_never_puts_back_junk():
    mod = _script("restore_lossy_affiliation_merges")
    rec = {"creators": [{"name": "Doe, Jane", "affiliation": [{"name": "Somewhere Institute"}]}]}
    stats = Counter()
    assert mod.restore_record(rec, [_snap("Doe, Jane", "1")], GEO, {}, [], None, None, stats) == 0
    assert stats["unexplained"] == 1
