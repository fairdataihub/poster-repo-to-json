"""License handling: normalizer, merger deposit-only rights, converter, backfill."""
import json
import subprocess
import sys
import tempfile
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

from poster_to_json.schema_converter import SchemaConverter, _normalize_license_id
from poster_to_json.merger import MetadataMerger

LICENSE_SCRIPT = ROOT / "scripts" / "post_processing" / "backfill_licenses.py"


def test_normalizer():
    assert _normalize_license_id("mit-license") == "MIT"
    assert _normalize_license_id("MIT") == "MIT"
    assert _normalize_license_id("cc-by-4.0") == "CC-BY-4.0"
    assert _normalize_license_id("CC-BY-4.0") == "CC-BY-4.0"
    assert _normalize_license_id("other-open") == "other-open"
    assert _normalize_license_id("") is None
    assert _normalize_license_id(None) is None
    print("OK normalizer")


def test_merger_drops_fabricated_rights_when_deposit_has_none():
    merger = MetadataMerger()
    extraction = {
        "titles": [{"title": "T"}],
        "content": {"sections": [{"sectionTitle": "x", "sectionContent": "y"}]},
        "rightsList": [{"rights": "This work was supported by NERC [grant NE/V01076X/1]"}],
    }
    out = merger.merge(extraction, {})  # deposit has no rightsList
    assert "rightsList" not in out, out.get("rightsList")
    print("OK merger: fabricated rights dropped when deposit has none")


def test_merger_deposit_rights_win():
    merger = MetadataMerger()
    extraction = {
        "titles": [{"title": "T"}],
        "content": {"sections": [{"sectionTitle": "x", "sectionContent": "y"}]},
        "rightsList": [{"rights": "Thanks to Prof. Myers and the lab"}],
    }
    metadata = {"rightsList": [{"rights": "CC-BY-4.0", "rightsIdentifier": "CC-BY-4.0"}]}
    out = merger.merge(extraction, metadata)
    # deposit wins; align_schema strips the sub-field, leaving only `rights`
    assert out["rightsList"] == [{"rights": "CC-BY-4.0"}]
    print("OK merger: deposit rights win over extraction (sub-fields stripped)")


def test_converter_legacy_and_invenio_and_none():
    conv = SchemaConverter()
    # legacy metadata.license with alias
    a = conv.convert_zenodo({"metadata": {"license": {"id": "mit-license"}}})
    assert a["rightsList"][0]["rights"] == "MIT", a.get("rightsList")
    # InvenioRDM metadata.rights
    b = conv.convert_zenodo({"metadata": {"rights": [{"id": "cc-by-4.0"}]}})
    assert b["rightsList"][0]["rights"] == "CC-BY-4.0", b.get("rightsList")
    # no license at all -> no rightsList (the 5553054 case)
    c = conv.convert_zenodo({"metadata": {"title": "X"}})
    assert "rightsList" not in c, c.get("rightsList")
    print("OK converter: legacy alias, InvenioRDM rights, and no-license")


