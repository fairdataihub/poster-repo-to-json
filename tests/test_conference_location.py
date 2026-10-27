"""conference_location_geocode.py: verified geocoding and the repair of an earlier map."""
import importlib.util
import json
import pickle
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _mod():
    spec = importlib.util.spec_from_file_location("conference_location_geocode",
                                                  ROOT / "scripts" / "post_processing" / "conference_location_geocode.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _raw(name, cls="boundary", addresstype="city", cc="it", country="Italy", **names):
    return {"name": name, "class": cls, "addresstype": addresstype,
            "address": {"country": country, "country_code": cc},
            "namedetails": dict({"name": name}, **{k.replace("_", ":"): v for k, v in names.items()})}


def test_templates_and_dates_are_not_places():
    mod = _mod()
    for t in ["Conference Location", "Name of Location", "City, Country", "Venue", "TBD", "October 2021",
              "Spring 2020", "Fall 2019"]:
        assert mod.is_template(t), t
    for t in ["Marseille", "Mayo", "Berlin 2021", "San Diego, CA", "Ghent", "Springfield", "Spring, Texas"]:
        assert not mod.is_template(t), t


def test_build_uses_verified_places_only(tmp_path):
    mod = _mod()
    work = str(tmp_path / "loc")
    Path(work + ".terms.json").write_text(json.dumps({"Milano, Italy": 3, "Milan": 2, "CERN": 4, "Online": 1}))
    Path(work + ".geo.json").write_text(json.dumps({
        "Milano, Italy": {"raw": _raw("Milano", name_en="Milan")},
        "Milan": _raw("Milano", name_en="Milan"),                        # a bare raw answer works too
        "CERN": {"raw": _raw("Чернь", addresstype="town", cc="ru", country="Russia", name_en="Chern")},
        "Online": {"city": "Montpellier", "country": "France", "cc": "fr"}}))   # first-version entry
    mod.build(work)
    remap = pickle.load(open(work + ".map.pickle", "rb"))
    assert remap == {"Milano, Italy": "Milan, Italy", "Milan": "Milan, Italy"}


def test_repair_resets_old_canonicals_from_the_sources():
    mod = _mod()
    old = {"Pisa, Italy": "Tuscany, Italy", "CERN": "Chern, Russia", "Conference Location": "Nizhniy Arkhyz, Russia",
           "Milano": "Milan, Italy"}
    new = {"Pisa, Italy": "Pisa, Italy", "Milano": "Milan, Italy", "Wien": "Vienna, Austria"}
    stats = Counter()

    def run(loc, sources):
        d = {"conference": {"conferenceLocation": loc}}
        mod.repair_record(d, sources, old, new, stats)
        return d["conference"].get("conferenceLocation")
    assert run("Tuscany, Italy", ["Pisa, Italy"]) == "Pisa, Italy"
    assert run("Chern, Russia", ["CERN"]) == "CERN"
    assert run("Nizhniy Arkhyz, Russia", ["Conference Location"]) is None   # template removed
    assert run("Milan, Italy", ["Milano"]) == "Milan, Italy"               # verified: unchanged
    assert run("Wien", ["Wien"]) == "Vienna, Austria"                      # a new verified mapping
    assert run("Somewhere Else", ["Pisa, Italy"]) == "Somewhere Else"      # not explained: left
    assert stats["unexplained"] == 1 and stats["template_removed"] == 1


def test_subject_surface_key_keeps_acronyms_and_double_s():
    spec = importlib.util.spec_from_file_location("build_subject_surface_map",
                                                  ROOT / "scripts" / "post_processing" / "build_subject_surface_map.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    same = lambda a, b: m.surface_key(a) == m.surface_key(b)   # noqa: E731
    assert not same("mas", "mass") and not same("NGSS", "NGS") and not same("TES", "TESS")
    assert same("genomics", "genomic") and same("machine-learning", "Machine Learning")
    assert same("Exoplanets", "exoplanet")


def test_repair_takes_several_earlier_maps_and_build_checks_ambiguity(tmp_path):
    mod = _mod()
    stats = Counter()
    d = {"conference": {"conferenceLocation": "Province of Padua, Italy"}}   # written by a second map
    v1, v2 = {"Padova": "Veneto, Italy"}, {"Padova": "Province of Padua, Italy"}
    mod.repair_record(d, ["Padova"], [v1, v2], {"Padova": "Padova, Italy"}, stats)
    assert d["conference"]["conferenceLocation"] == "Padova, Italy"
    work = str(tmp_path / "loc")
    Path(work + ".terms.json").write_text(json.dumps({"Cambridge": 5, "Berlin": 3}))
    uk = dict(_raw("Cambridge", cc="gb", country="United Kingdom"), importance=0.74)
    us = dict(_raw("Cambridge", cc="us", country="United States"), importance=0.68)
    de = dict(_raw("Berlin", cc="de", country="Germany"), importance=0.85)
    Path(work + ".geo.json").write_text(json.dumps({"Cambridge": {"raw": uk, "others": [us]},
                                                    "Berlin": {"raw": de, "others": []}}))
    mod.build(work)
    assert pickle.load(open(work + ".map.pickle", "rb")) == {"Berlin": "Berlin, Germany"}
