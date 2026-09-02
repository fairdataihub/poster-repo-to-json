"""Tests for repository version family detection and linking.

The Zenodo fixtures mirror live API responses for concept 10572542, which holds
three versions (index 0, 1, 2) of which our harvest only ever saw two. That gap
is the case the module exists to get right.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:
    import tqdm  # noqa: F401
except ImportError:
    import types
    m = types.ModuleType("tqdm")
    m.tqdm = lambda x, **k: x
    sys.modules["tqdm"] = m

from poster_to_json import version_linking as vl


# --- fixtures mirroring live API shapes -------------------------------------

def zenodo_record(rec_id, doi, index, is_last, concept_doi="10.5281/zenodo.10572542",
                  concept_recid="10572542"):
    return {
        "id": rec_id,
        "doi": doi,
        "conceptrecid": concept_recid,
        "conceptdoi": concept_doi,
        "metadata": {
            "relations": {
                "version": [
                    {"index": index, "is_last": is_last,
                     "parent": {"pid_type": "recid", "pid_value": concept_recid}}
                ]
            }
        },
    }


def figshare_record(article_id, version):
    return {
        "id": article_id,
        "doi": f"10.6084/m9.figshare.{article_id}.v{version}",
        "version": version,
        "defined_type": 4,
    }


# --- Zenodo ------------------------------------------------------------------

def test_zenodo_sequence_is_repository_index_plus_one():
    f = vl.from_zenodo(zenodo_record(17435084, "10.5281/zenodo.17435084", 2, True))
    assert f.sequence == 3
    assert f.is_latest is True
    assert f.root == "10.5281/zenodo.10572542"
    assert f.root_type == "DOI"
    assert f.source == "zenodo-relations"


def test_zenodo_not_latest_is_honoured():
    f = vl.from_zenodo(zenodo_record(13168995, "10.5281/zenodo.13168995", 1, False))
    assert f.sequence == 2
    assert f.is_latest is False


def test_zenodo_gap_in_harvest_does_not_renumber():
    """Harvesting index 1 and 2 must not present them as versions 1 and 2."""
    items = [
        {"family": vl.from_zenodo(zenodo_record(13168995, "10.5281/zenodo.13168995", 1, False)),
         "poster_json": {}},
        {"family": vl.from_zenodo(zenodo_record(17435084, "10.5281/zenodo.17435084", 2, True)),
         "poster_json": {}},
    ]
    vl.link_families(items)
    sequences = [i["poster_json"]["versionInfo"]["versionSequence"] for i in items]
    assert sequences == [2, 3]


def test_zenodo_is_last_false_survives_when_newer_version_unharvested():
    """Only version we hold, but Zenodo says something newer exists."""
    item = {"family": vl.from_zenodo(
        zenodo_record(13168995, "10.5281/zenodo.13168995", 1, False)),
        "poster_json": {}}
    vl.link_families([item])
    info = item["poster_json"]["versionInfo"]
    assert info["isLatestVersion"] is False
    assert info["versionCount"] == 1


def test_zenodo_without_relations_falls_back_to_concept():
    rec = {"id": 1, "doi": "10.5281/zenodo.1", "conceptrecid": "999",
           "conceptdoi": "10.5281/zenodo.999", "metadata": {}}
    f = vl.from_zenodo(rec)
    assert f.sequence == 1
    assert f.is_latest is True
    assert f.source == "zenodo-concept"


def test_zenodo_without_concept_doi_uses_recid_key():
    rec = {"id": 1, "doi": "10.5281/zenodo.1", "conceptrecid": "999", "metadata": {}}
    f = vl.from_zenodo(rec)
    assert f.root == "zenodo:recid:999"
    assert f.root_type == "Other"


def test_zenodo_no_signal_returns_none():
    assert vl.from_zenodo({"id": 1, "doi": "10.5281/zenodo.1", "metadata": {}}) is None
    assert vl.from_zenodo(None) is None


# --- Figshare ----------------------------------------------------------------

def test_figshare_uses_article_id_and_version_int():
    f = vl.from_figshare(figshare_record(21359946, 1))
    assert f.root == "figshare:article:21359946"
    assert f.sequence == 1
    assert f.own_doi == "10.6084/m9.figshare.21359946.v1"
    assert f.source == "figshare-version"


def test_figshare_falls_back_to_vn_suffix():
    rec = {"id": 555, "doi": "10.6084/m9.figshare.555.v4", "defined_type": 4}
    f = vl.from_figshare(rec)
    assert f.sequence == 4
    assert f.source == "figshare-doi"


def test_stale_is_last_is_overridden_by_a_higher_harvested_sequence():
    """Zenodo's is_last is only true as of harvest time.

    A record harvested while it was the newest keeps claiming to be latest. If
    the corpus later picks up its successor, holding a higher sequence in the
    same family proves the flag went stale.
    """
    items = [
        # Harvested in 2023, when it really was the last version.
        {"family": vl.from_zenodo(zenodo_record(7853235, "10.5281/zenodo.7853235", 0, True,
                                                concept_doi="10.5281/zenodo.7853234",
                                                concept_recid="7853234")),
         "poster_json": {}},
        # Harvested later. The 2023 snapshot was never refreshed.
        {"family": vl.from_zenodo(zenodo_record(17169582, "10.5281/zenodo.17169582", 1, True,
                                                concept_doi="10.5281/zenodo.7853234",
                                                concept_recid="7853234")),
         "poster_json": {}},
    ]
    stats = vl.link_families(items)
    flags = [i["poster_json"]["versionInfo"]["isLatestVersion"] for i in items]
    assert flags == [False, True]
    assert stats["stale_latest_corrected"] == 1


def test_highest_sequence_keeps_the_repository_answer():
    """is_last False on the newest record we hold means a newer one exists upstream."""
    items = [
        {"family": vl.from_zenodo(zenodo_record(1, "10.5281/zenodo.1", 0, True)),
         "poster_json": {}},
        {"family": vl.from_zenodo(zenodo_record(2, "10.5281/zenodo.2", 1, False)),
         "poster_json": {}},
    ]
    vl.link_families(items)
    flags = [i["poster_json"]["versionInfo"]["isLatestVersion"] for i in items]
    assert flags == [False, False]


def test_same_record_in_two_corpus_slices_is_not_two_versions():
    """A re-ingested poster has the same DOI in two harvest batches.

    Those files are the same version, not siblings. They must not cross-link to
    themselves or inflate versionCount, but both files still get annotated.
    """
    shared = "10.6084/m9.figshare.15087663.v1"
    a, b = {}, {}
    items = [
        {"family": vl.from_figshare(figshare_record(15087663, 1)), "poster_json": a},
        {"family": vl.from_figshare(figshare_record(15087663, 1)), "poster_json": b},
    ]
    stats = vl.link_families(items)
    assert stats["duplicate_files"] == 1
    assert stats["multi_version_families"] == 0
    # One version only, so nothing to link and no self-reference.
    assert a == {} and b == {}
    assert items[0]["family"].own_doi == shared


def test_duplicate_files_annotated_but_counted_once():
    """Two slices hold v1; one slice holds v2. versionCount is 2, not 3."""
    v1a, v1b, v2 = {}, {}, {}
    items = [
        {"family": vl.from_figshare(figshare_record(100, 1)), "poster_json": v1a},
        {"family": vl.from_figshare(figshare_record(100, 1)), "poster_json": v1b},
        {"family": vl.from_figshare(figshare_record(100, 2)), "poster_json": v2},
    ]
    stats = vl.link_families(items)
    assert stats["duplicate_files"] == 1
    assert stats["multi_version_families"] == 1
    for pj in (v1a, v1b):
        assert pj["versionInfo"]["versionCount"] == 2
        assert pj["versionInfo"]["versionSequence"] == 1
        assert pj["versionInfo"]["isLatestVersion"] is False
        forward = [r["relatedIdentifier"] for r in pj["relatedIdentifiers"]
                   if r["relationType"] == "IsPreviousVersionOf"]
        assert forward == ["10.6084/m9.figshare.100.v2"]
    assert v2["versionInfo"]["versionCount"] == 2
    # The newer record points back at v1 once, not twice.
    back = [r["relatedIdentifier"] for r in v2["relatedIdentifiers"]
            if r["relationType"] == "IsNewVersionOf"]
    assert back == ["10.6084/m9.figshare.100.v1"]


def test_records_without_a_doi_are_not_collapsed_together():
    """Missing DOIs must not merge unrelated records into one slot."""
    items = []
    for _ in range(2):
        rec = {"id": 1, "conceptrecid": "999", "metadata": {}}
        items.append({"family": vl.from_zenodo(rec), "poster_json": {}})
    stats = vl.link_families(items)
    assert stats["duplicate_files"] == 0
    assert stats["multi_version_families"] == 1


def test_figshare_latest_settled_across_siblings():
    items = [
        {"family": vl.from_figshare(figshare_record(21359946, n)), "poster_json": {}}
        for n in (2, 1, 3)
    ]
    vl.link_families(items)
    latest = {i["family"].sequence: i["poster_json"]["versionInfo"]["isLatestVersion"]
              for i in items}
    assert latest == {1: False, 2: False, 3: True}


# --- linking behaviour -------------------------------------------------------

def test_sibling_relations_point_the_right_way():
    a = {"family": vl.from_figshare(figshare_record(100, 1)), "poster_json": {}}
    b = {"family": vl.from_figshare(figshare_record(100, 2)), "poster_json": {}}
    vl.link_families([a, b])

    def rel(item, kind):
        return [r["relatedIdentifier"] for r in item["poster_json"]["relatedIdentifiers"]
                if r["relationType"] == kind]

    assert rel(a, "IsPreviousVersionOf") == ["10.6084/m9.figshare.100.v2"]
    assert rel(a, "IsNewVersionOf") == []
    assert rel(b, "IsNewVersionOf") == ["10.6084/m9.figshare.100.v1"]
    assert rel(b, "IsPreviousVersionOf") == []


def test_concept_doi_emitted_as_is_version_of():
    items = [
        {"family": vl.from_zenodo(zenodo_record(1, "10.5281/zenodo.1", 0, False)),
         "poster_json": {}},
        {"family": vl.from_zenodo(zenodo_record(2, "10.5281/zenodo.2", 1, True)),
         "poster_json": {}},
    ]
    vl.link_families(items)
    for item in items:
        anchors = [r["relatedIdentifier"] for r in item["poster_json"]["relatedIdentifiers"]
                   if r["relationType"] == "IsVersionOf"]
        assert anchors == ["10.5281/zenodo.10572542"]


def test_synthetic_root_is_not_emitted_as_a_related_identifier():
    """figshare:article:100 is not a resolvable identifier."""
    items = [
        {"family": vl.from_figshare(figshare_record(100, n)), "poster_json": {}}
        for n in (1, 2)
    ]
    vl.link_families(items)
    for item in items:
        idents = [r["relatedIdentifier"] for r in item["poster_json"]["relatedIdentifiers"]]
        assert not any(i.startswith("figshare:") for i in idents)
        assert item["poster_json"]["versionInfo"]["versionRoot"] == "figshare:article:100"


def test_depositor_relations_are_preserved():
    poster = {"relatedIdentifiers": [
        {"relatedIdentifier": "10.1234/paper", "relatedIdentifierType": "DOI",
         "relationType": "IsSupplementTo"},
    ]}
    items = [
        {"family": vl.from_figshare(figshare_record(100, 1)), "poster_json": poster},
        {"family": vl.from_figshare(figshare_record(100, 2)), "poster_json": {}},
    ]
    vl.link_families(items)
    kept = [r for r in poster["relatedIdentifiers"] if r["relationType"] == "IsSupplementTo"]
    assert len(kept) == 1


def test_linking_is_idempotent():
    def build():
        return [
            {"family": vl.from_figshare(figshare_record(100, n)), "poster_json": {}}
            for n in (1, 2, 3)
        ]

    once = build()
    vl.link_families(once)
    snapshot = [i["poster_json"] for i in once]

    twice = [{"family": vl.from_figshare(figshare_record(100, n)),
              "poster_json": dict(p)} for n, p in zip((1, 2, 3), snapshot)]
    vl.link_families(twice)
    assert [i["poster_json"] for i in twice] == snapshot


def test_relinking_after_reharvest_drops_stale_sibling_links():
    """A version removed upstream must not leave a dangling pointer."""
    items = [{"family": vl.from_figshare(figshare_record(100, n)), "poster_json": {}}
             for n in (1, 2)]
    vl.link_families(items)
    survivor = items[0]["poster_json"]
    assert any(r["relationType"] == "IsPreviousVersionOf"
               for r in survivor["relatedIdentifiers"])

    again = [{"family": vl.from_figshare(figshare_record(100, 1)),
              "poster_json": survivor}]
    vl.link_families(again)
    assert "relatedIdentifiers" not in survivor
    assert "versionInfo" not in survivor
    # No stale sequence left behind from the family it no longer belongs to.
    assert "version" not in survivor


def test_depositor_version_string_is_never_overwritten():
    """`version` belongs to the depositor. Ordering lives in versionSequence.

    Zenodo's version field is free text and commonly holds a date, so
    overwriting it with our sequence would destroy real metadata to duplicate
    something already published in versionInfo.
    """
    poster = {"version": "2024-08-02"}
    items = [
        {"family": vl.from_zenodo(zenodo_record(1, "10.5281/zenodo.1", 1, False)),
         "poster_json": poster},
        {"family": vl.from_zenodo(zenodo_record(2, "10.5281/zenodo.2", 2, True)),
         "poster_json": {}},
    ]
    vl.link_families(items)
    assert poster["version"] == "2024-08-02"
    assert poster["versionInfo"]["versionSequence"] == 2

    # The sibling disappears upstream and the record stands alone again.
    solo = vl.from_zenodo(zenodo_record(1, "10.5281/zenodo.1", 0, True))
    vl.link_families([{"family": solo, "poster_json": poster}])
    assert poster["version"] == "2024-08-02"
    assert "versionInfo" not in poster


def test_solitary_first_version_is_left_byte_identical():
    """The common case must not be rewritten at all, relations included."""
    poster = {
        "titles": [{"title": "A poster"}],
        "version": "1.0",
        "relatedIdentifiers": [
            {"relatedIdentifier": "10.1234/paper", "relatedIdentifierType": "DOI",
             "relationType": "IsSupplementTo"},
        ],
    }
    import copy
    original = copy.deepcopy(poster)
    item = {"family": vl.from_figshare(figshare_record(100, 1)), "poster_json": poster}
    vl.link_families([item])
    assert poster == original


def test_solitary_version_one_record_is_left_alone():
    """The overwhelmingly common case: one deposit, one version, no annotation."""
    poster = {"titles": [{"title": "A poster"}]}
    item = {"family": vl.from_figshare(figshare_record(100, 1)), "poster_json": poster}
    vl.link_families([item])
    assert poster == {"titles": [{"title": "A poster"}]}


def test_separate_families_do_not_cross_link():
    items = [
        {"family": vl.from_figshare(figshare_record(100, 1)), "poster_json": {}},
        {"family": vl.from_figshare(figshare_record(200, 1)), "poster_json": {}},
    ]
    stats = vl.link_families(items)
    assert stats["families"] == 2
    assert stats["multi_version_families"] == 0


# --- DOI normalization -------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("https://doi.org/10.5281/ZENODO.123", "10.5281/zenodo.123"),
    ("http://dx.doi.org/10.5281/zenodo.123", "10.5281/zenodo.123"),
    ("doi:10.5281/zenodo.123", "10.5281/zenodo.123"),
    ("  10.5281/zenodo.123  ", "10.5281/zenodo.123"),
    (None, ""),
])
def test_doi_normalization(raw, expected):
    assert vl._normalize_doi(raw) == expected


def test_normalized_dois_group_the_same_family():
    a = vl.from_zenodo({**zenodo_record(1, "https://doi.org/10.5281/zenodo.1", 0, False),
                        "conceptdoi": "https://doi.org/10.5281/ZENODO.10572542"})
    b = vl.from_zenodo(zenodo_record(2, "10.5281/zenodo.2", 1, True))
    assert a.root == b.root


# --- dispatch and schema conformance ----------------------------------------

def test_from_record_dispatch():
    assert vl.from_record(zenodo_record(1, "10.5281/zenodo.1", 0, True), "zenodo") is not None
    assert vl.from_record(figshare_record(100, 1), "figshare") is not None
    assert vl.from_record({}, "unknown") is None


def test_emitted_relations_use_only_schema_allowed_keys():
    allowed = {"relatedIdentifier", "relatedIdentifierType", "relationType",
               "resourceTypeGeneral"}
    items = [{"family": vl.from_zenodo(zenodo_record(i, f"10.5281/zenodo.{i}", i - 1,
                                                     i == 2)),
              "poster_json": {}} for i in (1, 2)]
    vl.link_families(items)
    for item in items:
        for r in item["poster_json"]["relatedIdentifiers"]:
            assert set(r) <= allowed, set(r) - allowed


def test_version_info_survives_conform_to_schema():
    from poster_to_json.field_normalize import conform_to_schema

    items = [{"family": vl.from_zenodo(zenodo_record(i, f"10.5281/zenodo.{i}", i - 1,
                                                     i == 2)),
              "poster_json": {}} for i in (1, 2)]
    vl.link_families(items)
    record = items[0]["poster_json"]
    conform_to_schema(record)
    assert "versionInfo" in record
    assert record["versionInfo"]["versionSequence"] == 1