def _write(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def test_backfill_licenses_end_to_end():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "corpus"
        meta = Path(td) / "metadata"
        # rec A: junk license in merged, deposit (raw) HAS a real license
        _write(root / "merged" / "zenodo" / "A_complete.json", {
            "titles": [{"title": "A"}],
            "content": {"sections": [{"sectionTitle": "x", "sectionContent": "y"}]},
            "rightsList": [{"rights": "Grant support: VEGA 1/0748/22"}],
        })
        _write(meta / "zenodo" / "A.json", {"metadata": {"license": {"id": "cc-by-4.0"}}})
        # rec B: junk license in merged, deposit (raw) has NO license -> remove
        _write(root / "merged" / "zenodo" / "B_complete.json", {
            "titles": [{"title": "B"}],
            "content": {"sections": [{"sectionTitle": "x", "sectionContent": "y"}]},
            "rightsList": [{"rights": "Contact me via foo@bar.edu"}],
        })
        _write(meta / "zenodo" / "B.json", {"metadata": {"title": "B"}})

        r = subprocess.run(
            [sys.executable, str(LICENSE_SCRIPT), "--root", str(root),
             "--metadata-dir", str(meta)],
            capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

        a = json.loads((root / "merged" / "zenodo" / "A_complete.json").read_text())
        assert a["rightsList"][0]["rights"] == "CC-BY-4.0", a.get("rightsList")
        b = json.loads((root / "merged" / "zenodo" / "B_complete.json").read_text())
        assert "rightsList" not in b, b.get("rightsList")

        # idempotent
        r2 = subprocess.run(
            [sys.executable, str(LICENSE_SCRIPT), "--root", str(root),
             "--metadata-dir", str(meta), "--dry-run"],
            capture_output=True, text=True)
        out2 = r2.stdout + r2.stderr
        assert "rights_set     0" in out2 and "rights_removed 0" in out2, out2
        print("OK backfill_licenses: deposit license set, junk removed, idempotent")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("\nAll license checks passed.")


def test_classify_license_normalizes_format_variants():
    sys.path.insert(0, str(ROOT / "scripts" / "post_processing"))
    from license_policy import classify_license, normalize_license
    # format variants of allowed licenses (spaces, case, version-less, region ports)
    assert normalize_license("CC BY 4.0") == "CC-BY-4.0"
    assert normalize_license("CC0") == "CC0-1.0"
    assert normalize_license("cc-by") == "CC-BY-4.0"
    assert normalize_license("Apache 2.0") == "Apache-2.0"
    assert normalize_license("cc-by-3.0-us") == "CC-BY-3.0"
    for lic in ["CC BY 4.0", "CC0", "cc-by", "Apache 2.0", "gpl-3.0-or-later", "mit-license", "other-open"]:
        assert classify_license([{"rights": lic}]) == "allowed", lic
    # blocked variants
    for lic in ["CC BY-ND 4.0", "CC-BY-NC-ND-4.0", "cc-by-nc-nd-4.0", "In Copyright", "All Rights Reserved"]:
        assert classify_license([{"rights": lic}]) == "blocked", lic
    # null / unrecognized
    assert classify_license(None) == "blocked"
    assert classify_license([{"rights": "Totally Made Up License"}]) == "unknown"
    # dual licensing: one allowed wins
    assert classify_license([{"rights": "In Copyright"}, {"rights": "CC BY 4.0"}]) == "allowed"
    print("OK classify_license: format-variant normalization")


def test_merger_enforces_license_policy():
    import copy
    from poster_to_json.merger import MetadataMerger
    merger = MetadataMerger()
    base = {
        "titles": [{"title": "T"}],
        "content": {"sections": [{"sectionTitle": "Intro", "sectionContent": "body text here"}]},
        "researchField": "Physical Sciences",
        "descriptions": [{"description": "llm summary", "descriptionType": "Other"}],
    }
    # blocked (ND) -> content stripped, flag set
    blocked = merger.merge(copy.deepcopy(base), {"rightsList": [{"rights": "CC-BY-ND-4.0"}]})
    assert blocked.get("_license_blocked") is True
    assert "content" not in blocked and "researchField" not in blocked
    # allowed via FORMAT VARIANT "CC BY 4.0" -> content kept (in-pipeline normalization)
    allowed = merger.merge(copy.deepcopy(base), {"rightsList": [{"rights": "CC BY 4.0"}]})
    assert not allowed.get("_license_blocked") and "content" in allowed
    # no license at all -> default-deny -> stripped
    nolic = merger.merge(copy.deepcopy(base), {})
    assert nolic.get("_license_blocked") is True and "content" not in nolic
    print("OK merger enforces license policy: block ND / keep CC-BY(format variant) / default-deny")


def test_classify_from_datacite_cc_uri_and_display_name():
    """DataCite deposits often drop rightsIdentifier and give only a
    jurisdiction-named display string plus a creativecommons.org URI."""
    from poster_to_json.license_policy import classify_license, normalize_license, _license_from_uri
    # spelled-out display name with a country jurisdiction -> canonical, allowed
    assert normalize_license("Creative Commons Attribution 3.0 Germany") == "CC-BY-3.0"
    assert normalize_license("Creative Commons Attribution Non Commercial 4.0 International") == "CC-BY-NC-4.0"
    # CC URI parsing (jurisdiction + version)
    assert _license_from_uri("https://creativecommons.org/licenses/by/3.0/de/legalcode") == "CC-BY-3.0"
    assert _license_from_uri("https://creativecommons.org/licenses/by-nc-sa/4.0/") == "CC-BY-NC-SA-4.0"
    # classify uses rights, then rightsUri; an ND URI blocks
    assert classify_license([{"rights": "Creative Commons Attribution 3.0 Germany",
                              "rightsUri": "https://creativecommons.org/licenses/by/3.0/de/legalcode"}]) == "allowed"
    assert classify_license([{"rights": "Creative Commons Attribution Non Commercial No Derivatives 3.0 Germany",
                              "rightsUri": "https://creativecommons.org/licenses/by-nc-nd/3.0/de/legalcode"}]) == "blocked"
    assert classify_license([{"rights": "x", "rightsUri": "https://creativecommons.org/licenses/by/4.0/"}]) == "allowed"
    # non-license access-level strings stay unresolved (default-deny)
    assert classify_license([{"rights": "Restricted Access", "rightsUri": "info:eu-repo/semantics/restrictedAccess"}]) == "unknown"
    print("OK classify recovers CC licenses from DataCite display names and URIs")


def test_recover_publication_year_from_date_signals():
    """The export stamps the ingest year on every record; recover the real year
    from dates[] (Issued preferred), else publishedAt, else conference."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "bpy", str(ROOT / "scripts/post_processing/backfill_export_publication_year.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # dates[] Issued wins over publishedAt
    assert mod.recover_year({"publishedAt": "2026-01-01",
                             "posterJson": {"dates": [{"date": "2019-05-02", "dateType": "Issued"}]}}) == 2019
    # fall back to publishedAt when no usable dates[]
    assert mod.recover_year({"publishedAt": "2021-09-05T00:00:00Z", "posterJson": {}}) == 2021
    # fall back to conference
    assert mod.recover_year({"posterJson": {"conference": {"conferenceStartDate": "2017-03-10"}}}) == 2017
    # nothing usable -> None (record left unchanged upstream)
    assert mod.recover_year({"posterJson": {}}) is None
    print("OK recover_year prefers Issued date, then publishedAt, then conference")
