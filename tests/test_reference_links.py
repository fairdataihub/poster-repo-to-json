"""Citations become References relations (poster_to_json.reference_links).

The Zenodo reference list below is the real one for record 10801927 (live poster
2979), which showed one reference on posters.science while Zenodo lists three.
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

from poster_to_json.reference_links import (  # noqa: E402
    add_reference_links,
    citation_identifier,
    link_poster_references,
)

ZENODO_REFERENCES = [
    "Cornelis, C., De Brouwere, K., De Fré, R., Goyvaerts, M. P., Schoeters, G., Swaans, W., "
    "& Van Holderbeke, M. (2007). Voorstel voor milieukwaliteitsnormen voor depositie van "
    "dioxines en PCB's (2007/IMS/R/278).",
    "Scientific Committee on Food (SCF), Opinion on the Risk Assessment of Dioxins and "
    "Dioxin-like PCBs in Food. 2001, Scientific Committee on Food: Brussels, Belgium. p. 29. "
    "https://ec.europa.eu/food/fs/sc/scf/out90_en.pdf",
    "EFSA CONTAM Panel (2018). Risk for animal and human health related to the presence of "
    "dioxins and dioxin‐like PCBs in feed and food. EFSA Journal, 16(11), e05333. "
    "https://doi.org/10.2903/j.efsa.2018.5333",
]


def rels(record):
    return [(r["relatedIdentifier"], r["relatedIdentifierType"], r["relationType"])
            for r in record.get("relatedIdentifiers", [])]


# --- picking the identifier out of a citation ---------------------------------

def test_doi_inside_a_doi_org_url_is_a_doi():
    assert citation_identifier(ZENODO_REFERENCES[2]) == ("10.2903/j.efsa.2018.5333", "DOI")


def test_plain_url_is_a_url_and_a_citation_without_identifier_is_none():
    assert citation_identifier(ZENODO_REFERENCES[1]) == (
        "https://ec.europa.eu/food/fs/sc/scf/out90_en.pdf", "URL")
    assert citation_identifier(ZENODO_REFERENCES[0]) is None


def test_trailing_punctuation_and_wrapping_parens_are_trimmed():
    assert citation_identifier("Smith (2020). Title. (doi: 10.1000/abc.123).")[0] == "10.1000/abc.123"
    assert citation_identifier("See https://example.org/page, accessed 2021.")[0] == \
        "https://example.org/page"


def test_balanced_parentheses_inside_a_doi_are_kept():
    assert citation_identifier("Lancet. doi:10.1016/S0140-6736(20)30183-5.")[0] == \
        "10.1016/S0140-6736(20)30183-5"
    # a DOI ending in its own parenthesis, inside the citation's parentheses:
    # only the unbalanced outer ")" goes
    assert citation_identifier("Ref (doi:10.1234/abc(2020))")[0] == "10.1234/abc(2020)"


def test_arxiv_and_pmid():
    assert citation_identifier("Preprint arXiv:2101.00001v2") == ("2101.00001v2", "arXiv")
    assert citation_identifier("J Biol. 2019. PMID: 31234567") == ("31234567", "PMID")


def test_doi_wins_over_url_in_the_same_citation():
    text = "Paper at https://journal.org/article/5, doi 10.1234/xyz"
    assert citation_identifier(text) == ("10.1234/xyz", "DOI")


# --- adding relations ---------------------------------------------------------

def test_poster_2979_gets_both_linkable_references_and_keeps_related_work():
    record = {
        "identifiers": [{"identifier": "10.5281/zenodo.10801927", "identifierType": "DOI"}],
        "relatedIdentifiers": [{
            "relatedIdentifier": "https://dioxin2023.org/wp-content/uploads/2023/10/10445-Dioxin-2023-BOA-2023.pdf",
            "relatedIdentifierType": "URL", "relationType": "IsDescribedBy"}],
    }
    assert add_reference_links(record, ZENODO_REFERENCES) == 2
    assert rels(record) == [
        ("https://dioxin2023.org/wp-content/uploads/2023/10/10445-Dioxin-2023-BOA-2023.pdf",
         "URL", "IsDescribedBy"),
        ("https://ec.europa.eu/food/fs/sc/scf/out90_en.pdf", "URL", "References"),
        ("10.2903/j.efsa.2018.5333", "DOI", "References"),
    ]


def test_existing_relation_is_not_duplicated_even_as_a_doi_org_url():
    record = {"relatedIdentifiers": [{"relatedIdentifier": "https://doi.org/10.2903/J.EFSA.2018.5333",
                                      "relatedIdentifierType": "URL", "relationType": "References"}]}
    assert add_reference_links(record, ZENODO_REFERENCES[2:]) == 0


def test_own_identifier_is_never_a_reference():
    record = {"identifiers": [{"identifier": "10.5281/zenodo.1", "identifierType": "DOI"}]}
    assert add_reference_links(record, ["This poster: https://doi.org/10.5281/zenodo.1"]) == 0


def test_idempotent():
    record = {}
    add_reference_links(record, ZENODO_REFERENCES)
    once = rels(record)
    assert add_reference_links(record, ZENODO_REFERENCES) == 0
    assert rels(record) == once


def test_poster_parsed_references_are_linked():
    record = {"references": [
        {"id": "1", "authors": "Cornelis, C.", "year": "2007", "title": "Voorstel (2007/IMS/R/278)"},
        {"id": "2", "authors": "EFSA CONTAM Panel", "year": "2018",
         "title": "Risk for animal and human health. EFSA Journal. doi: 10.2903/j.efsa.2018.5333."},
    ]}
    assert link_poster_references(record) is True
    assert rels(record) == [("10.2903/j.efsa.2018.5333", "DOI", "References")]


# --- the pipeline: converter and merger ---------------------------------------

def test_convert_zenodo_links_the_deposit_reference_list():
    from poster_to_json.schema_converter import SchemaConverter

    out = SchemaConverter().convert_zenodo({
        "id": 10801927, "doi": "10.5281/zenodo.10801927", "conceptrecid": "10801926",
        "metadata": {
            "title": "Deriving new deposition threshold values",
            "related_identifiers": [{
                "identifier": "https://dioxin2023.org/boa.pdf", "relation": "isDescribedBy",
                "resource_type": "publication-other", "scheme": "url"}],
            "references": ZENODO_REFERENCES,
        },
    })
    kinds = [(t, rel) for _, t, rel in rels(out)]
    assert kinds == [("URL", "IsDescribedBy"), ("URL", "References"), ("DOI", "References")]


def test_merger_keeps_repository_relations_when_extraction_has_its_own():
    from poster_to_json.merger import MetadataMerger

    extraction = {"relatedIdentifiers": [
        {"relatedIdentifier": "10.9999/from-poster", "relatedIdentifierType": "DOI",
         "relationType": "References"}]}
    metadata = {"relatedIdentifiers": [
        {"relatedIdentifier": "https://dioxin2023.org/boa.pdf", "relatedIdentifierType": "URL",
         "relationType": "IsDescribedBy"},
        {"relatedIdentifier": "10.2903/j.efsa.2018.5333", "relatedIdentifierType": "DOI",
         "relationType": "References"}]}
    merger = MetadataMerger()
    merger.ENFORCE_LICENSE = False
    out = merger.merge(extraction, metadata)
    ids = [r["relatedIdentifier"] for r in out.get("relatedIdentifiers", [])]
    assert ids == ["https://dioxin2023.org/boa.pdf", "10.2903/j.efsa.2018.5333", "10.9999/from-poster"]
