"""Lossy affiliation synonym merges and DataCite ROR affiliations."""
import importlib.util
import sys
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
    ]:
        assert not affiliation_merge_is_lossy(variant, canonical, GEO), (variant, canonical)


def test_restore_uses_the_creators_own_source():
    mod = _script("restore_lossy_affiliation_merges")
    syn = {"Library & Researcher Information Services": "Library", "University Library": "Library"}
    lossy = mod.lossy_canonicals(syn, GEO)
    rec = {"creators": [
        {"name": "Cheever, Erin", "affiliation": [{"name": "Library"}]},
        {"name": "Doe, Jane", "affiliation": [{"name": "Library"}]},
    ]}
    people = [("Cheever, Erin", ["Library & Researcher Information Services"]),
              ("Doe, Jane", ["Library"])]          # her source really says "Library"
    assert mod.restore_record(rec, people, lossy, syn, [], None) == 1
    assert rec["creators"][0]["affiliation"] == [{"name": "Library & Researcher Information Services"}]
    assert rec["creators"][1]["affiliation"] == [{"name": "Library"}]
    assert mod.restore_record(rec, people, lossy, syn, [], None) == 0      # idempotent


def test_restore_takes_the_first_of_several_own_variants():
    mod = _script("restore_lossy_affiliation_merges")
    syn = {"Freshwater Laboratory": "Plymouth Marine Laboratory",
           "Freshwater Laboratory, Faskally, Pitlochry": "Plymouth Marine Laboratory"}
    lossy = mod.lossy_canonicals(syn, GEO)
    rec = {"creators": [{"name": "Doe, Jane", "affiliation": [{"name": "Plymouth Marine Laboratory"}]}]}
    people = [("Doe, Jane", ["Freshwater Laboratory", "Freshwater Laboratory, Faskally, Pitlochry"])]
    assert mod.restore_record(rec, people, lossy, syn, [], None) == 1
    assert rec["creators"][0]["affiliation"] == [{"name": "Freshwater Laboratory"}]


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
