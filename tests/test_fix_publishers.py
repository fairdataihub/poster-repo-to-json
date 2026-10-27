"""fix_publishers.py: lossy publisher merges and the registrant publisher for Figshare."""
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "post_processing"))


def _mod():
    spec = importlib.util.spec_from_file_location("fix_publishers", ROOT / "scripts" / "post_processing" / "fix_publishers.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GEO = frozenset({"porto", "portugal", "sydney", "australia"})


def test_lossy_publisher_merge_is_restored():
    mod = _mod()
    d, stats = {"publisher": {"name": "Universidade do Porto"}}, Counter()
    assert mod.restore_publisher(d, "zenodo", ["Universidade Catolica Portuguesa"], GEO, stats)
    assert d["publisher"] == {"name": "Universidade Catolica Portuguesa"}
    assert mod.restore_publisher(d, "zenodo", ["Universidade Catolica Portuguesa"], GEO, stats) is None  # idempotent
    assert stats["restored"] == 1 and stats["kept_source_says_it"] == 1


def test_harmless_or_sourced_publishers_are_kept():
    mod = _mod()
    stats = Counter()
    d = {"publisher": {"name": "University of Sydney"}}
    assert mod.restore_publisher(d, "zenodo", ["The University of Sydney"], GEO, stats) is None
    assert d["publisher"] == {"name": "University of Sydney"} and stats["harmless_merge_kept"] == 1
    d = {"publisher": {"name": "Zenodo"}}                  # a cleaning step dropped the extraction value
    assert mod.restore_publisher(d, "zenodo", ["Haselman et al"], GEO, stats) is None
    assert stats["kept_repository_fallback"] == 1
    d = {"publisher": {"name": "EPA"}}                     # no extraction publisher at all
    assert mod.restore_publisher(d, "figshare", [], GEO, stats) is None
    assert stats["no_extraction_publisher"] == 1


def test_junk_extraction_publisher_is_not_restored():
    mod = _mod()
    d, stats = {"publisher": {"name": "Some Other Org"}}, Counter()
    assert mod.restore_publisher(d, "zenodo", ["The poster does not mention a publisher"], GEO, stats) is None
    assert d["publisher"] == {"name": "Some Other Org"} and stats["extraction_publisher_is_junk"] == 1


def _cache(tmp_path, doi, publisher):
    c = tmp_path / "cache"
    c.mkdir(exist_ok=True)
    (c / (doi.replace("/", "_").replace(":", "_") + ".json")).write_text(json.dumps({"publisher": publisher}))
    return c


def _meta(tmp_path, rid, doi):
    m = tmp_path / "metadata" / "figshare"
    m.mkdir(parents=True, exist_ok=True)
    (m / f"{rid}.json").write_text(json.dumps({"doi": doi}))
    return tmp_path / "metadata"


def test_figshare_fallback_takes_the_registrant_publisher(tmp_path):
    mod = _mod()
    doi = "10.60752/102376.27328098.v1"
    cache, meta = _cache(tmp_path, doi, "UL Research Institutes"), _meta(tmp_path, "27328098.v1", doi)
    d = {"publisher": {"name": "Figshare"}, "identifiers": [{"identifier": doi, "identifierType": "DOI"}]}
    stats = Counter()
    assert mod.registrant_publisher(d, "figshare", "27328098.v1", cache, meta, stats) == ("Figshare", "UL Research Institutes")
    assert d["publisher"] == {"name": "UL Research Institutes"}
    assert mod.registrant_publisher(d, "figshare", "27328098.v1", cache, meta, stats) is None   # idempotent


def test_registrant_publisher_skips(tmp_path):
    mod = _mod()
    doi = "10.6084/m9.figshare.1.v1"
    stats = Counter()
    d = {"publisher": {"name": "Figshare"}, "identifiers": [{"identifier": doi, "identifierType": "DOI"}]}
    cache = _cache(tmp_path, doi, "figshare")
    assert mod.registrant_publisher(d, "figshare", "1", cache, _meta(tmp_path, "1", doi), stats) is None
    assert stats["dc_registrant_is_figshare"] == 1
    # a poster publisher from the extraction is never replaced
    d2 = {"publisher": {"name": "Library & Researcher Information Services"},
          "identifiers": [{"identifier": doi, "identifierType": "DOI"}]}
    assert mod.registrant_publisher(d2, "figshare", "1", _cache(tmp_path, doi, "UL Research Institutes"), None, stats) is None
    # the DOI is not the deposit's own (e.g. a published abstract's DOI)
    other = "10.1000/abstract.1"
    d3 = {"publisher": {"name": "Figshare"}, "identifiers": [{"identifier": other, "identifierType": "DOI"}]}
    assert mod.registrant_publisher(d3, "figshare", "9", _cache(tmp_path, other, "Some Journal"), None, stats) is None
    assert stats["dc_doi_not_the_deposit"] == 1
    # Zenodo records are never touched
    d4 = {"publisher": {"name": "Zenodo"}, "identifiers": [{"identifier": other, "identifierType": "DOI"}]}
    assert mod.registrant_publisher(d4, "zenodo", "1", _cache(tmp_path, other, "Some Journal"), None, stats) is None



def test_deposit_doi_carries_the_article_id():
    mod = _mod()
    assert mod.deposit_doi("10.60752/102376.27328098.v1", "27328098.v1")
    assert mod.deposit_doi("10.17608/k6.auckland.10007483.v1", "10007483.v1")
    assert not mod.deposit_doi("10.1186/s12868-015-0001-1", "23431121")
    assert not mod.deposit_doi("10.6084/m9.figshare.127328098.v1", "27328098")
