"""Field normalization: conference."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poster_to_json.field_normalize import normalize_conference


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


if __name__ == "__main__":
    for k, v in sorted(globals().items()):
        if k.startswith("test_"):
            v()
    print("\nAll field-normalize checks passed.")
