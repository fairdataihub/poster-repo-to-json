"""Field normalization: conference."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poster_to_json.field_normalize import (
    normalize_conference, normalize_publisher, normalize_subjects,
    normalize_creators, normalize_formats,
)


def test_conference_drops_placeholders():
    rec = {"conference": {
        "conferenceName": "Not specified",
        "conferenceLocation": "Not specified",
        "conferenceStartDate": "Not specified",
        "conferenceEndDate": "Not specified",
        "conferenceYear": 2019,
    }}
    changed = normalize_conference(rec)
    assert changed
    # only the real signal (year) survives
    assert rec["conference"] == {"conferenceYear": 2019}
    print("OK conference: placeholders dropped, real field kept")


def test_conference_normalizes_dates_and_year():
    rec = {"conference": {
        "conferenceName": "AGU Fall Meeting",
        "conferenceStartDate": "9 December 2019",
        "conferenceEndDate": "13 December 2019",
        "conferenceYear": 9999,
    }}
    changed = normalize_conference(rec)
    assert changed
    c = rec["conference"]
    assert c["conferenceStartDate"] == "2019-12-09"
    assert c["conferenceEndDate"] == "2019-12-13"
    assert "conferenceYear" not in c            # 9999 dropped
    assert c["conferenceName"] == "AGU Fall Meeting"
    print("OK conference: dates ISO, garbage year dropped")


def test_conference_all_junk_dropped():
    rec = {"conference": {
        "conferenceName": "Not specified",
        "conferenceLocation": "",
        "conferenceStartDate": "unknown",
    }, "titles": [{"title": "T"}]}
    changed = normalize_conference(rec)
    assert changed
    assert "conference" not in rec               # nothing real -> object removed
    assert "titles" in rec                       # record intact
    print("OK conference: all-junk object removed, record kept")


def test_conference_null_and_notfound_dropped():
    rec = {"conference": {
        "conferenceName": "Conference Name Not Found",
        "conferenceEndDate": None,
        "conferenceLocation": None,
        "conferenceStartDate": None,
        "conferenceYear": 2018,
    }}
    changed = normalize_conference(rec)
    assert changed
    # null sub-values and the "not found" name are gone; real year kept
    assert rec["conference"] == {"conferenceYear": 2018}, rec["conference"]
    print("OK conference: null values + 'not found' name dropped")


def test_conference_clean_noop():
    rec = {"conference": {"conferenceName": "AGU", "conferenceYear": 2020}}
    assert normalize_conference(rec) is False
    print("OK conference: clean object unchanged (idempotent)")


def test_publisher_to_repository():
    z = {"identifiers": [{"identifier": "10.5281/zenodo.123", "identifierType": "DOI"}],
         "publisher": {"name": "PosterPresentations.com"}}
    assert normalize_publisher(z)
    assert z["publisher"] == {"name": "Zenodo"}
    fs = {"identifiers": [{"identifier": "10.6084/m9.figshare.9", "identifierType": "DOI"}],
          "publisher": {"name": "University of X"}}
    assert normalize_publisher(fs)
    assert fs["publisher"] == {"name": "Figshare"}
    # no DOI -> unchanged
    n = {"publisher": {"name": "Something"}}
    assert normalize_publisher(n) is False
    print("OK publisher: set to source repository")


def test_subjects_drop_junk_dedup():
    rec = {"subjects": [
        {"subject": "Genomics"}, {"subject": "genomics"},   # dup
        {"subject": "Not specified"},                        # placeholder
        {"subject": "https://example.com"},                  # url
        {"subject": "me@x.edu"},                             # email
        {"subject": "vortex structures, rotating cones"},    # commas kept (no split)
    ]}
    assert normalize_creators  # imported
    assert normalize_subjects(rec)
    subs = [s["subject"] for s in rec["subjects"]]
    assert subs == ["Genomics", "vortex structures, rotating cones"], subs
    print("OK subjects: junk dropped, deduped, no splitting")


def test_creators_drop_clear_junk():
    rec = {"creators": [
        {"name": "Doe, John", "affiliation": [{"name": "Acme"}, {"name": "Institution Name"}]},
        {"name": "null"},
        {"name": "Conference, Nanostruc2014"},
        {"name": "SARTOR", "givenName": "null"},   # placeholder givenName dropped
        {"name": "Smith, Jane^1, Doe, K.^2"},   # lumped-but-real: KEPT as-is
    ]}
    assert normalize_creators(rec)
    names = [c["name"] for c in rec["creators"]]
    assert names == ["Doe, John", "SARTOR", "Smith, Jane^1, Doe, K.^2"], names
    assert rec["creators"][0]["affiliation"] == [{"name": "Acme"}]  # placeholder aff dropped
    assert "givenName" not in rec["creators"][1]                    # null givenName dropped
    print("OK creators: clear junk dropped, lumped names kept")


def test_formats():
    rec = {"formats": ["pdf", "PDF", "Poster", "text/html"]}
    assert normalize_formats(rec)
    assert rec["formats"] == ["PDF", "HTML"], rec["formats"]
    print("OK formats: canonicalized, junk dropped, deduped")


if __name__ == "__main__":
    for k, v in sorted(globals().items()):
        if k.startswith("test_"):
            v()
    print("\nAll field-normalize checks passed.")
