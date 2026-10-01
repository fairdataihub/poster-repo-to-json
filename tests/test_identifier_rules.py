"""Identifier rules from the 2026-09-30 DOI review.

Each case mirrors a real record from that review: a poster whose depositor
linked it to itself (1292418), the CropTiPS collapsed family (5483821), a record
with extraction DOIs left in identifiers (10279754), a depositor relation copied
into identifiers (10194), and two Zenodo deposits sharing a borrowed DOI.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:
    import tqdm  # noqa: F401
except ImportError:
    import types
    m = types.ModuleType("tqdm")
    m.tqdm = lambda x, **k: x
    sys.modules["tqdm"] = m

from poster_to_json.field_normalize import align_schema, own_doi, repair_identifiers  # noqa: E402
from poster_to_json import version_linking as vl  # noqa: E402


def ids(record):
    return [i["identifier"] for i in record.get("identifiers", [])]


def rels(record):
    return [r["relatedIdentifier"] for r in record.get("relatedIdentifiers", [])]


# --- align_schema: the pipeline rule -------------------------------------------

def test_self_relation_is_dropped_and_own_doi_kept():
    record = {
        "identifiers": [{"identifier": "10.5281/zenodo.1292418", "identifierType": "DOI"},
                        {"identifier": "1292418", "identifierType": "Other"}],
        "relatedIdentifiers": [
            {"relatedIdentifier": "10.5281/zenodo.1292418", "relatedIdentifierType": "DOI",
             "relationType": "IsCitedBy"},
            {"relatedIdentifier": "https://arxiv.org/abs/1808.10760", "relatedIdentifierType": "URL",
             "relationType": "References"},
        ],
    }
    align_schema(record)
    assert ids(record)[0] == "10.5281/zenodo.1292418"
    assert rels(record) == ["https://arxiv.org/abs/1808.10760"]


def test_self_relation_matches_through_a_doi_org_url():
    record = {
        "identifiers": [{"identifier": "10.5281/zenodo.5", "identifierType": "DOI"}],
        "relatedIdentifiers": [{"relatedIdentifier": "https://doi.org/10.5281/ZENODO.5",
                                "relatedIdentifierType": "URL", "relationType": "IsIdenticalTo"}],
    }
    align_schema(record)
    assert ids(record) == ["10.5281/zenodo.5"]
    assert "relatedIdentifiers" not in record


def test_leaked_reference_doi_still_leaves_identifiers():
    record = {
        "identifiers": [{"identifier": "10.5281/zenodo.10194", "identifierType": "DOI"},
                        {"identifier": "10.2481/dsj.OSOM13-043", "identifierType": "DOI"}],
        "relatedIdentifiers": [{"relatedIdentifier": "10.2481/dsj.OSOM13-043",
                                "relatedIdentifierType": "DOI", "relationType": "Cites"}],
    }
    align_schema(record)
    assert ids(record) == ["10.5281/zenodo.10194"]
    assert rels(record) == ["10.2481/dsj.OSOM13-043"]


def test_own_doi_is_first_doi():
    assert own_doi({"identifiers": [{"identifier": "1", "identifierType": "Other"},
                                    {"identifier": "10.1/x", "identifierType": "DOI"}]}) == "10.1/x"
    assert own_doi({"identifiers": []}) is None


# --- repair_identifiers: the one-time blob repair -------------------------------

def test_repair_restores_a_lost_doi_and_drops_the_self_relation():
    record = {
        "identifiers": [{"identifier": "1292418", "identifierType": "Other"}],
        "relatedIdentifiers": [
            {"relatedIdentifier": "10.5281/zenodo.1292418", "relatedIdentifierType": "DOI",
             "relationType": "IsCitedBy"},
            {"relatedIdentifier": "https://arxiv.org/abs/1808.10760", "relatedIdentifierType": "URL",
             "relationType": "References"},
        ],
    }
    out = repair_identifiers(record, "10.5281/zenodo.1292418", "1292418")
    assert out["restored"] and out["self_relations"] == 1 and out["removed_dois"] == []
    assert ids(record) == ["10.5281/zenodo.1292418", "1292418"]
    assert rels(record) == ["https://arxiv.org/abs/1808.10760"]


def test_repair_keeps_the_croptips_collapse_shape():
    record = {"identifiers": [
        {"identifier": "10.25909/5483821.v3", "identifierType": "DOI"},
        {"identifier": "10.4225/55/59e3f09ff1a4d", "identifierType": "DOI"},
        {"identifier": "10.25909/5483821", "identifierType": "DOI"},
        {"identifier": "5483821", "identifierType": "Other"}]}
    before = ids(record)
    out = repair_identifiers(record, "10.4225/55/59e3f09ff1a4d", "5483821")
    assert out == {"restored": False, "self_relations": 0, "removed_dois": []}
    assert ids(record) == before


def test_repair_removes_extraction_dois_without_adding_relations():
    record = {"identifiers": [
        {"identifier": "10.26180/5dc7a0713dd4f", "identifierType": "DOI"},
        {"identifier": "10.1145/2858036.2858409", "identifierType": "DOI"},
        {"identifier": "10.5281/ZENODO.7941533", "identifierType": "DOI"},
        {"identifier": "10279754", "identifierType": "Other"}]}
    out = repair_identifiers(record, "10.26180/5dc7a0713dd4f", "10279754")
    assert out["removed_dois"] == ["10.1145/2858036.2858409", "10.5281/ZENODO.7941533"]
    assert ids(record) == ["10.26180/5dc7a0713dd4f", "10279754"]
    assert "relatedIdentifiers" not in record


def test_record_id_match_is_whole_number_only():
    # 1234 must not count as part of 10.5281/zenodo.12345
    record = {"identifiers": [{"identifier": "10.5281/zenodo.1234", "identifierType": "DOI"},
                              {"identifier": "10.5281/zenodo.12345", "identifierType": "DOI"}]}
    out = repair_identifiers(record, "10.5281/zenodo.1234", "1234")
    assert out["removed_dois"] == ["10.5281/zenodo.12345"]


def test_repair_is_idempotent():
    record = {"identifiers": [{"identifier": "1292418", "identifierType": "Other"}],
              "relatedIdentifiers": [{"relatedIdentifier": "10.5281/zenodo.1292418",
                                      "relatedIdentifierType": "DOI", "relationType": "IsCitedBy"}]}
    repair_identifiers(record, "10.5281/zenodo.1292418", "1292418")
    assert repair_identifiers(record, "10.5281/zenodo.1292418", "1292418") == \
        {"restored": False, "self_relations": 0, "removed_dois": []}


# --- duplicate deposits sharing a borrowed DOI -----------------------------------

RG = "10.13140/rg.2.2.21495.53926"


def test_zenodo_only_duplicates_keep_the_newest_deposit():
    borrowed = {"14837147": RG, "14947868": RG.upper()}
    owners = {RG: [("zenodo", "14837147"), ("zenodo", "14947868")]}
    assert vl.find_borrowed_doi_copies(borrowed, owners) == {"14837147": RG}


def test_single_zenodo_borrower_is_left_alone():
    assert vl.find_borrowed_doi_copies({"2528673": "10.13140/rg.2.2.20714.36805"}, {}) == {}


def test_copy_of_another_repositorys_record_still_dropped():
    borrowed = {"1196536": "10.6084/m9.figshare.5467180.v3"}
    owners = {"10.6084/m9.figshare.5467180.v3": [("figshare", "5467180")]}
    assert vl.find_borrowed_doi_copies(borrowed, owners) == {
        "1196536": "10.6084/m9.figshare.5467180.v3"}
