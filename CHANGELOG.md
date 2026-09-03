# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.39.0] - 2026-09-03

### Added
- **Version linking for auto-indexed posters** (`src/poster_to_json/version_linking.py`).
  Auto-indexing harvests each repository version as its own record, so a poster deposited
  twice as v1 and v2 appeared in the corpus as two unrelated posters. Every version is still
  kept; they are now identified as a family and linked, so the platform can populate the
  versioning model added in posters-science PR #49 (`versionRootId` / `versionSequence` /
  `isLatestVersion`).

  **Only fields the poster schema already defines are written.** No new fields, no schema
  change. Each record in a family gets `relatedIdentifiers` entries using the DataCite version
  relations: `IsVersionOf` pointing at the family's DOI, and `IsNewVersionOf` /
  `IsPreviousVersionOf` pointing at the harvested siblings. The platform reads its three
  columns straight off those: the root from the `IsVersionOf` target, `isLatestVersion` from
  the absence of an `IsPreviousVersionOf`, and the sequence from position in the chain.

  The family anchor is a real resolvable DOI in both repositories. Zenodo publishes it as the
  concept DOI; Figshare has no concept DOI, but the article DOI with the `.vN` suffix removed
  is registered with DataCite and resolves to the latest version, so it serves the same role.
  The 23 records with neither are grouped by an internal repository-scoped key that is never
  written to a poster.

  `version` is never touched. It is the depositor's own statement, Zenodo lets them put
  anything in it (dates are common, and one corpus record reads "Posters.science automated"),
  and it cannot carry ordering. Cleaning up junk version strings is a field-normalization job.

  Sequence comes from the repository, never from local ordering. Zenodo's
  `relations.version[].index` counts the whole family and our harvest has gaps: concept
  10572542 gives us index 1 and index 2 while index 0 was never indexed. The sequence orders
  siblings and detects stale flags; it is not published.

  A repository's latest-flag is only true as of harvest time, and our stored metadata is a
  snapshot. A record harvested while it was newest keeps claiming to be latest, so holding a
  higher sequence in the same family is treated as proof the flag went stale. This corrected
  16 flags.

  One version can be more than one file, when a poster was re-ingested in a later harvest
  batch under the same DOI. Those files are the same version, not siblings: they collapse to
  one slot for ordering and neighbour links, and every file is still annotated.

  Records that are the only known version of their family, at sequence 1 with nothing newer
  upstream, are left byte-identical.

  Depositor-declared version relations are preserved. Only relations pointing at a DOI in the
  record's own computed family are rewritten, that being the exact set we emit. An earlier
  draft stripped every version relation before rewriting, which destroyed 25 depositor-declared
  ones (record 12681959 arrived with its own `IsVersionOf`). Verified against the pre-run
  backup: 2,219 records changed, 0 lost a relation.

- **`scripts/post_processing/link_versions.py`** links families across an existing corpus,
  which per-record conversion cannot do because it sees one deposit at a time. `--raw` is
  required (the version graph exists only in the raw harvest) and accepts a directory of
  per-record JSON as well as ndjson. Supports `--dry-run`, `--out` and `--report`, and is
  idempotent.

  `--corpus` is repeatable and every slice that could share a family must be in one run. This
  is not a convenience: all 16 multi-version families on the corpus span the pre2025 and
  data2025 harvest batches, because a poster deposited in one year and revised in a later one
  has its versions in different batches by construction. Linking the batches separately finds
  nothing.

### Changed
- **`version` is a deposit-only field in the merger.** Which version of a deposit a poster is
  is something only the repository knows; a version string read off the poster face is
  unrelated, so extraction can no longer contribute it.

### Corpus run (2026-09-03)

Run against `/storage/poster-work/{pre2025,data2025}/merged` on hpcf, 31,363 posters against
39,471 raw harvested records:

