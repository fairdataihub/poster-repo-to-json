# Identifier rules

What `identifiers[]` and `relatedIdentifiers[]` may contain on a delivered record,
and how the pipeline enforces it. These rules came out of the 2026-09-30 review of
the blob (missing DOIs, duplicate DOIs, records with several DOIs) and ship in
0.39.11. Version relations are covered in `VERSION_LINKING.md`; front-end caveats
in `FRONTEND_NOTES.md`.

## 1. `identifiers[]` holds only the record's own identifiers

The first DOI in `identifiers[]` is the record's own DOI, the one the repository
minted for this deposit (the merger puts the repository's identifiers first). The
only other DOIs allowed are other DOIs of the same item:

- the deposit DOI from the raw harvest, and
- a DOI that contains the record id, such as the concept and legacy DOIs kept when
  a legacy shared-DOI Figshare family is collapsed (CropTiPS 5483821 keeps
  `10.25909/5483821.v3`, `10.4225/55/59e3f09ff1a4d` and `10.25909/5483821`).

Any other DOI is removed. These come from two places: DOIs read off the poster
text during extraction (journal papers, datasets, an author's other Zenodo
records), and DOIs the depositor listed as related works that were copied into
identifiers. Example: Figshare 10279754 carried four extra DOIs, including
`10.1145/2858036.2858409` and `10.5281/ZENODO.7941533`; it now carries only
`10.26180/5dc7a0713dd4f`.

Removed DOIs are not turned into new relations. The team decided to hold off on
adding references from poster text beyond what the platform's own references
approach produces, so a DOI that was only ever an identifier is dropped, not
moved. A DOI the depositor already listed in `relatedIdentifiers` stays there.

## 2. A record never relates to itself

Some depositors list the poster's own DOI as a related identifier
(`IsCitedBy`, `IsIdenticalTo`, `References`, `Documents`, ... pointing at the
poster itself). That relation carries no information and is dropped.

The record's own DOI is never dropped because of it. Before 0.39.11 the
identifier cleanup in `align_schema` removed every identifier that also appeared
in `relatedIdentifiers`, so a self-relation cost the record its DOI: 28 records
reached the blob with no DOI at all (Zenodo 1292418, 2528673, 13364746, and
others). The cleanup now drops the self-relation first and only then removes
identifiers that leaked in from relations, never the own DOI.

## 3. One delivered record per DOI

A DOI is a unique identifier, so two delivered records never share one.

- **Borrowed from another repository** (0.39.8). A Zenodo deposit that carries a
  DOI minted elsewhere (ResearchGate `10.13140`, Figshare `10.6084`), when a record
  owning that DOI from the other repository is also in the corpus, is the same
  poster deposited twice. The owning repository's record is kept and the Zenodo
  copy is dropped. Example: Zenodo 1196536 carries Figshare 5467180 v3's DOI.
- **Shared by two Zenodo deposits** (0.39.11). When two or more Zenodo deposits
  carry the same borrowed DOI and no other repository owns it, the newest deposit
  (highest record id) is kept and the older ones are dropped. Examples: Zenodo
  14837147 and 14947868 share ResearchGate `10.13140/rg.2.2.21495.53926`, and
  Zenodo 4420001 and 7446785 share `10.13140/rg.2.2.30959.18083`; 14947868 and
  7446785 are kept.
- **Legacy shared-DOI Figshare versions** (0.39.5) are collapsed to one record,
  see `VERSION_LINKING.md`.
- A borrowed DOI only one deposit carries, such as a journal or ResearchGate DOI
  on a single Zenodo record, is left alone.

Both duplicate rules run in `link_versions.py` (`find_borrowed_doi_copies`),
before version linking.

## Where each rule lives

| Rule | Pipeline (new records) | Existing corpus |
|------|------------------------|-----------------|
| Own identifiers only, no self-relations | `align_schema` in `field_normalize.py` | `scripts/post_processing/repair_identifiers.py` |
| Restore a lost DOI from the raw harvest | not needed once `align_schema` is fixed | `repair_identifiers.py` |
| One record per DOI | `find_borrowed_doi_copies` in `version_linking.py` | `link_versions.py` |

`repair_identifiers.py` reads each record's deposit DOI from
`metadata/<repo>/<id>.json`. Older Figshare versions kept beside the latest one
(`903710.v1_complete.json`) have their own DOI and link to the latest version, so
they get no deposit DOI from the harvest; only the first-DOI and record-id rules
apply to them. The script is idempotent and has a `--dry-run` mode.

## 2026-10-01 repair of the blob

| Change | Records |
|--------|---------|
| DOI restored | 28 |
| Self-relations dropped | 30 (the 28 above, plus Zenodo 11442700 and 17911503) |
| Records with extra DOIs removed | 11 (18 DOIs) |
| Duplicate Zenodo deposits dropped | 2 (14837147, 4420001) |
| CropTiPS 5483821 | unchanged, by design |

Tests: `tests/test_identifier_rules.py`, each case taken from a record in the
review.
