"""Tests for repository version family detection and linking.

The Zenodo fixtures mirror live API responses for concept 10572542, which holds
three versions (index 0, 1, 2) of which our harvest only ever saw two. That gap
is the case the module exists to get right.

Only fields the poster schema already defines are written, so every assertion
here is about `relatedIdentifiers`. The platform derives its three columns from
those: versionRootId from IsVersionOf, isLatestVersion from the absence of
IsPreviousVersionOf, and versionSequence from position in the chain.
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


def rel(poster_json, relation):
    """Related identifiers of one relation type, as the platform would read them."""
    return [r["relatedIdentifier"] for r in poster_json.get("relatedIdentifiers") or []
            if r.get("relationType") == relation]


def is_latest(poster_json):
    """How the platform derives isLatestVersion: nothing newer points away."""
    return not rel(poster_json, "IsPreviousVersionOf")


# --- Zenodo ------------------------------------------------------------------

def test_zenodo_sequence_is_repository_index_plus_one():
    f = vl.from_zenodo(zenodo_record(17435084, "10.5281/zenodo.17435084", 2, True))
    assert f.sequence == 3
    assert f.is_latest is True
    assert f.root_doi == "10.5281/zenodo.10572542"
    assert f.group_key == "10.5281/zenodo.10572542"
    assert f.source == "zenodo-relations"


def test_zenodo_not_latest_is_honoured():
    f = vl.from_zenodo(zenodo_record(13168995, "10.5281/zenodo.13168995", 1, False))
    assert f.sequence == 2
    assert f.is_latest is False


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
    assert f.root_doi == ""
    assert f.group_key == "zenodo:recid:999"


def test_zenodo_no_signal_returns_none():
    assert vl.from_zenodo({"id": 1, "doi": "10.5281/zenodo.1", "metadata": {}}) is None
    assert vl.from_zenodo(None) is None


# --- Figshare ----------------------------------------------------------------

def test_figshare_root_is_the_article_doi_without_the_version_suffix():
    """Figshare's base DOI is registered with DataCite and resolves.

    It is the direct equivalent of Zenodo's concept DOI, so it can be published
    as an IsVersionOf target rather than needing an invented key.
    """
    f = vl.from_figshare(figshare_record(21359946, 1))
    assert f.root_doi == "10.6084/m9.figshare.21359946"
    assert f.own_doi == "10.6084/m9.figshare.21359946.v1"
    assert f.sequence == 1
    assert f.source == "figshare-version"


def test_figshare_institutional_prefixes_work_the_same_way():
    rec = {"id": 24049458, "doi": "10.17044/scilifelab.24049458.v2",
           "version": 2, "defined_type": 4}
    f = vl.from_figshare(rec)
    assert f.root_doi == "10.17044/scilifelab.24049458"
    assert f.sequence == 2


def test_figshare_falls_back_to_vn_suffix():
    rec = {"id": 555, "doi": "10.6084/m9.figshare.555.v4", "defined_type": 4}
    f = vl.from_figshare(rec)
    assert f.sequence == 4
    assert f.source == "figshare-doi"


def test_figshare_without_a_versioned_doi_has_no_root_doi():
    rec = {"id": 777, "doi": "10.6084/m9.figshare.777", "version": 1, "defined_type": 4}
    f = vl.from_figshare(rec)
    assert f.root_doi == ""
    assert f.group_key == "figshare:article:777"


# --- what actually gets written ----------------------------------------------

def test_family_anchor_and_sibling_relations():
    a, b = {}, {}
    items = [
        {"family": vl.from_zenodo(zenodo_record(1, "10.5281/zenodo.1", 0, False)),
         "poster_json": a},
        {"family": vl.from_zenodo(zenodo_record(2, "10.5281/zenodo.2", 1, True)),
         "poster_json": b},
    ]
    vl.link_families(items)

    for pj in (a, b):
        assert rel(pj, "IsVersionOf") == ["10.5281/zenodo.10572542"]
    assert rel(a, "IsPreviousVersionOf") == ["10.5281/zenodo.2"]
    assert rel(a, "IsNewVersionOf") == []
    assert rel(b, "IsNewVersionOf") == ["10.5281/zenodo.1"]
    assert rel(b, "IsPreviousVersionOf") == []
    assert is_latest(a) is False
    assert is_latest(b) is True


def test_nothing_but_related_identifiers_is_written():
    """No invented top-level fields. The schema already has what we need."""
    poster = {"titles": [{"title": "A poster"}], "version": "2024-08-02"}
    items = [
        {"family": vl.from_zenodo(zenodo_record(1, "10.5281/zenodo.1", 1, False)),
         "poster_json": poster},
        {"family": vl.from_zenodo(zenodo_record(2, "10.5281/zenodo.2", 2, True)),
         "poster_json": {}},
    ]
    vl.link_families(items)
    assert set(poster) == {"titles", "version", "relatedIdentifiers"}
    assert "versionInfo" not in poster


def test_depositor_version_string_is_never_touched():
    """`version` belongs to the depositor and is not an ordering field.

    Zenodo lets them put anything in it: dates are common, and one record in the
    corpus reads "Posters.science automated".
    """
    poster = {"version": "Posters.science automated"}
    items = [
        {"family": vl.from_zenodo(zenodo_record(1, "10.5281/zenodo.1", 1, False)),
         "poster_json": poster},
        {"family": vl.from_zenodo(zenodo_record(2, "10.5281/zenodo.2", 2, True)),
         "poster_json": {}},
    ]
    vl.link_families(items)
    assert poster["version"] == "Posters.science automated"


def test_zenodo_gap_in_harvest_does_not_renumber():
    """Harvesting index 1 and 2 must not present them as versions 1 and 2."""
    older, newer = {}, {}
    items = [
        {"family": vl.from_zenodo(zenodo_record(13168995, "10.5281/zenodo.13168995", 1, False)),
         "poster_json": older},
        {"family": vl.from_zenodo(zenodo_record(17435084, "10.5281/zenodo.17435084", 2, True)),
         "poster_json": newer},
    ]
    vl.link_families(items)
    assert [i["family"].sequence for i in items] == [2, 3]
    assert rel(older, "IsPreviousVersionOf") == ["10.5281/zenodo.17435084"]
    assert rel(newer, "IsNewVersionOf") == ["10.5281/zenodo.13168995"]


def test_lone_later_version_still_gets_its_family_anchor():
    """We hold v2 and never harvested v1. The family link is still true."""
    poster = {}
    item = {"family": vl.from_zenodo(
        zenodo_record(13168995, "10.5281/zenodo.13168995", 1, True)),
        "poster_json": poster}
    vl.link_families([item])
    assert rel(poster, "IsVersionOf") == ["10.5281/zenodo.10572542"]
    assert rel(poster, "IsNewVersionOf") == []
    assert is_latest(poster) is True


def test_solitary_first_version_is_left_byte_identical():
    """The overwhelmingly common case must not be rewritten at all."""
    import copy
    poster = {
        "titles": [{"title": "A poster"}],
        "version": "1.0",
        "relatedIdentifiers": [
            {"relatedIdentifier": "10.1234/paper", "relatedIdentifierType": "DOI",
             "relationType": "IsSupplementTo"},
        ],
    }
    original = copy.deepcopy(poster)
    vl.link_families([{"family": vl.from_figshare(figshare_record(100, 1)),
                       "poster_json": poster}])
    assert poster == original


def test_synthetic_group_key_is_never_published():
    """A repository-scoped key groups records but is not a resolvable identifier.

    Institutional Figshare deposits sometimes carry a Handle rather than a
    versioned DOI, leaving nothing to publish as a family anchor. Siblings still
    link to each other by their own DOIs; the group key stays internal.
    """
    a, b = {}, {}
    items = [
        {"family": vl.from_figshare({"id": 777, "doi": doi, "version": n,
                                     "defined_type": 4}),
         "poster_json": pj}
        for doi, n, pj in (("10.6084/m9.figshare.777", 1, a),
                           ("10.6084/m9.figshare.778", 2, b))
    ]
    vl.link_families(items)
    assert items[0]["family"].group_key == "figshare:article:777"
    for pj in (a, b):
        for r in pj.get("relatedIdentifiers") or []:
            assert not r["relatedIdentifier"].startswith("figshare:")
        assert rel(pj, "IsVersionOf") == []
    assert rel(a, "IsPreviousVersionOf") == ["10.6084/m9.figshare.778"]
    assert rel(b, "IsNewVersionOf") == ["10.6084/m9.figshare.777"]


# --- stale flags and duplicate files -----------------------------------------

def test_stale_is_last_is_overridden_by_a_higher_harvested_sequence():
    """Zenodo's is_last is only true as of harvest time.

    A record harvested while it was newest keeps claiming to be latest. If the
    corpus later picks up its successor, holding a higher sequence in the same
    family proves the flag went stale.
    """
    old, new = {}, {}
    items = [
        # Harvested in 2023, when it really was the last version.
        {"family": vl.from_zenodo(zenodo_record(7853235, "10.5281/zenodo.7853235", 0, True,
                                                concept_doi="10.5281/zenodo.7853234",
                                                concept_recid="7853234")),
         "poster_json": old},
        # Harvested later. The 2023 snapshot was never refreshed.
        {"family": vl.from_zenodo(zenodo_record(17169582, "10.5281/zenodo.17169582", 1, True,
                                                concept_doi="10.5281/zenodo.7853234",
                                                concept_recid="7853234")),
         "poster_json": new},
    ]
    stats = vl.link_families(items)
    assert stats["stale_latest_corrected"] == 1
    assert is_latest(old) is False
    assert is_latest(new) is True


def test_highest_sequence_keeps_the_repository_answer():
    """is_last False on the newest record we hold means a newer one exists upstream.

    We cannot point at it, so the record simply carries no forward link and the
    platform reads it as latest among what we have. Nothing is fabricated.
    """
    items = [
        {"family": vl.from_zenodo(zenodo_record(1, "10.5281/zenodo.1", 0, True)),
         "poster_json": {}},
        {"family": vl.from_zenodo(zenodo_record(2, "10.5281/zenodo.2", 1, False)),
         "poster_json": {}},
    ]
    vl.link_families(items)
    assert items[0]["family"].is_latest is False
    assert items[1]["family"].is_latest is False


def test_same_record_in_two_corpus_slices_is_not_two_versions():
    """A re-ingested poster has the same DOI in two harvest batches."""
    a, b = {}, {}
    items = [
        {"family": vl.from_figshare(figshare_record(15087663, 1)), "poster_json": a},
        {"family": vl.from_figshare(figshare_record(15087663, 1)), "poster_json": b},
    ]
    stats = vl.link_families(items)
    assert stats["duplicate_files"] == 1
    assert stats["multi_version_families"] == 0
    # One version only: nothing to link, and above all no self-reference.
    assert a == {} and b == {}


def test_duplicate_files_annotated_but_counted_once():
    """Two slices hold v1; one slice holds v2. That is a family of two."""
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
        assert rel(pj, "IsPreviousVersionOf") == ["10.6084/m9.figshare.100.v2"]
        assert is_latest(pj) is False
    # The newer record points back at v1 once, not twice.
    assert rel(v2, "IsNewVersionOf") == ["10.6084/m9.figshare.100.v1"]
    assert is_latest(v2) is True


def test_records_without_a_doi_are_not_collapsed_together():
    items = [{"family": vl.from_zenodo({"id": 1, "conceptrecid": "999", "metadata": {}}),
              "poster_json": {}} for _ in range(2)]
    stats = vl.link_families(items)
    assert stats["duplicate_files"] == 0
    assert stats["multi_version_families"] == 1


def test_figshare_latest_settled_across_siblings():
    posters = [{} for _ in range(3)]
    items = [
        {"family": vl.from_figshare(figshare_record(21359946, n)), "poster_json": pj}
        for n, pj in zip((2, 1, 3), posters)
    ]
    vl.link_families(items)
    by_seq = {i["family"].sequence: i["poster_json"] for i in items}
    assert is_latest(by_seq[1]) is False
    assert is_latest(by_seq[2]) is False
    assert is_latest(by_seq[3]) is True


# --- idempotence and cleanup -------------------------------------------------

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
    def run():
        posters = [{} for _ in range(3)]
        items = [{"family": vl.from_figshare(figshare_record(100, n)), "poster_json": pj}
                 for n, pj in zip((1, 2, 3), posters)]
        vl.link_families(items)
        return posters

    once = run()
    twice = [dict(p) for p in once]
    items = [{"family": vl.from_figshare(figshare_record(100, n)), "poster_json": pj}
             for n, pj in zip((1, 2, 3), twice)]
    vl.link_families(items)
    assert twice == once


def test_relinking_replaces_our_own_stale_sibling_links():
    """Re-running after a re-harvest converges instead of accumulating links."""
    survivor = {}
    items = [
        {"family": vl.from_figshare(figshare_record(100, 1)), "poster_json": survivor},
        {"family": vl.from_figshare(figshare_record(100, 2)), "poster_json": {}},
    ]
    vl.link_families(items)
    assert rel(survivor, "IsPreviousVersionOf") == ["10.6084/m9.figshare.100.v2"]

    # A third version appears. The forward link must move, not duplicate.
    items = [
        {"family": vl.from_figshare(figshare_record(100, n)), "poster_json": pj}
        for n, pj in ((1, survivor), (2, {}), (3, {}))
    ]
    vl.link_families(items)
    assert rel(survivor, "IsPreviousVersionOf") == ["10.6084/m9.figshare.100.v2"]
    assert len(rel(survivor, "IsVersionOf")) == 1


def test_depositor_version_relations_are_never_deleted():
    """Depositors declare their own version relations and the corpus has them.

    Record 12681959 arrived with its own IsVersionOf. An earlier draft stripped
    every version relation before rewriting and destroyed 25 of these.
    """
    # Solitary record: we assert nothing, so we touch nothing.
    solo = {"relatedIdentifiers": [
        {"relatedIdentifier": "10.5281/zenodo.8045979", "relatedIdentifierType": "DOI",
         "relationType": "IsVersionOf"},
    ]}
    import copy
    untouched = copy.deepcopy(solo)
    vl.link_families([{"family": vl.from_figshare(figshare_record(100, 1)),
                       "poster_json": solo}])
    assert solo == untouched

    # Record in a real family: our own links are rewritten, theirs survives.
    poster = {"relatedIdentifiers": [
        {"relatedIdentifier": "https://example.org/an-older-poster",
         "relatedIdentifierType": "URL", "relationType": "IsNewVersionOf"},
    ]}
    items = [
        {"family": vl.from_figshare(figshare_record(200, 1)), "poster_json": poster},
        {"family": vl.from_figshare(figshare_record(200, 2)), "poster_json": {}},
    ]
    vl.link_families(items)
    assert "https://example.org/an-older-poster" in rel(poster, "IsNewVersionOf")
    assert rel(poster, "IsPreviousVersionOf") == ["10.6084/m9.figshare.200.v2"]


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
    assert a.group_key == b.group_key


# --- dispatch and schema conformance ----------------------------------------

def test_from_record_dispatch():
    assert vl.from_record(zenodo_record(1, "10.5281/zenodo.1", 0, True), "zenodo") is not None
    assert vl.from_record(figshare_record(100, 1), "figshare") is not None
    assert vl.from_record({}, "unknown") is None


def test_emitted_relations_use_only_schema_allowed_keys():
    allowed = {"relatedIdentifier", "relatedIdentifierType", "relationType",
               "resourceTypeGeneral"}
    items = [{"family": vl.from_zenodo(zenodo_record(i, f"10.5281/zenodo.{i}", i - 1, i == 2)),
              "poster_json": {}} for i in (1, 2)]
    vl.link_families(items)
    for item in items:
        for r in item["poster_json"]["relatedIdentifiers"]:
            assert set(r) <= allowed, set(r) - allowed


def test_output_survives_conform_to_schema():
    """conform_to_schema must not strip what we write, because it is all schema."""
    from poster_to_json.field_normalize import conform_to_schema

    items = [{"family": vl.from_zenodo(zenodo_record(i, f"10.5281/zenodo.{i}", i - 1, i == 2)),
              "poster_json": {}} for i in (1, 2)]
    vl.link_families(items)
    record = items[0]["poster_json"]
    before = [dict(r) for r in record["relatedIdentifiers"]]
    conform_to_schema(record)
    assert record["relatedIdentifiers"] == before
