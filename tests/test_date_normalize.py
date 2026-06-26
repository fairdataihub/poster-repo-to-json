"""Date normalization: real patterns sampled from the corpus."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poster_to_json.date_normalize import (
    normalize_publication_year, normalize_date_value, normalize_record_dates,
)


def test_publication_year():
    assert normalize_publication_year(2024) == 2024
    assert normalize_publication_year("2019") == 2019
    assert normalize_publication_year(9999) is None
    assert normalize_publication_year(2029, max_year=2026) is None
    assert normalize_publication_year(2027, max_year=2026) is None
    assert normalize_publication_year(1981) == 1981   # genuine old poster
    assert normalize_publication_year(None) is None
    print("OK publicationYear")


def test_date_values():
    cases = {
        "2024-03-19/2024-03-20": "2024-03-19/2024-03-20",   # already ISO range
        "2024-03-19/2024-03-19": "2024-03-19",              # collapse dup halves
        "June 16-22, 2024": "2024-06-16/2024-06-22",
        "16-18 December 2019": "2019-12-16/2019-12-18",
        "16-18 December 2019/16-18 December 2019": "2019-12-16/2019-12-18",
        "3rd-7th June 2019": "2019-06-03/2019-06-07",
        "June 2020": "2020-06",
        "May 2018": "2018-05",
        "25-27 June 2008": "2008-06-25/2008-06-27",
        "September 26-27, 2023": "2023-09-26/2023-09-27",
        "2019-10-18T18:43:37Z": "2019-10-18",               # strip timestamp
        "Not specified/Not specified": None,
        "null": None,
        "N/A": None,
        "Not Found": None,
        "May": None,        # month only, no year -> drop
        "12": None,         # bare fragment -> drop
    }
    for raw, want in cases.items():
        got = normalize_date_value(raw)
        assert got == want, f"{raw!r}: got {got!r}, want {want!r}"
    print(f"OK date values ({len(cases)} cases)")


def test_record():
    rec = {
        "publicationYear": 9999,
        "dates": [
            {"date": "2024-03-19/2024-03-20", "dateType": "Issued"},
            {"date": "Not specified/Not specified", "dateType": "Presented"},
            {"date": "16-18 December 2019", "dateType": "Presented"},
        ],
    }
    changed = normalize_record_dates(rec, max_year=2026)
    assert changed
    assert "publicationYear" not in rec               # 9999 dropped
    types = [(d["dateType"], d["date"]) for d in rec["dates"]]
    assert ("Issued", "2024-03-19/2024-03-20") in types
    assert ("Presented", "2019-12-16/2019-12-18") in types
    assert all("Not specified" not in d["date"] for d in rec["dates"])  # junk gone
    assert len(rec["dates"]) == 2
    # idempotent
    assert normalize_record_dates(rec, max_year=2026) is False
    print("OK record normalization + idempotent")


if __name__ == "__main__":
    for k, v in sorted(globals().items()):
        if k.startswith("test_"):
            v()
    print("\nAll date-normalize checks passed.")
