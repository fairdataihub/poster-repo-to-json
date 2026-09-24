"""Zenodo deposits that borrowed another repository's DOI.

Mirrors the case found in production: Zenodo record 1196536 is the same poster
as Figshare article 5467180 v3, deposited twice, and its DOI field holds the
Figshare DOI instead of a Zenodo one. The Figshare record owns the DOI; the
Zenodo copy must be dropped so it neither ships as a DOI-less duplicate nor
gets linked into the Figshare family. A Zenodo record carrying a journal or
ResearchGate DOI that nothing else in the corpus holds must be left alone.
"""

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from poster_to_json import version_linking as vl  # noqa: E402

FIGSHARE_V3 = "10.6084/m9.figshare.5467180.v3"
JOURNAL = "10.13140/rg.2.2.20714.36805"


def test_is_borrowed_doi():
    assert vl.is_borrowed_doi("zenodo", FIGSHARE_V3)
    assert vl.is_borrowed_doi("zenodo", "https://doi.org/" + JOURNAL)
    assert not vl.is_borrowed_doi("zenodo", "10.5281/zenodo.1196536")
    assert not vl.is_borrowed_doi("zenodo", "")
    assert not vl.is_borrowed_doi("figshare", FIGSHARE_V3)


def test_copy_of_a_record_we_hold_is_dropped():
    borrowed = {"1196536": FIGSHARE_V3}
    owners = {FIGSHARE_V3: [("figshare", "5467180")]}
    assert vl.find_borrowed_doi_copies(borrowed, owners) == {"1196536": FIGSHARE_V3}


def test_borrowed_doi_nobody_else_holds_is_kept():
    borrowed = {"2528673": JOURNAL}
    owners = {JOURNAL: [("zenodo", "2528673")]}  # only the Zenodo record itself
    assert vl.find_borrowed_doi_copies(borrowed, owners) == {}


def test_doi_matching_is_normalized():
    borrowed = {"1196536": "https://doi.org/10.6084/M9.FIGSHARE.5467180.V3"}
    owners = {FIGSHARE_V3: [("figshare", "5467180")]}
    assert vl.find_borrowed_doi_copies(borrowed, owners) == {"1196536": FIGSHARE_V3}


# --- end to end through scripts/post_processing/link_versions.py -------------

def _load_link_versions():
    path = ROOT / "scripts" / "post_processing" / "link_versions.py"
    spec = importlib.util.spec_from_file_location("link_versions_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def _corpus(tmp_path):
    raw = tmp_path / "raw"
    _write(raw / "figshare" / "5467180_v3.json", {
        "id": 5467180, "doi": FIGSHARE_V3, "version": 3, "defined_type": 4,
        "url_public_html": "https://figshare.com/articles/poster/x/5467180/3"})
    _write(raw / "zenodo" / "1196536.json", {
        "id": 1196536, "recid": "1196536", "conceptrecid": "1196535",
        "doi": FIGSHARE_V3, "metadata": {}})
    _write(raw / "zenodo" / "2528673.json", {
        "id": 2528673, "recid": "2528673", "conceptrecid": "2528672",
        "doi": JOURNAL, "metadata": {}})
    corpus = tmp_path / "corpus"
    fig = corpus / "figshare" / "5467180.v3_complete.json"
    copy = corpus / "zenodo" / "1196536_complete.json"
    journal = corpus / "zenodo" / "2528673_complete.json"
    _write(fig, {"version": "3", "identifiers": [
        {"identifier": FIGSHARE_V3, "identifierType": "DOI"}]})
    # the copy already had its borrowed DOI cleaned off, as in production
    _write(copy, {"identifiers": [{"identifier": "1196536", "identifierType": "Other"}]})
    _write(journal, {"identifiers": [{"identifier": JOURNAL, "identifierType": "DOI"}]})
    return raw, corpus, fig, copy, journal


def _run(mod, monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["link_versions.py", *argv])
    mod.main()


def test_link_versions_deletes_the_copy_and_keeps_the_rest(tmp_path, monkeypatch, capsys):
    raw, corpus, fig, copy, journal = _corpus(tmp_path)
    _run(_load_link_versions(), monkeypatch, "--corpus", str(corpus), "--raw", str(raw))
    assert not copy.exists()
    assert fig.exists() and journal.exists()
    assert "borrowed-DOI copies deleted   : 1" in capsys.readouterr().out


def test_link_versions_dry_run_deletes_nothing(tmp_path, monkeypatch, capsys):
    raw, corpus, fig, copy, journal = _corpus(tmp_path)
    _run(_load_link_versions(), monkeypatch, "--corpus", str(corpus), "--raw", str(raw),
         "--dry-run")
    assert copy.exists() and fig.exists() and journal.exists()
    assert "borrowed-DOI copies to drop   : 1" in capsys.readouterr().out
