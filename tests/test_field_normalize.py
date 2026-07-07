"""Field normalization: conference."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poster_to_json.field_normalize import (
    normalize_conference, normalize_publisher, normalize_subjects,
    normalize_creators, normalize_formats, resolve_lumped_creators,
    same_author, dedup_creators, split_lumped_name, normalize_lumped_creators,
    align_schema,
)


def test_align_schema():
    rec = {
        "$schema": "https://posters.science/schema/v0.1/poster_schema.json",
        "_source": "pdfalto",
        "_extraction_time_s": 66.0,
        "_license_blocked": True,
        "conference": None,
        "researchField": "Physical Sciences",
        "types": {"resourceType": "Scientific Poster", "resourceTypeGeneral": "Image"},
        "creators": [
            {"name": "Doe, John",
             "nameIdentifiers": [{"nameIdentifier": "0000-0001-2345-6789", "nameIdentifierScheme": "ORCID"}],
             "affiliation": [{"name": "Acme", "affiliationIdentifier": "https://ror.org/05acme",
                              "affiliationIdentifierScheme": "ROR", "schemeUri": "https://ror.org/"},
                             "BRIN, Indonesia", None]},
            {"name": "Roe, Jane", "affiliation": None},
        ],
        "identifiers": [{"identifier": "10.5281/zenodo.1", "identifierType": "DOI"},
                        {"identifier": "1", "identifierType": "Other"},
                        {"identifier": "10.ref/x", "identifierType": "DOI"}],
        "relatedIdentifiers": [{"relatedIdentifier": "10.ref/x", "relatedIdentifierType": "DOI",
                                "relationType": "References"}],
        "rightsList": [{"rights": "CC-BY-4.0", "rightsIdentifier": "CC-BY-4.0",
                        "rightsIdentifierScheme": "SPDX", "schemeUri": "https://spdx.org/licenses/"}],
        "dates": [{"date": "2024", "dateType": "Issued"},
                  {"date": "2023", "dateType": "Submitted"},
                  {"date": "2024/2024", "dateType": "Presented"}],
        "publisher": {"name": "Zenodo", "publisherIdentifier": "https://ror.org/x"},
    }
    assert align_schema(rec)
    assert rec["$schema"] == "https://posters.science/schema/v0.2/poster_schema.json"
    assert "_source" not in rec and "_extraction_time_s" not in rec
    assert rec.get("_license_blocked") is True   # ingestion-consumed, must survive
    assert rec.get("domain") == "Physical Sciences"  # mirrors researchField for ingestion
    assert "conference" not in rec
    assert rec["types"] == {"resourceType": "Poster", "resourceTypeGeneral": "Poster"}
    nid = rec["creators"][0]["nameIdentifiers"][0]
    assert nid["schemeURI"] == "https://orcid.org"
    assert nid["nameIdentifier"] == "https://orcid.org/0000-0001-2345-6789"  # URL-normalized
    aff = rec["creators"][0]["affiliation"]
    assert aff[0]["schemeURI"] == "https://ror.org" and "schemeUri" not in aff[0]
    assert aff[1] == {"name": "BRIN, Indonesia"} and len(aff) == 2  # string wrapped, null dropped
    assert "affiliation" not in rec["creators"][1]                  # null affiliation dropped
    assert [i["identifier"] for i in rec["identifiers"]] == ["10.5281/zenodo.1", "1"]  # ref DOI removed
    assert rec["rightsList"] == [{"rights": "CC-BY-4.0"}]
    assert [d["dateType"] for d in rec["dates"]] == ["Issued", "Presented"]  # only Submitted stripped
    assert rec["publisher"] == {"name": "Zenodo"}
    assert align_schema(rec) is False   # idempotent
    print("OK align_schema: schema/internals/conference/affiliation/identifiers + prior fixes")


def test_ensure_presented_date():
    from poster_to_json.field_normalize import ensure_presented_date
    rec = {"conference": {"conferenceName": "AGU 2023",
                          "conferenceStartDate": "2023-12-11", "conferenceEndDate": "2023-12-15"},
           "dates": [{"date": "2023-12-01", "dateType": "Issued"}]}
    assert ensure_presented_date(rec)
    pres = [d for d in rec["dates"] if d["dateType"] == "Presented"]
    assert pres and pres[0]["date"] == "2023-12-11/2023-12-15"
    assert pres[0]["dateInformation"] == "Presented at AGU 2023"
    assert ensure_presented_date(rec) is False        # idempotent (already has one)
    # single-day conference -> single date; no conference -> no-op
    r2 = {"conference": {"conferenceStartDate": "2022-05-02", "conferenceEndDate": "2022-05-02"}}
    ensure_presented_date(r2)
    assert r2["dates"][0]["date"] == "2022-05-02"
    assert ensure_presented_date({"conference": None}) is False
    print("OK ensure_presented_date: derived from conference range for parity")


def test_reconcile_publication_year():
    from poster_to_json.field_normalize import reconcile_publication_year
    # LLM year survived; Issued is deposit-authoritative -> follow Issued
    r = {"publicationYear": 2024, "dates": [{"date": "2025-03-01", "dateType": "Issued"}]}
    assert reconcile_publication_year(r) and r["publicationYear"] == 2025
    assert reconcile_publication_year(r) is False            # idempotent
    # no Issued -> fall back to Presented
    r2 = {"publicationYear": 2019, "dates": [{"date": "2021-06-06/2021-06-08", "dateType": "Presented"}]}
    assert reconcile_publication_year(r2) and r2["publicationYear"] == 2021
    # bogus future Issued (>max_year) -> leave publicationYear untouched
    r3 = {"publicationYear": None, "dates": [{"date": "2029-01-01", "dateType": "Issued"}]}
    assert reconcile_publication_year(r3) is False and r3["publicationYear"] is None


def test_sanitize_conference_dates():
    from poster_to_json.field_normalize import sanitize_conference_dates
    r = {"publicationYear": 2018,
         "conference": {"conferenceName": "Neutrino 2018", "conferenceYear": 2025,
                        "conferenceStartDate": "2025-06-04", "conferenceLocation": "X"},
         "dates": [{"date": "2018-07-02", "dateType": "Issued"},
                   {"date": "2025-06-04", "dateType": "Presented"}]}
    assert sanitize_conference_dates(r)
    assert "conferenceYear" not in r["conference"]            # hallucinated year dropped
    assert "conferenceStartDate" not in r["conference"]       # future start dropped
    assert r["conference"]["conferenceName"] == "Neutrino 2018"   # name kept
    assert [d["dateType"] for d in r["dates"]] == ["Issued"]  # wrong Presented dropped
    # credible conference (same year) untouched
    ok = {"publicationYear": 2023,
          "conference": {"conferenceStartDate": "2023-06-28", "conferenceYear": 2023}}
    assert sanitize_conference_dates(ok) is False
    print("OK sanitize_conference_dates: drops future conf dates + derived Presented")


def test_conference_string_coercion():
    from poster_to_json.field_normalize import normalize_conference
    # bare string -> {conferenceName}; now a valid object + reachable by sanitizer
    r = {"conference": "AGU Fall Meeting"}
    assert normalize_conference(r)
    assert r["conference"] == {"conferenceName": "AGU Fall Meeting"}
    # empty string -> dropped
    r2 = {"conference": "   "}
    assert normalize_conference(r2) and "conference" not in r2
    print("OK normalize_conference: string conference coerced to object")


def test_strip_invalid_dates():
    from poster_to_json.field_normalize import strip_invalid_dates
    # bogus future Issued (2029) stripped; valid Presented kept
    r = {"dates": [{"date": "2029-08-28", "dateType": "Issued"},
                   {"date": "2020-06-06", "dateType": "Presented"}],
         "conference": {"conferenceYear": 9999, "conferenceStartDate": "2019-05-01",
                        "conferenceName": "X"}}
    assert strip_invalid_dates(r)
    assert [d["date"] for d in r["dates"]] == ["2020-06-06"]      # 2029 stripped
    assert "conferenceYear" not in r["conference"]                # 9999 stripped
    assert r["conference"]["conferenceStartDate"] == "2019-05-01"  # valid kept
    # all dates invalid -> dates dropped entirely
    r2 = {"dates": [{"date": "9999", "dateType": "Issued"}]}
    assert strip_invalid_dates(r2) and "dates" not in r2
    # bad end of a range strips the entry
    r3 = {"dates": [{"date": "2020-01-01/2035-01-01", "dateType": "Presented"}]}
    assert strip_invalid_dates(r3) and "dates" not in r3
    # all valid -> no-op
    assert strip_invalid_dates({"dates": [{"date": "2023-01-01", "dateType": "Issued"}]}) is False
    print("OK strip_invalid_dates: out-of-range years stripped (hard rule)")


def test_split_lumped_name():
    # explicit "and"
    assert split_lumped_name("Doe, John and Roe, Jane") == ["Doe, John", "Roe, Jane"]
    # Family, Given pairs
    assert split_lumped_name("Koss, Paul, Piepenburg, Dieter, Teschke, Katharina") == \
        ["Koss, Paul", "Piepenburg, Dieter", "Teschke, Katharina"]
    # list of multi-word full names (needs 3+ commas to count as lumped)
    assert split_lumped_name("Rabah Abdul Khalek, Valerio Bertone, Alice Khoudli, Emanuele Nocera") == \
        ["Rabah Abdul Khalek", "Valerio Bertone", "Alice Khoudli", "Emanuele Nocera"]
    # ambiguous -> leave alone
    assert split_lumped_name("Cardoso, M, E, G.") is None            # initials, not people
    assert split_lumped_name("Velazquez Miranda, Santiago. Dept. of Medical Physics") is None
    # organisations: single org never splits; a list of distinct orgs may
    assert split_lumped_name("Croatian Agency for Agriculture and Food, Center for Food Safety") is None
    assert split_lumped_name("University of California, Berkeley, Department of Physics, USA") is None
    assert split_lumped_name("Heidelberg University, University of Macedonia, RI REACH GmbH, AXIA Innovation GmbH") == \
        ["Heidelberg University", "University of Macedonia", "RI REACH GmbH", "AXIA Innovation GmbH"]
    print("OK split_lumped_name: clean splits only, single orgs & ambiguous left")


def test_normalize_lumped_creators():
    rec = {"creators": [{"name": "Doe, John and Roe, Jane"}, {"name": "Smith, Jane"}]}
    assert normalize_lumped_creators(rec)
    assert [c["name"] for c in rec["creators"]] == ["Doe, John", "Roe, Jane", "Smith, Jane"]
    print("OK normalize_lumped_creators: lumped split, others kept")


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


def test_normalize_name_identifiers():
    from poster_to_json.field_normalize import normalize_name_identifiers
    rec = {"creators": [
        {"name": "A", "nameIdentifiers": [
            {"nameIdentifier": "05kytsw45", "nameIdentifierScheme": "ROR"},
            {"nameIdentifier": "0000 0001 0738 8966", "nameIdentifierScheme": "ISNI"},
            {"nameIdentifier": "118540238", "nameIdentifierScheme": "GND"},
            {"nameIdentifier": "0000-0001-2345-6789", "nameIdentifierScheme": "ORCID"},
            {"nameIdentifier": "https://example.org/x", "nameIdentifierScheme": "URL"},
        ]},
        {"name": "B"},
    ]}
    assert normalize_name_identifiers(rec)
    n = rec["creators"][0]["nameIdentifiers"]
    assert n[0]["nameIdentifier"] == "https://ror.org/05kytsw45" and n[0]["schemeURI"] == "https://ror.org"
    assert n[1]["nameIdentifier"] == "https://isni.org/isni/0000000107388966" and n[1]["schemeURI"] == "https://isni.org"
    assert n[2]["nameIdentifier"] == "https://d-nb.info/gnd/118540238" and n[2]["schemeURI"] == "https://d-nb.info/gnd/"
    assert "schemeURI" not in n[3] and n[3]["nameIdentifier"] == "0000-0001-2345-6789"  # ORCID left to align_schema
    assert n[4] == {"nameIdentifier": "https://example.org/x", "nameIdentifierScheme": "URL"}
    assert normalize_name_identifiers(rec) is False
    print("OK normalize_name_identifiers: ROR/ISNI/GND URL-normalized, ORCID/URL left")


def test_drop_invalid_orcids():
    from poster_to_json.field_normalize import drop_invalid_orcids
    rec = {"creators": [
        {"name": "Doe, John", "nameIdentifiers": [
            {"nameIdentifier": "https://orcid.org/0000-0001-2345-6789", "nameIdentifierScheme": "ORCID"},
            {"nameIdentifier": "https://orcid.org/0000-0000-0000-0000", "nameIdentifierScheme": "ORCID"},
        ]},
        {"name": "Roe, Jane", "nameIdentifiers": [
            {"nameIdentifier": "2105-0019-1463-2019", "nameIdentifierScheme": "ORCID"}]},
        {"name": "Foo, Bar", "nameIdentifiers": [
            {"nameIdentifier": "0034-7167-2019-0109", "nameIdentifierScheme": "ISNI"}]},
    ]}
    assert drop_invalid_orcids(rec)
    assert rec["creators"][0]["nameIdentifiers"] == [
        {"nameIdentifier": "https://orcid.org/0000-0001-2345-6789", "nameIdentifierScheme": "ORCID"}]
    assert "nameIdentifiers" not in rec["creators"][1]
    assert rec["creators"][2]["nameIdentifiers"][0]["nameIdentifierScheme"] == "ISNI"  # non-ORCID untouched
    for bad in ("0000-0000-0000-0000", "2105-0019-1463-2019", "0001-0003-3648-8952",
                "0034-7167-2019-0109", "0999-8992-8534-5985", "0999-8992-9453-6695"):
        r = {"creators": [{"name": "X", "nameIdentifiers": [
            {"nameIdentifier": bad, "nameIdentifierScheme": "ORCID"}]}]}
        assert drop_invalid_orcids(r) and "nameIdentifiers" not in r["creators"][0]
    r2 = {"creators": [{"name": "Y", "nameIdentifiers": [
        {"nameIdentifier": "0000-0002-1694-233X", "nameIdentifierScheme": "ORCID"}]}]}
    assert drop_invalid_orcids(r2) is False  # valid X-check ORCID kept
    print("OK drop_invalid_orcids: checksum drop, all-zeros, X-check, scheme-scoped")


def test_split_lumped_two_full_names_and_remnants():
    from poster_to_json.field_normalize import split_lumped_name, normalize_lumped_creators
    assert split_lumped_name("Enrique Vázquez-Semadeni and Robert M. Loughnane") == [
        "Enrique Vázquez-Semadeni", "Robert M. Loughnane"]
    assert split_lumped_name("Alexis L. Quintana & Nicholas J. Wright") == [
        "Alexis L. Quintana", "Nicholas J. Wright"]
    assert split_lumped_name("Boumaaza, J. and others") == ["Boumaaza, J."]
    for keep in ("Marks & Spencer", "Procter & Gamble", "Bill & Melinda Gates Foundation",
                 "Science and Technology Facilities Council", "John Smith", "Doe, John"):
        assert split_lumped_name(keep) is None, keep
    rec = {"creators": [{"name": "Anna C. Childs and Rebecca G. Martin"}, {"name": "Doe, John"}]}
    assert normalize_lumped_creators(rec)
    assert [c["name"] for c in rec["creators"]] == ["Anna C. Childs", "Rebecca G. Martin", "Doe, John"]
    assert normalize_lumped_creators(rec) is False
    print("OK split_lumped_name: two-full-name and/& split + remnant drop, precision")


def test_drop_letterless_creator_fields():
    from poster_to_json.field_normalize import drop_letterless_creator_fields
    rec = {"creators": [
        {"name": "Ng, Wei", "givenName": "Wei", "familyName": "Ng"},
        {"name": "Wang, Li", "givenName": "丽", "familyName": "王"},   # CJK kept
        {"name": "Oh, Sun", "givenName": "2", "familyName": "Oh"},
        {"name": "Wu, Ann", "givenName": "Ann", "familyName": "-"},
        {"name": "123"},
    ]}
    assert drop_letterless_creator_fields(rec)
    assert [c["name"] for c in rec["creators"]] == ["Ng, Wei", "Wang, Li", "Oh, Sun", "Wu, Ann"]
    assert rec["creators"][1] == {"name": "Wang, Li", "givenName": "丽", "familyName": "王"}  # native script kept
    assert "givenName" not in rec["creators"][2] and "familyName" not in rec["creators"][3]
    assert drop_letterless_creator_fields(rec) is False
    assert drop_letterless_creator_fields({"creators": [{"name": "&"}, {"name": "2"}]}) is False  # never zero
    print("OK drop_letterless: no-letter dropped, alpha + native-script names kept, never zero")


def test_normalize_affiliation_in_name():
    from poster_to_json.field_normalize import normalize_affiliation_in_name
    rec = {"creators": [
        {"name": "Velázquez Miranda, Santiago. Dept. of Medical Physics. Virgen del Rocio University Hospital"},
        {"name": "Davies, University of Bremen"},   # comma-affiliation -> split, person kept
        {"name": "Dept. of Electrical Machines and Drives. Technical University of Cluj-Napoca"},
        {"name": "Torres-Company, Victor"}, {"name": "Companys, Berta"},
        {"name": "Stephens, Ag"}, {"name": "The, S.L"},
        {"name": "Hospital, Marc Antoni Pere"},  # marker surname, person -> untouched
        {"name": "Smith, Jane"},
    ]}
    assert normalize_affiliation_in_name(rec)
    cs = rec["creators"]
    assert cs[0]["name"] == "Velázquez Miranda, Santiago"
    assert cs[0]["affiliation"] == [{"name": "Dept. of Medical Physics. Virgen del Rocio University Hospital"}]
    assert cs[1]["name"] == "Davies" and cs[1]["affiliation"] == [{"name": "University of Bremen"}]  # comma split
    assert cs[2]["nameType"] == "Organizational"
    for i in (3, 4, 5, 6, 7, 8):  # real people / traps untouched
        assert cs[i].get("nameType") != "Organizational" and "affiliation" not in cs[i], cs[i]
    assert normalize_affiliation_in_name(rec) is False
    print("OK affiliation-in-name: (a) period/comma split, (b) org-tagged, persons kept")


def test_drop_llm_affiliation_creators():
    from poster_to_json.field_normalize import drop_llm_affiliation_creators
    rec = {"creators": [
        {"name": "Croatian Agency for Agriculture and Food"},      # deposit org -> keep
        {"name": "Institute of Neuroscience, University of X"},     # LLM-added -> drop
        {"name": "Smith, Jane"},
    ]}
    deposit = ["Croatian Agency for Agriculture and Food", "Smith, Jane"]
    assert drop_llm_affiliation_creators(rec, deposit)
    assert [c["name"] for c in rec["creators"]] == ["Croatian Agency for Agriculture and Food", "Smith, Jane"]
    assert drop_llm_affiliation_creators(rec, deposit) is False
    # GUARD: empty/None deposit evidence -> never drop
    r2 = {"creators": [{"name": "Institute of Neuroscience, University of X"}, {"name": "Smith, Jane"}]}
    assert drop_llm_affiliation_creators(r2, []) is False and len(r2["creators"]) == 2
    assert drop_llm_affiliation_creators(r2, None) is False
    print("OK drop-llm-affiliation: LLM-added orgs dropped, empty-deposit guard holds")


def test_normalize_affiliation_names():
    from poster_to_json.field_normalize import normalize_affiliation_names
    rec = {"creators": [
        {"name": "Doe, John", "affiliation": [
            {"name": "Yonsei University; University of California, Davis"},
            {"name": " "}, "&",
            {"name": "王大学"},
        ]},
        {"name": "Roe, Jane", "affiliation": [
            {"name": "University of California, Berkeley, CA, USA"},
            {"name": "Wageningen University and Research"}]},
        {"name": "Kim", "affiliation": [
            {"name": "Inst A; Inst B", "affiliationIdentifier": "https://ror.org/05kytsw45",
             "affiliationIdentifierScheme": "ROR"}]},
        {"name": "Solo", "affiliation": [" ", "'"]},
    ]}
    assert normalize_affiliation_names(rec)
    cs = rec["creators"]
    assert len(cs) == 4
    assert [a["name"] for a in cs[0]["affiliation"]] == [
        "Yonsei University", "University of California, Davis", "王大学"]
    assert len(cs[1]["affiliation"]) == 2                      # comma/and not split
    assert cs[2]["affiliation"][0]["name"] == "Inst A; Inst B"  # ROR-bearing not split
    assert "affiliation" not in cs[3]                          # all-junk removed, creator kept
    assert normalize_affiliation_names(rec) is False           # idempotent
    print("OK affiliation_names: split on ; only, drop no-letter, keep comma/and/ROR")


def test_subjects_drop_letterless_and_split_blobs():
    from poster_to_json.field_normalize import normalize_subjects, split_subject
    rec = {"subjects": [{"subject": "."}, {"subject": "1"}, {"subject": "2"},
                        {"subject": "3D"}, {"subject": "Genomics"}]}
    assert normalize_subjects(rec)
    assert [s["subject"] for s in rec["subjects"]] == ["3D", "Genomics"]
    rec = {"subjects": [{"subject":
        "Extended Reality (XR)  Virtual Reality (VR)  Augmented Reality (AR)  Mixed Reality (MR)"}]}
    assert normalize_subjects(rec)
    assert [s["subject"] for s in rec["subjects"]] == [
        "Extended Reality (XR)", "Virtual Reality (VR)", "Augmented Reality (AR)", "Mixed Reality (MR)"]
    assert split_subject(
        "Extended Reality (XR) Virtual Reality (VR) Augmented Reality (AR) "
        "Mixed Reality (MR) Human-Computer Interaction") == [
        "Extended Reality (XR)", "Virtual Reality (VR)", "Augmented Reality (AR)",
        "Mixed Reality (MR)", "Human-Computer Interaction"]
    assert split_subject("Keywords: genomics, RNA-seq") == ["genomics", "RNA-seq"]
    assert split_subject("Machine Learning") == ["Machine Learning"]
    assert split_subject("Topics in Algebra") == ["Topics in Algebra"]         # not a header
    assert split_subject("Subject-Verb Agreement") == ["Subject-Verb Agreement"]  # header-hyphen FP fixed
    assert split_subject(
        "Business Information Management (incl. Records, Knowledge) not elsewhere classified"
    ) == ["Business Information Management (incl. Records, Knowledge) not elsewhere classified"]
    print("OK subjects: letterless dropped, header/2-space/acronym split, phrases preserved")


def test_title_fallback():
    from poster_to_json.field_normalize import (
        title_is_bad_llm, title_is_reasonable, replace_bad_llm_title)
    for t in ("Aim", "SIP", "DH", "Introduction", "Results", "abstract.",
              "NFDI4Chem will be organizing a workshop on minimum information standards " + "x" * 220):
        assert title_is_bad_llm(t), t
    for t in ("Graphene", "CRISPR", "Effect of Graphene Oxide on Cancer Cell Viability",
              "Demonstration of critical battery metals recovery from spent lithium-ion cells using a green process."):
        assert not title_is_bad_llm(t), t   # legit long title ending in a period is kept
    assert title_is_reasonable("Effect of X on Cancer Cell Viability")
    assert not title_is_reasonable("poster_final_v2.pdf")     # filename
    assert not title_is_reasonable("Untitled Poster")         # merger placeholder (required fix)
    assert not title_is_reasonable("Aim")
    dep = "Minimum Information Standards for Chemistry Data in NFDI4Chem"
    rec = {"titles": [{"title": "Aim", "lang": "en"}]}
    assert replace_bad_llm_title(rec, dep) and rec["titles"][0]["title"] == dep
    assert rec["titles"][0]["lang"] == "en"
    assert replace_bad_llm_title(rec, dep) is False           # idempotent
    good = {"titles": [{"title": "Photocatalytic Water Splitting on TiO2"}]}
    assert replace_bad_llm_title(good, dep) is False          # good LLM title kept
    assert replace_bad_llm_title({"titles": [{"title": "Aim"}]}, "Untitled Poster") is False  # placeholder deposit
    print("OK title_fallback: bad LLM titles -> deposit title; placeholders/filenames rejected")


def test_collapse_multidate_ranges():
    from poster_to_json.field_normalize import collapse_multidate_ranges
    r = {"dates": [{"date": "2017-06-20/2017-06-22/2017-07-25/2017-07-27", "dateType": "Presented"}]}
    assert collapse_multidate_ranges(r) and r["dates"][0]["date"] == "2017-06-20/2017-07-27"
    r2 = {"dates": [{"date": "2024-06-24/2024-06-28/2024-06-28", "dateType": "Presented"}]}
    assert collapse_multidate_ranges(r2) and r2["dates"][0]["date"] == "2024-06-24/2024-06-28"
    r3 = {"dates": [{"date": "2021-05-01/2021-05-01/2021-05-01", "dateType": "Presented"}]}
    assert collapse_multidate_ranges(r3) and r3["dates"][0]["date"] == "2021-05-01"   # dedup -> single
    assert collapse_multidate_ranges({"dates": [{"date": "2020-06-06/2020-06-08"}]}) is False   # 2-part kept
    assert collapse_multidate_ranges({"dates": [{"date": "2020-06-06"}]}) is False              # single kept
    assert collapse_multidate_ranges(r) is False                                                # idempotent
    print("OK collapse_multidate_ranges: 3+ ISO parts -> min/max, dedup -> single")


def test_normalize_version():
    from poster_to_json.field_normalize import normalize_version
    for val in ("https://www.researchgate.net/publication/385782798_x", "www.example.com/v/2",
                "Aktualisierte Version auf Basis von Vierkant et al. (2019). Fassung.",
                "Complete List Of United Airlines(TM) Customer Service", "Call now 1-800-555-1234",
                "18005551234", "   ", "This is the second revised edition of the work"):
        rec = {"version": val}
        assert normalize_version(rec) and "version" not in rec, val
    for val in ("1", "1.0", "v2", "1.2.3", "2019-03", "Version 2", "1.0.0.20190315", "2.3.20201231"):
        rec = {"version": val}
        assert normalize_version(rec) is False and rec["version"] == val, val   # dotted versions kept
    print("OK normalize_version: url/long/spam dropped, dotted/short versions kept")


def test_drop_junk_related_identifiers():
    from poster_to_json.field_normalize import drop_junk_related_identifiers, _relid_is_junk
    for j in ("N/A", "URL", "tax", "NULL", "10.", "ab",
              "https://pubmed.ncbi.nlm.nih.gov/Wentao%20L%2C%20Shuxia",   # %2C -> junk
              "urn:isbn%3AAlbites%20Yanza%2C", "1.Modelling%20Nanomaterial%20Toxicity",  # %20 non-URL
              None, 123, ""):
        assert _relid_is_junk(j), j
    for g in ("10.5281/zenodo.123456", "https://doi.org/10.1234/abc", "urn:isbn:9781234567890",
              "https://example.org/files/Info%20Material/report.pdf"):   # single %20 in a URL -> kept
        assert not _relid_is_junk(g), g
    rec = {"relatedIdentifiers": [
        {"relatedIdentifier": "10.5281/zenodo.1", "relatedIdentifierType": "DOI", "relationType": "References"},
        {"relatedIdentifier": "N/A", "relatedIdentifierType": "DOI"},
        {"relatedIdentifier": "https://pubmed.ncbi.nlm.nih.gov/Wentao%20L%2C", "relatedIdentifierType": "URL"}]}
    assert drop_junk_related_identifiers(rec)
    assert len(rec["relatedIdentifiers"]) == 1 and rec["relatedIdentifiers"][0]["relationType"] == "References"
    assert drop_junk_related_identifiers(rec) is False   # idempotent
    print("OK drop_junk_related_identifiers: placeholder/short/encoded junk dropped, real URLs kept")


def test_drop_junk_descriptions():
    from poster_to_json.field_normalize import drop_junk_descriptions
    rec = {"descriptions": [
        {"description": "This poster presents a real study of X on Y.", "descriptionType": "Abstract"},
        {"description": "*", "descriptionType": "Other"},
        {"description": '{"references": ["Borucki, W. J.", "Smith 2019"]}', "descriptionType": "Other"}]}
    assert drop_junk_descriptions(rec)
    assert [d["description"] for d in rec["descriptions"]] == ["This poster presents a real study of X on Y."]
    assert drop_junk_descriptions(rec) is False   # idempotent
    keep = {"descriptions": [
        {"description": "A" * 4000, "descriptionType": "Abstract"},
        {"description": "本研究探讨了纳米材料在能源存储中的应用。", "descriptionType": "Other"},
        {"description": "{note} this is prose, not JSON at all", "descriptionType": "Other"}]}
    assert drop_junk_descriptions(keep) is False   # long/CJK/brace-prose kept
    print("OK drop_junk_descriptions: punct/short/JSON-blob dropped, prose (incl. CJK) kept")


def test_drop_junk_funding():
    from poster_to_json.field_normalize import drop_junk_funding
    rec = {"fundingReferences": [
        {"funderName": "Agencia Nacional de Promoción de la Investigación, el Desarrollo Tecnológico y la Innovación",
         "awardNumber": "PICT-2019-1234"},                                   # real long agency name -> kept
        {"funderName": "This study was supported by Academy of Finland grants 265966 to GP"},  # ACK -> drop
        {"funderName": "-"},                                                  # no-letter -> drop
        {"funderName": "funded by NIH", "funderIdentifier": "10.13039/100000002"},  # ACK but has id -> kept
        {"funderName": "NSF", "awardNumber": "-"},                            # clear no-alnum awardNumber
        {"funderName": "European Commission", "awardNumber": "874538"}]}      # numeric grant -> kept
    assert drop_junk_funding(rec)
    names = [f["funderName"] for f in rec["fundingReferences"]]
    assert names == ["Agencia Nacional de Promoción de la Investigación, el Desarrollo Tecnológico y la Innovación",
                     "funded by NIH", "NSF", "European Commission"]
    assert "awardNumber" not in rec["fundingReferences"][2]                   # NSF's "-" cleared
    assert rec["fundingReferences"][3]["awardNumber"] == "874538"             # numeric kept
    assert drop_junk_funding(rec) is False                                    # idempotent
    print("OK drop_junk_funding: ACK/no-letter dropped, agency names + numeric grants kept")


def test_clean_conference_junk():
    from poster_to_json.field_normalize import clean_conference_junk
    rec = {"conference": {"conferenceName": "3", "conferenceAcronym": "C",
                          "conferenceStartDate": "2020-06-01"}}
    assert clean_conference_junk(rec)
    assert "conferenceName" not in rec["conference"] and "conferenceAcronym" not in rec["conference"]
    assert rec["conference"]["conferenceStartDate"] == "2020-06-01"           # dates kept
    keep = {"conference": {"conferenceName": "EGU General Assembly", "conferenceAcronym": "EGU2020"}}
    assert clean_conference_junk(keep) is False
    print("OK clean_conference_junk: junk name/acronym cleared, real kept, dates untouched")


def test_drop_junk_sections():
    from poster_to_json.field_normalize import drop_junk_sections
    rec = {"content": {"sections": [
        {"sectionTitle": "Introduction", "sectionContent": "Real content here about the study."},
        {"sectionTitle": "0", "sectionContent": "This section has real body text worth keeping."},  # strip title
        {"sectionTitle": "1", "sectionContent": "*"},                         # both junk -> drop
        {"sectionTitle": "X" * 250, "sectionContent": ""},                    # overlong, no body -> demote
        {"sectionTitle": "Y" * 250, "sectionContent": "real body"}]}}         # overlong + body -> merge (no loss)
    assert drop_junk_sections(rec)
    secs = rec["content"]["sections"]
    assert len(secs) == 4
    assert secs[0]["sectionTitle"] == "Introduction"
    assert "sectionTitle" not in secs[1] and secs[1]["sectionContent"].startswith("This section")
    assert "sectionTitle" not in secs[2] and secs[2]["sectionContent"] == "X" * 250   # demoted
    assert "sectionTitle" not in secs[3] and secs[3]["sectionContent"] == "Y" * 250 + "\n\nreal body"  # merged, nothing lost
    assert drop_junk_sections(rec) is False                                   # idempotent
    print("OK drop_junk_sections: fully-junk dropped, junk title stripped, overlong title demoted (no text loss)")


def test_drop_junk_captions():
    from poster_to_json.field_normalize import drop_junk_captions
    rec = {"imageCaptions": [
        {"caption": "Figure 1: the experimental setup."}, {"caption": "*"}, {"caption": "!!"}],
        "tableCaptions": [{"caption": "-"}]}
    assert drop_junk_captions(rec)
    assert [c["caption"] for c in rec["imageCaptions"]] == ["Figure 1: the experimental setup."]
    assert "tableCaptions" not in rec                                         # emptied -> key dropped
    assert drop_junk_captions(rec) is False                                   # idempotent
    print("OK drop_junk_captions: punctuation/<=2 dropped, real captions kept")


def test_portion4_unicode_preservation():
    """Adversarial-pass regression: non-ASCII values must survive (Asian/Nepali corpus)."""
    from poster_to_json.field_normalize import (
        drop_junk_funding, clean_conference_junk, drop_junk_sections, drop_junk_captions)
    # funding: non-ASCII (fullwidth) award number kept; CJK funder kept
    rec = {"fundingReferences": [{"funderName": "日本学術振興会", "awardNumber": "２０２０"}]}
    assert drop_junk_funding(rec) is False
    assert rec["fundingReferences"][0]["awardNumber"] == "２０２０"
    # conference: CJK name + Greek acronym kept (not stripped to 0 by an ASCII gauge)
    conf = {"conference": {"conferenceName": "電子情報通信学会", "conferenceAcronym": "ΣΥΝ",
                           "conferenceStartDate": "2020-06-01"}}
    assert clean_conference_junk(conf) is False
    assert conf["conference"]["conferenceName"] == "電子情報通信学会"
    # sections: a short (2-char) CJK body is real content -> section kept (junk title stripped)
    sec = {"content": {"sections": [{"sectionTitle": "1", "sectionContent": "方法"},
                                    {"sectionTitle": "2024", "sectionContent": "In 2024 we ran it."}]}}
    assert drop_junk_sections(sec)
    secs = sec["content"]["sections"]
    assert len(secs) == 2                                    # neither dropped
    assert "sectionTitle" not in secs[0] and secs[0]["sectionContent"] == "方法"   # short CJK kept
    assert secs[1]["sectionTitle"] == "2024"                 # numeric title label kept
    assert drop_junk_sections(sec) is False                  # idempotent
    # captions: short CJK caption kept (ASCII <=2 rule does not apply to it)
    cap = {"imageCaptions": [{"caption": "方法"}, {"caption": "m3"}, {"caption": "*"}]}
    assert drop_junk_captions(cap)
    assert [c["caption"] for c in cap["imageCaptions"]] == ["方法"]   # CJK kept, ASCII "m3"/"*" dropped
    print("OK portion4 unicode: non-ASCII award/conference/section/caption values preserved")


def test_conform_to_schema():
    from poster_to_json.field_normalize import conform_to_schema
    rec = {
        "$schema": "x", "titles": [{"title": "T", "titleType": None, "junk": 1}],
        "creators": [{"name": "Doe, J", "givenName": "J", "familyName": "Doe",
                      "orcid": "0000-0002-1694-233X", "degree": "PhD", "email": "a@b.c",
                      "content": {"leaked": "poster"}}],   # extra keys + leaked poster field
        "subjects": [{"subject": "AI", "content": "leak", "conference": "x"}],
        "references": [{"id": 1, "citation": "Smith 2020"}],   # structured extra -> kept
        "acknowledgements": "Thanks to funders",                # structured extra -> kept
        "footer": "page 1 of 2", "webLink": "http://x", "sectionTitle": "Intro",  # junk -> dropped
    }
    assert conform_to_schema(rec)
    c = rec["creators"][0]
    assert set(c.keys()) == {"name", "givenName", "familyName", "nameIdentifiers"}   # stripped to schema
    assert c["nameIdentifiers"][0]["nameIdentifier"] == "https://orcid.org/0000-0002-1694-233X"  # ORCID rescued
    assert set(rec["subjects"][0].keys()) == {"subject"}                              # leaked keys stripped
    assert set(rec["titles"][0].keys()) <= {"title", "titleType"}                     # junk stripped
    assert "references" in rec and "acknowledgements" in rec                          # structured extras kept
    assert "footer" not in rec and "webLink" not in rec and "sectionTitle" not in rec  # junk dropped
    assert conform_to_schema(rec) is False                                            # idempotent
    print("OK conform_to_schema: nested whitelist-strip + ORCID rescue + junk top-level drop")


def test_funding_from_grants():
    from poster_to_json.field_normalize import funding_from_grants, build_funding_from_grants
    fct = {"name": "Fundacao para a Ciencia e Tecnologia", "doi": "10.13039/501100001871"}
    grants = [{"code": "PTDC/ABC/123", "funder": fct, "title": "A great project", "url": "https://ex.org/g1"}]
    rec = {"fundingReferences": [{"funderName": "Hallucinated Foundation"}]}   # LLM contamination
    assert funding_from_grants(rec, grants)
    assert rec["fundingReferences"] == [{
        "funderName": "Fundacao para a Ciencia e Tecnologia",
        "funderIdentifier": "https://doi.org/10.13039/501100001871",
        "funderIdentifierType": "Crossref Funder ID",              # NO schemeUri (corpus convention)
        "awardNumber": "PTDC/ABC/123", "awardTitle": "A great project",
        "awardUri": "https://ex.org/g1"}], rec["fundingReferences"]
    assert funding_from_grants(rec, grants) is False               # idempotent
    # carry over a resolved funderIdentifier when the grant lacks funder.doi
    rec2 = {"fundingReferences": [{"funderName": "NSF",
            "funderIdentifier": "https://doi.org/10.13039/100000001",
            "funderIdentifierType": "Crossref Funder ID"}]}
    assert funding_from_grants(rec2, [{"funder": {"name": "NSF"}, "code": "1"}])
    assert rec2["fundingReferences"][0]["funderIdentifier"] == "https://doi.org/10.13039/100000001"  # carried
    # carry over across a funderName surface variant, matched by awardNumber (adversarial-pass fix)
    rec3 = {"fundingReferences": [{"funderName": "National Science Foundation",
            "funderIdentifier": "https://doi.org/10.13039/100000001", "awardNumber": "1234567"}]}
    assert funding_from_grants(rec3, [{"funder": {"name": "NSF"}, "code": "1234567"}])
    assert rec3["fundingReferences"][0]["funderIdentifier"] == "https://doi.org/10.13039/100000001"
    # funderName falls back to grant.title; awardTitle dropped when it duplicates funderName
    assert build_funding_from_grants([{"title": "Big Grant", "funder": {}}]) == [{"funderName": "Big Grant"}]
    # no grants -> existing untouched (no-clobber)
    keep = {"fundingReferences": [{"funderName": "LLM only"}]}
    assert funding_from_grants(keep, []) is False and keep["fundingReferences"] == [{"funderName": "LLM only"}]
    print("OK funding_from_grants: grants win, carry-over id, no schemeUri, title fallback, no-clobber")


def test_fill_conference_from_meeting():
    from poster_to_json.field_normalize import fill_conference_from_meeting
    # the 937 root-cause bucket: title+place, no parseable date -> name/loc/uri/acronym fill
    r = {}
    m = {"title": "Intl Conf on Poster Science", "place": "Lisbon, Portugal",
         "url": "https://conf.ex/2019", "acronym": "ICPS"}
    assert fill_conference_from_meeting(r, m)
    assert r["conference"] == {"conferenceName": "Intl Conf on Poster Science", "conferenceAcronym": "ICPS",
                               "conferenceLocation": "Lisbon, Portugal", "conferenceUri": "https://conf.ex/2019"}
    assert fill_conference_from_meeting(r, m) is False             # idempotent
    r2 = {}
    fill_conference_from_meeting(r2, {"title": "X Symposium", "dates": "2019-06-12 - 2019-06-14"})
    c = r2["conference"]
    assert (c["conferenceStartDate"], c["conferenceEndDate"], c["conferenceYear"]) == ("2019-06-12", "2019-06-14", 2019)
    # single-year day range must NOT strand the leading day (adversarial-pass fix)
    r5 = {}
    fill_conference_from_meeting(r5, {"title": "Y Conf", "dates": "5 - 7 May 2021"})
    c5 = r5["conference"]
    assert (c5["conferenceStartDate"], c5["conferenceEndDate"]) == ("2021-05-05", "2021-05-07"), c5
    # no-clobber: keep a real existing value, fill the gap
    r3 = {"conference": {"conferenceName": "Real Name"}}
    fill_conference_from_meeting(r3, {"title": "Meeting Title", "place": "Berlin"})
    assert r3["conference"]["conferenceName"] == "Real Name" and r3["conference"]["conferenceLocation"] == "Berlin"
    # nameless meeting (place/dates, junk/placeholder title) -> NO conference (schema needs a name)
    r4 = {}
    assert fill_conference_from_meeting(r4, {"title": "--", "place": "Rome", "dates": "2020-01-01/2020-01-02"}) is False
    assert "conference" not in r4
    assert fill_conference_from_meeting({}, {"title": "Not specified", "place": "Paris"}) is False   # placeholder title
    print("OK fill_conference_from_meeting: date range fixed, no nameless object, placeholder rejected")


if __name__ == "__main__":
    for k, v in sorted(globals().items()):
        if k.startswith("test_"):
            v()
    print("\nAll field-normalize checks passed.")