```
records with a version signal  : 31,147   (216 without)
distinct families              : 31,119
families with >1 version held  :      16   (32 records, all spanning both batches)
lone later versions            :   2,210   (we hold v2+, earlier versions never harvested)
stale latest flags corrected   :      16
duplicate files collapsed      :      12
records with no family DOI     :      23
files written                  :   2,219
```

Verified idempotent on a second pass (0 files written) and spot-checked against the live
Zenodo API.

### Notes
- An earlier draft of this feature wrote a `versionInfo` object holding `versionRoot`,
  `versionSequence`, `isLatestVersion`, `versionCount` and `versionSource`. It was dropped
  before release. `versionInfo` is not in the poster schema, and measuring it against the
  corpus showed it was almost entirely redundant: `versionRoot` is the `IsVersionOf` target,
  and `isLatestVersion` is derivable from the absence of an `IsPreviousVersionOf` on all
  annotated records with zero counterexamples. The stated justification for the object,
  that relations cannot express "not the latest" when the newer sibling was never harvested,
  turned out not to occur in the data. Only `versionSequence` is genuinely unexpressible, and
  the platform can number a chain itself. Trade-off: a lone upstream-v3 ingests as sequence 1
  and needs renumbering if a later harvest fills the gap.

- Scope is repository-declared version families only. Posters deposited twice under separate
  DOIs, and posters cross-posted to both Zenodo and Figshare, are duplicates rather than
  versions and are not touched here. That population is larger (822 groups covering 1,764
  records) and cannot be resolved without judgement: the largest group is fourteen different
  Loughborough posters sharing a conference series title. See
  `docs/DUPLICATE_LINKING_PROPOSAL.md` for the evidence tiers and the open questions, which
  need a team decision before anything is built.

## [0.38.1] - 2026-07-14

### Fixed
- **`restore_blocked_content` re-conforms the schema after re-attaching.** The re-attached
  `researchField` is raw extraction, so restored records now run `align_schema` to lift it to
  an OpenAlex domain and re-mirror `domain` (previously left non-conformant / mirror-stale).

## [0.38.0] - 2026-07-14

### Changed
- **License enforcement is now a default step of the pipeline.** `MetadataMerger.merge`
  classifies each record and strips poster-derived content (sets `_license_blocked`) for any
  non-open license (blocked or unknown/unlisted -> default-deny), via `enforce_license`. No
  poster leaves the merge with content it is not licensed to redistribute. License logic
  moved to `src/poster_to_json/license_policy.py` (single source of truth; the
  scripts/post_processing shim re-exports it). Disable with `MetadataMerger.ENFORCE_LICENSE = False`.

## [0.37.5] - 2026-07-14

### Added
- **`correct_export.py`**: produces a license-compliant version of a platform DB ndjson
  export -- recovers dropped `rightsList` from our corpus (by the record's own Zenodo/
  Figshare id), reclassifies with the normalized policy, and strips content + blanks the
  thumbnail for non-open licenses via `strip_extracted_content`.

## [0.37.4] - 2026-07-14

### Fixed
- **License classifier normalizes format variants** (`license_policy.classify_license`):
  license strings are now folded fair-ly-style (strip case/spacing/punctuation) and
  resolved through an alias map before whitelist/blocklist matching, so `CC BY 4.0`,
  `CC0`, `cc-by`, `Apache 2.0`, `cc-by-3.0-us` classify correctly instead of falling to
  `unknown`/blocked. Prevents wrongly stripping ~6k open posters.

## [0.37.3] - 2026-07-14

### Added
- **Universal exact-collapse pass** (`collapse_exact.py`): merges field values that are
  identical after aggressive normalization (strip diacritics, casing, and all whitespace /
  punctuation down to alphanumerics), catching stray-formatting duplicates -- double / non-
  breaking / zero-width spaces, casing, hyphenation (`Lyon,  France`, `PolitecnicodiTorino`,
  `DigitalHumanities`). Deterministic and conservative: only byte-identical alphanumeric
  content merges, so distinct entities (UC San Diego vs UC Berkeley) are never collapsed.
  Runs for publisher / funder / affiliation / subject / location.

