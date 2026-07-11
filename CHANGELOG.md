# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
