"""Adjacent DOI and excluded records (prod migration review, 2026-10-02).

Cases come from Dorian's Deferred DOIs sheet: Sussex Figshare posters with only
a Handle, whose related DOIs are either the poster's own published abstract
(BMC Neuroscience, CHI) or a paper the poster cites.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from poster_to_json.adjacent_doi import adopt_adjacent_doi, bare_doi, title_matches  # noqa: E402
from poster_to_json.exclusions import exclusion_reason  # noqa: E402


def handle_record(title, rel_dois):
    return {
        "titles": [{"title": title}],
        "identifiers": [{"identifier": "23431121", "identifierType": "Other"},
                        {"identifier": "10779/uos.23431121.v1", "identifierType": "Handle"}],
        "relatedIdentifiers": [{"relatedIdentifier": d, "relatedIdentifierType": "DOI",
                                "relationType": "References"} for d in rel_dois],
    }


REGISTERED = {
    "10.1186/1471-2202-14-s1-p378": "Data-driven honeybee antennal lobe model demonstrates how stimulus "
                                    "decorrelation emerges",
    "10.1145/2851581.2892471": "Scented Material",
    "10.1002/cyto.990060202": "A comparison of different focus functions for use in autofocus algorithms",
    "10.1093/nar/gkw1004": "CARD 2017: expansion and model-centric curation of the comprehensive "
                           "antibiotic resistance database",
}


def resolver(doi):
    return REGISTERED.get(doi.lower())


def test_own_published_abstract_becomes_the_doi():
    rec = handle_record("Data-driven honeybee antennal lobe model demonstrates how stimulus decorrelation emerges",
                        ["10.1002/cyto.990060202", "10.1186/1471-2202-14-S1-P378"])
    assert adopt_adjacent_doi(rec, resolver) == "10.1186/1471-2202-14-S1-P378"
    assert rec["identifiers"][0] == {"identifier": "10.1186/1471-2202-14-S1-P378", "identifierType": "DOI"}
    assert [i["identifierType"] for i in rec["identifiers"][1:]] == ["Other", "Handle"]
    assert [r["relatedIdentifier"] for r in rec["relatedIdentifiers"]] == ["10.1002/cyto.990060202"]


def test_main_title_before_a_colon_matches():
    rec = handle_record("Scented Material: Changing Features of Physical Creations based on Odors",
                        ["10.1145/2851581.2892471"])
    assert adopt_adjacent_doi(rec, resolver) == "10.1145/2851581.2892471"
    assert "relatedIdentifiers" not in rec


def test_cited_paper_is_not_adopted():
    rec = handle_record("High Precision Robotic Navigation and Sensing", ["10.1002/cyto.990060202"])
    before = {k: list(v) if isinstance(v, list) else v for k, v in rec.items()}
    assert adopt_adjacent_doi(rec, resolver) is None
    assert rec == before


def test_record_with_a_doi_is_left_alone():
    rec = handle_record("Scented Material: Changing Features", ["10.1145/2851581.2892471"])
    rec["identifiers"].insert(0, {"identifier": "10.6084/m9.figshare.1.v1", "identifierType": "DOI"})
    assert adopt_adjacent_doi(rec, resolver) is None


def test_short_or_generic_titles_never_match():
    assert not title_matches("Introduction", "Introduction")
    assert not title_matches("Poster", "Poster")
    assert title_matches("Benchmarking Drosophila Receptor Neurons for Technical Applications",
                         "Benchmarking Drosophilareceptor neurons for technical applications")


def test_extraction_punctuation_is_trimmed():
    assert bare_doi("10.1093/nar/gkw1004)") == "10.1093/nar/gkw1004"
    assert bare_doi("https://doi.org/10.1016/S0140-6736(17)32843-X") == "10.1016/S0140-6736(17)32843-X"
    assert bare_doi("https://example.org/page") is None


def test_spam_and_vendor_records_are_excluded():
    assert exclusion_reason("zenodo", "16960815_complete").startswith("Spam")
    assert exclusion_reason("zenodo", "16960815.v1").startswith("Spam")
    assert exclusion_reason("zenodo", "14090256").startswith("Vendor")
    assert exclusion_reason("figshare", "16960815") is None
    assert exclusion_reason("zenodo", "14947868") is None
