# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.39.0] - 2026-09-02

### Added
- **Version linking for auto-indexed posters** (`src/poster_to_json/version_linking.py`).
  Auto-indexing harvests each repository version as its own record, so a poster deposited
  twice as v1 and v2 appeared in the corpus as two unrelated posters. Every version is still
  kept; they are now identified as a family and linked. Mirrors the versioning model added to
  the platform in posters-science PR #49 (`versionRootId` / `versionSequence` /
  `isLatestVersion`), so the ingest can populate those columns without re-deriving anything.

  Each record in a family gets:
  - `relatedIdentifiers` entries using the DataCite version relations: `IsVersionOf` pointing
    at the Zenodo concept DOI, and `IsNewVersionOf` / `IsPreviousVersionOf` pointing at the
    harvested siblings.
  - A `versionInfo` object holding the same facts as scalars: `versionRoot`,
    `versionRootType`, `versionSequence`, `isLatestVersion`, `versionCount` and
    `versionSource`.

  `version` is never written. That field is the depositor's own statement, Zenodo lets them
  put anything in it (dates and free text are both common), and the machine-readable position
  belongs in `versionInfo.versionSequence` where the platform reads it. Overwriting `version`
  would destroy real metadata to duplicate something we already publish.

  Sequence and latest-flag come from the repository, never from local ordering. Zenodo's
  `relations.version[].index` counts the whole family, and our harvest can have gaps: concept
  10572542 gives us index 1 and index 2 while index 0 was never indexed, so those records are
  version 2 and version 3, not 1 and 2. Records that are the only known version of their
  family are left byte-identical.

  A repository's latest-flag is only true as of harvest time, and our stored metadata is a
  snapshot. A record harvested while it was newest keeps claiming to be latest, so holding a
  higher sequence in the same family is treated as proof the flag went stale. Only the highest
  sequence we hold keeps the repository's own answer, which is what preserves the case where
  Zenodo reports a newer version we never indexed. This corrected 16 stale flags on the corpus.

  One version can also be more than one file, when a poster was re-ingested in a later harvest
  batch under the same DOI. Those files are the same version rather than siblings: they
  collapse to one slot for sequencing, neighbours and `versionCount`, and every file still gets
  annotated.

- **`scripts/post_processing/link_versions.py`** links families across an existing corpus,
  which per-record conversion cannot do because it sees one deposit at a time. Reads version
  graphs from the raw harvest (`--raw`, accepting a directory of per-record JSON as well as
  ndjson), falls back to `versionInfo` already on the poster JSON, and supports `--dry-run`,
  `--out` and `--report`. Idempotent: relinking converges rather than accumulating stale
  sibling pointers.

  `--corpus` is repeatable and every slice that could share a family must be in one run. This
  is not a convenience: all 16 multi-version families found on the corpus span the pre2025 and
  data2025 harvest batches, because a poster deposited in one year and revised in a later one
  has its versions in different batches by construction. Linking the batches separately finds
  nothing.

### Corpus run (2026-09-02)

Run against `/storage/poster-work/{pre2025,data2025}/merged` on hpcf, 31,363 posters against
39,471 raw harvested records:

```
records with a version signal : 31,147   (216 without)
distinct families             : 31,119
families with >1 version held :      16   (32 records, all spanning both batches)
lone later versions           :   2,210   (we hold v2+, earlier versions never harvested)
stale latest flags corrected  :      16
duplicate files collapsed     :      12
files written                 :   2,242
```

Verified idempotent on a second pass (0 files written) and spot-checked against the live
Zenodo API.

### Changed
- **`version` and `versionInfo` are deposit-only fields in the merger.** Which version of a
  deposit a poster is, and what family it belongs to, is something only the repository knows.
  A version string read off the poster face is unrelated and would corrupt the ordering, so
  extraction can no longer contribute either field.

### Notes
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