### Fixed
- **UC department-level cross-campus mis-merges** (`fix_uc_crosscampus.py`): 3 long
  "Department of X, University of California, <campus>" strings had merged across campuses
  (department name dominated the embedding; no ROR to split on). Restored from the pre-merge
  snapshot (4 records). Clean campus names were unaffected.

## [0.37.2] - 2026-07-11

### Added
- **Conservative subject surface-merge** (`build_subject_surface_map.py`): folds only
  surface variants (case, hyphen/punctuation, light plural stem) so `machine learning`
  / `Machine Learning` / `machine-learning` collapse, while ANZSRC controlled-vocabulary
  labels ("... not elsewhere classified") and distinct concepts are preserved. Unicode-safe
  (non-Latin scripts are not stripped to an empty key).
- **`build_synlustre.py --min-freq`**: only cluster terms at/above a frequency, making very
  large fields (subjects, ~97K terms) tractable at full dimension without PCA.

### Fixed
- **Conference-location geocoding excludes virtual locations.** `conference_location_geocode.py`
  no longer geocodes pure-virtual strings ("Online", "Virtual", "on-line", "Remote", ...),
  which Nominatim fuzzy-matched to real cities (Online -> Montpellier, Virtual -> Moscow).
  Strings that also name a city ("Berlin / online") are unaffected.

## [0.37.1] - 2026-07-10

### Fixed
- **`researchField` / `domain` schema conformance.** `poster_schema.json` requires
  `researchField` to be one of the four OpenAlex top-level domains (Health / Life /
  Physical / Social Sciences). `align_schema` now lifts field-level and
  foreign-language values (Computer Science, Arts and Humanities, Geowissenschaften,
  …) up to their parent domain and omits unmappable ones, then mirrors the result to
  `domain` (the key the auto-index ingestion reads). `scripts/post_processing/
  fix_research_field_domain.py` backfills this into an already-built corpus (1,085
  records corrected in the delivered set).

## [0.37.0] - 2026-07-10

### Added
- **Acronym / short-token holdout in synonym clustering.** Acronyms and short
  (`< 4` char) tokens are held out of the HDBSCAN clustering that collapses
  `publisher` / `funder` / `affiliation` / `subject` variants, so they no longer
  act as spurious join keys (e.g. `ZHAW → Z`, `LUH → HU`) and unrelated field
  values sharing a short string are no longer merged.
- **ROR-based cluster splitting for institution fields.** A synonym cluster that
  spans two or more distinct ROR ids is partitioned back into per-ROR
  sub-clusters before a canonical is chosen, so lexically-close but distinct
  institutions (e.g. *University of Washington* vs *Washington University*) are
  not collapsed together.
- **Geocoding-based conference-location normalization.** Conference locations are
  normalized by geocoding (Nominatim) and grouping on `(country, city)`, yielding
  canonical `"City, Country"` names and keeping genuinely different places apart
  instead of merging free-text variants.
- **2025 publisher backfill + rigor cleaning.** Missing 2025 publishers are
  sourced (Figshare custom field, else a local LLM from poster content), then run
  through a deterministic rigor pass that drops hedge phrases, author citations,
  bare-generic and placeholder junk to the repository fallback.
- **V-measure validation harness.** Added `validate_vmeasure.py`, which scores
  `publisher` / `funder` / `affiliation` / `subject` synonym-clustering quality
  against a gold set with the V-measure metric across an epsilon sweep, to guard
  against over-merge regressions and tune `cluster_selection_epsilon` per field.

### Fixed
- **Single-character and string-shaped affiliations.** Single-character junk
  affiliations are dropped, and bare-string affiliations are coerced to proper
  list-of-object form so the list-guarded normalizers no longer skip them.
- **Short-acronym affiliation mis-merges.** Affiliations previously collapsed onto
  a wrong short canonical are restored from the pre-merge snapshot.

[0.37.0]: https://github.com/FAIRDataIHub/poster-repo-to-json/releases/tag/v0.37.0
