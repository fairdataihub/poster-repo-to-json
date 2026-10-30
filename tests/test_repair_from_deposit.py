"""repair_from_deposit.py: a merged record repaired against its own deposit."""
import importlib.util
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
F = "�"


def _mod():
    spec = importlib.util.spec_from_file_location(
        "repair_from_deposit", ROOT / "scripts" / "post_processing" / "repair_from_deposit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _corpus(tmp_path, rid, deposit):
    m = tmp_path / "metadata" / "zenodo"
    m.mkdir(parents=True, exist_ok=True)
    (m / f"{rid}.json").write_text(json.dumps(deposit, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_creators_title_and_text_come_back_from_the_deposit(tmp_path):
    mod = _mod()
    deposit = {"id": 14921591, "doi": "10.5281/zenodo.14921591",
               "metadata": {"title": "Determination of Processability of Mechanically Recycled Flexible Plastics",
                            "creators": [{"name": "李, 杰"}],
                            "description": "<p>Recycling quality of flexible plastics</p>"}}
    corpus = _corpus(tmp_path, "14921591", deposit)
    raw, dep = mod._deposit(str(corpus), "zenodo", "14921591")
    d = {"titles": [{"title": f"Determina{F}on of Processability of Mechanically Recycled Flexible Plas{F}cs"}],
         "identifiers": [{"identifier": "10.5281/zenodo.14921591,", "identifierType": "DOI"}],
         "subjects": [{"subject": "Computational biology"}, {"subject": "Computational  Biology"}]}
    stats = Counter()
    assert mod.repair_record(d, "zenodo", "14921591", raw, dep, {}, {}, None, stats, [], 0)
    assert [c["name"] for c in d["creators"]] == ["李, 杰"]
    assert d["titles"][0]["title"] == "Determination of Processability of Mechanically Recycled Flexible Plastics"
    assert d["identifiers"][0]["identifier"] == "10.5281/zenodo.14921591"
    assert d["subjects"] == [{"subject": "Computational biology"}]
    # idempotent
    assert not mod.repair_record(d, "zenodo", "14921591", raw, dep, {}, {}, None, Counter(), [], 0)


def test_template_title_and_url_affiliations(tmp_path):
    mod = _mod()
    deposit = {"id": 17280313, "doi": "10.5281/zenodo.17280313",
               "metadata": {"title": "SPIRou and the search for brown dwarfs (and exoplanets)",
                            "creators": [{"name": "Oustlant, Christopher", "affiliation": "https://ror.org/02ezch769"}]}}
    corpus = _corpus(tmp_path, "17280313", deposit)
    raw, dep = mod._deposit(str(corpus), "zenodo", "17280313")
    d = {"titles": [{"title": "MAIN POSTER TITLE"}],
         "identifiers": [{"identifier": "10.5281/zenodo.17280313", "identifierType": "DOI"}],
         "creators": [{"name": "Oustlant, Christopher", "affiliation": [{"name": "https://ror.org/02h2x0161"}]}]}
    names = {"02ezch769": "Example Ocean Institute", "02h2x0161": "GEOMAR Helmholtz Centre for Ocean Research Kiel"}
    assert mod.repair_record(d, "zenodo", "17280313", raw, dep, {}, names, None, Counter(), [], 0)
    assert d["titles"][0]["title"] == "SPIRou and the search for brown dwarfs (and exoplanets)"
    # the depositor's ROR URL wins, and becomes the affiliation it identifies
    assert d["creators"][0]["affiliation"] == [{
        "name": "Example Ocean Institute", "affiliationIdentifier": "https://ror.org/02ezch769",
        "affiliationIdentifierScheme": "ROR", "schemeURI": "https://ror.org"}]


def test_a_zenodo_doi_is_added_only_when_datacite_has_it(tmp_path):
    mod = _mod()

    class Check:
        def __init__(self, ok):
            self.ok = ok

        def registered_for(self, doi, rid):
            return self.ok

    deposit = {"id": 1101, "metadata": {"title": "Coordination of National Environment Programmes",
                                        "creators": [{"name": "Pittman, Adrienne"}]}}
    corpus = _corpus(tmp_path, "1101", deposit)
    raw, dep = mod._deposit(str(corpus), "zenodo", "1101")
    d = {"titles": [{"title": "Coordination of National Environment Programmes"}],
         "creators": [{"name": "Pittman, Adrienne"}]}
    stats = Counter()
    assert not mod.repair_record(d, "zenodo", "1101", raw, dep, {}, {}, Check(False), stats, [], 0)
    assert stats["zenodo_no_doi_unverified"] == 1 and "identifiers" not in d
    assert mod.repair_record(d, "zenodo", "1101", raw, dep, {}, {}, Check(True), stats, [], 0)
    assert d["identifiers"] == [{"identifier": "10.5281/zenodo.1101", "identifierType": "DOI"}]
