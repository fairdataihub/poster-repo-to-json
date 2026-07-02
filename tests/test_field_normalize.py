"""Field normalization: conference."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poster_to_json.field_normalize import (
    normalize_conference, normalize_publisher, normalize_subjects,
    normalize_creators, normalize_formats, resolve_lumped_creators,
    same_author, dedup_creators,
)


def test_same_author_matching():
    assert same_author("Yadav Pinku", "Yadav, P.")
    assert same_author("Zambaldi, Claudio", "Zambaldi, C.")
    assert same_author("Bilodeau, Zoë", "Bilodeau, Zoe")
    assert same_author("Tunnell, Christopher D.", "Tunnell, Christopher")
    assert same_author("Lourenço, A. A.", "Lourenco, Abilio Afonso")
    assert same_author("Lemos, G. C.", "Lemos, Gina C.")     # middle initial
    assert same_author("Mace, G. N.", "Mace, Greg")          # extra middle initial
    # different people must NOT match
    assert not same_author("Smith, John", "Smith, Jane")
    assert not same_author("Yadav, P.", "Yadav Kumar")      # initial P != Kumar
    assert not same_author("Yadav, P.", "Yadav, K.")        # different initials
    assert not same_author("John Smith", "John Doe")        # shared given only
    assert not same_author("Smith, J. A.", "Smith, J. B.")  # differ on middle initial
    assert not same_author("Wang, Li", "Wang, Wei")         # same surname, diff given
    print("OK same_author: matches forms+middle initials, rejects distinct authors")


def test_dedup_creators_family_given():
    rec = {"creators": [
        {"name": "Yadav Pinku"},
        {"name": "Lacoste Eric", "affiliation": [{"name": "Bordeaux"}]},
        {"name": "Yadav, P.", "nameIdentifiers": [
            {"nameIdentifier": "0000-0001", "nameIdentifierScheme": "ORCID"}]},
        {"name": "Lacoste, E."},
        {"name": "Smith, Jane"},                       # distinct, untouched
        {"name": "Doe, John and Roe, Jane"},           # lumped, untouched
    ]}
    assert dedup_creators(rec)
    names = [c["name"] for c in rec["creators"]]
    assert names == ["Yadav, Pinku", "Lacoste, Eric", "Smith, Jane",
                     "Doe, John and Roe, Jane"], names
    # merged Yadav kept the ORCID from the deposit form + is Family, Given
    assert rec["creators"][0]["nameIdentifiers"][0]["nameIdentifier"] == "0000-0001"
    assert rec["creators"][1]["affiliation"] == [{"name": "Bordeaux"}]
    print("OK dedup: same-author forms collapsed to Family, Given; ids pooled")


def test_lumped_creators_prefer_clean_extraction():
    # deposit crammed 3 authors + et al into one name; extraction is clean
    deposit = [{"name": "A. Castillo-Morales, R. Rodriguez-Cardoso, A. Gil de Paz, et al."}]
    ext = [{"name": "Castillo-Morales, Africa"}, {"name": "Rodriguez Cardoso, Ramon"},
           {"name": "Gil de Paz, Armando"}]
    out = resolve_lumped_creators(deposit, ext)
    assert [c["name"] for c in out] == [c["name"] for c in ext]
    print("OK lumped: clean extraction preferred over lumped deposit")


def test_lumped_creators_keep_deposit_when_extraction_bad():
    # deposit lumped; extraction is the SAME lumped string duplicated -> keep deposit
    deposit = [{"name": "Koss, Paul, Piepenburg, Dieter, Teschke, Katharina"}]
    ext = [{"name": "Kloss, P., Piepenburg, D., Teschke, K."}] * 5
    out = resolve_lumped_creators(deposit, ext)
    assert out is deposit  # unchanged
    print("OK lumped: bad/duplicated extraction rejected, deposit kept")


def test_lumped_creators_noop_when_deposit_clean():
    deposit = [{"name": "Doe, John"}, {"name": "Smith, Jane"}]
    ext = [{"name": "Doe, J."}, {"name": "Smith, J."}]
    assert resolve_lumped_creators(deposit, ext) is deposit
    print("OK lumped: already-clean deposit untouched")


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


def test_subjects_split_dedup_and_preserve_taxonomy():
    rec = {"subjects": [
        {"subject": "Genomics"}, {"subject": "genomics"},   # dup
        {"subject": "Not specified"},                        # placeholder
        {"subject": "vortex structures, rotating cones; Taylor vortices"},  # split
        {"subject": "Business Information Management (incl. Records, Knowledge) not elsewhere classified"},  # FoR: keep whole
    ]}
    assert normalize_subjects(rec)
    subs = [s["subject"] for s in rec["subjects"]]
    assert subs == [
        "Genomics",
        "vortex structures", "rotating cones", "Taylor vortices",
        "Business Information Management (incl. Records, Knowledge) not elsewhere classified",
    ], subs
    print("OK subjects: comma/semicolon split, dedup, taxonomy (paren) preserved")


def test_creators_drop_clear_junk():
    rec = {"creators": [
        {"name": "Doe, John", "affiliation": [{"name": "Acme"}, {"name": "Institution Name"}]},
        {"name": "null"},
        {"name": "Conference, Nanostruc2014"},
        {"name": "SARTOR", "givenName": "null"},   # placeholder givenName dropped
        {"name": "LastName, FirstName"},         # template placeholder dropped
        {"name": "D"},                           # bare initial -> junk
        {"name": "M., A"},                       # initials only -> junk
        {"name": "0000-0002-1230-5392, 0000-0002-1304-469X"},  # ORCIDs -> junk
        {"name": "Wu, Li"},                      # short but real -> KEPT
        {"name": "Smith, Jane^1, Doe, K.^2"},   # lumped-but-real: KEPT as-is
    ]}
    assert normalize_creators(rec)
    names = [c["name"] for c in rec["creators"]]
    assert names == ["Doe, John", "SARTOR", "Wu, Li", "Smith, Jane^1, Doe, K.^2"], names
    assert rec["creators"][0]["affiliation"] == [{"name": "Acme"}]  # placeholder aff dropped
    assert "givenName" not in rec["creators"][1]                    # null givenName dropped
    print("OK creators: clear junk dropped, lumped names kept")


def test_creator_affiliation_bleed_split():
    rec = {"creators": [
        {"name": "Arora, Aashay - University of California San Diego"},
        {"name": "Smith, Jane"},                    # no bleed, untouched
        {"name": "Wong - Lee, Ming"},               # hyphen, not an org -> untouched
    ]}
    assert normalize_creators(rec)
    assert rec["creators"][0]["name"] == "Arora, Aashay"
    assert rec["creators"][0]["affiliation"] == [{"name": "University of California San Diego"}]
    assert rec["creators"][1]["name"] == "Smith, Jane"
    assert rec["creators"][2]["name"] == "Wong - Lee, Ming"
    print("OK creators: affiliation-in-name split off to affiliation")


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
