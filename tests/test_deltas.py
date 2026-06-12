"""Sanity checks for the repository-authoritative merge deltas (v0.4.0).

Covers the four agreed behaviors:
  1. creators       -> repository (Zenodo) names/order win; extraction only
                       enriches ORCID / ROR / nameType, never overwrites.
  2. subjects       -> union + case-insensitive dedup.
  3. descriptions   -> repository deposit description is the primary Abstract;
                       the LLM summary is demoted to a secondary "Other".
  4. fundingReferences -> repository funders/grants win.
Plus: convert_zenodo reads person_or_org.type for nameType.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# tqdm is the only third-party import in merger/converter; stub it if absent.
try:
    import tqdm  # noqa: F401
except ImportError:
    import types
    m = types.ModuleType("tqdm")
    m.tqdm = lambda x, **k: x
    sys.modules["tqdm"] = m

from poster_to_json.merger import MetadataMerger
from poster_to_json.schema_converter import SchemaConverter, _zenodo_name_type


def test_creators_repo_wins_extraction_enriches():
    merger = MetadataMerger()
    # Extraction: different ordering, a typo'd name, but carries ORCID + ROR.
    extraction = {
        "titles": [{"title": "T"}],
        "content": {"sections": [{"sectionTitle": "x", "sectionContent": "y"}]},
        "creators": [
            {"name": "Smith, Jane",
             "nameIdentifiers": [{"nameIdentifier": "0000-0001", "nameIdentifierScheme": "ORCID"}],
             "affiliation": [{"name": "Acme University",
                              "affiliationIdentifier": "https://ror.org/05acme",
                              "affiliationIdentifierScheme": "ROR"}]},
            {"name": "Doe, John"},
        ],
    }
    metadata = {
        "creators": [
            # Curated order: Doe first. Smith has affiliation text but no ROR.
            {"name": "Doe, John", "nameType": "Personal"},
            {"name": "Smith, Jane", "nameType": "Personal",
             "affiliation": [{"name": "Acme University"}]},
        ],
    }
    out = merger.merge(extraction, metadata)
    names = [c["name"] for c in out["creators"]]
    assert names == ["Doe, John", "Smith, Jane"], f"order should be Zenodo's: {names}"
    smith = out["creators"][1]
    assert smith["nameIdentifiers"][0]["nameIdentifier"] == "0000-0001", "ORCID enriched"
    assert smith["affiliation"][0]["affiliationIdentifier"] == "https://ror.org/05acme", "ROR grafted"
    assert smith["affiliation"][0]["name"] == "Acme University", "repo affiliation text kept"
    print("OK creators: repo order kept, ORCID+ROR enriched")


def test_subjects_union_dedup():
    merger = MetadataMerger()
    extraction = {
        "titles": [{"title": "T"}],
        "content": {"sections": [{"sectionTitle": "x", "sectionContent": "y"}]},
        "subjects": [{"subject": "Genomics"}, {"subject": "CRISPR"}],
    }
    metadata = {"subjects": [{"subject": "crispr"}, {"subject": "Proteomics"}]}
    out = merger.merge(extraction, metadata)
    subs = [s["subject"].lower() for s in out["subjects"]]
    assert subs == ["genomics", "crispr", "proteomics"], subs
    print("OK subjects: union + dedup")


def test_descriptions_repo_primary_llm_other():
    merger = MetadataMerger()
    extraction = {
        "titles": [{"title": "T"}],
        "content": {"sections": [{"sectionTitle": "x", "sectionContent": "y"}]},
        "descriptions": [{"description": "LLM generated summary of the poster.",
                          "descriptionType": "Abstract"}],
    }
    metadata = {"descriptions": [{"description": "Depositor's curated abstract.",
                                  "descriptionType": "Abstract"}]}
    out = merger.merge(extraction, metadata)
    assert out["descriptions"][0]["description"] == "Depositor's curated abstract."
    assert out["descriptions"][0]["descriptionType"] == "Abstract"
    assert out["descriptions"][1]["description"].startswith("LLM generated")
    assert out["descriptions"][1]["descriptionType"] == "Other", "LLM summary demoted"
    print("OK descriptions: Zenodo Abstract primary, LLM summary -> Other")


def test_descriptions_no_repo_keeps_extraction_abstract():
    merger = MetadataMerger()
    extraction = {
        "titles": [{"title": "T"}],
        "content": {"sections": [{"sectionTitle": "x", "sectionContent": "y"}]},
        "descriptions": [{"description": "LLM summary stays Abstract when deposit has none.",
                          "descriptionType": "Abstract"}],
    }
    out = merger.merge(extraction, {})  # no metadata description
    assert out["descriptions"][0]["descriptionType"] == "Abstract"
    print("OK descriptions: missing deposit abstract leaves extraction as Abstract")


def test_funding_repo_wins():
    merger = MetadataMerger()
    extraction = {
        "titles": [{"title": "T"}],
        "content": {"sections": [{"sectionTitle": "x", "sectionContent": "y"}]},
        "fundingReferences": [{"funderName": "Some funder the LLM guessed"}],
    }
    metadata = {"fundingReferences": [
        {"funderName": "National Science Foundation", "awardNumber": "ABC-123"}]}
    out = merger.merge(extraction, metadata)
    assert len(out["fundingReferences"]) == 1
    assert out["fundingReferences"][0]["funderName"] == "National Science Foundation"
    assert out["fundingReferences"][0]["awardNumber"] == "ABC-123"
    print("OK funding: Zenodo deposit funders win")


def test_nametype_from_person_or_org():
    assert _zenodo_name_type({"person_or_org": {"type": "organizational"}}) == "Organizational"
    assert _zenodo_name_type({"person_or_org": {"type": "personal"}}) == "Personal"
    assert _zenodo_name_type({"type": "organization"}) == "Organizational"
    assert _zenodo_name_type({}) == "Personal"
    conv = SchemaConverter()
    rec = {"metadata": {"creators": [
        {"name": "World Health Organization", "type": "organizational"},
        {"name": "Smith, Jane"},
    ]}}
    out = conv.convert_zenodo(rec)
    assert out["creators"][0]["nameType"] == "Organizational"
    assert out["creators"][1]["nameType"] == "Personal"
    print("OK nameType: person_or_org.type honored, defaults Personal")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nAll {len(fns)} delta checks passed.")
