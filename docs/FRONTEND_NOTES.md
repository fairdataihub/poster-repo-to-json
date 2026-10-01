# Front-end notes: consuming the indexed poster JSON

Caveats for the platform / front-end when displaying auto-indexed poster records.
These are properties of the data we deliver to the blob, not bugs to work around
silently. Schema field mapping lives in the auto-indexing field-coverage sheet;
version handling in `VERSION_LINKING.md`; identifier rules in `IDENTIFIER_RULES.md`;
license handling in `LICENSE_POLICY.md`.

## Identifiers

- `identifiers[]` carries the poster's **DOI** (`identifierType: "DOI"`) and, for
  institutional Figshare deposits that mint no DOI, a **Handle**
  (`identifierType: "Handle"`). The DOI is the primary, resolvable identifier.
- **The first DOI is the record's own.** `identifiers[]` holds only identifiers of
  the poster itself; DOIs of papers, datasets or other works it cites belong in
  `relatedIdentifiers[]`, and a record never lists itself there. More than one DOI
  appears only for a collapsed legacy family (below). From 0.39.11 the pipeline
  enforces this and the blob was repaired on 2026-10-01 (28 records had lost their
  DOI, 11 carried extra DOIs). See `IDENTIFIER_RULES.md`.
- **Legacy: a bare numeric id typed `"Other"`.** Records produced before
  poster-repo-to-json 0.39.6 also carry the raw Zenodo record id / Figshare article
  id as a second identifier typed `"Other"` (about 22,780 Zenodo and 9,894 Figshare
  records on the current frozen blob). It is redundant with the DOI, which embeds it
  (`10.5281/zenodo.<id>`, `10.NNNNN/<id>.vN`). From 0.39.6 the pipeline no longer
  emits it, so it will disappear from records on the next re-push; until then the
  front-end still sees it. Do not display the bare numeric id as-is. If you need the
  repository id, take it from the DOI rather than the `Other` entry.

- **Limitation: a DOI can resolve to a related paper.** A few Zenodo deposits
  reused an existing DOI instead of getting their own, sometimes the DOI of a
  paper the poster accompanies. We keep it so every record has a DOI. See
  `IDENTIFIER_RULES.md`.

## Linking to the source repository

- **Zenodo:** link to the record via the DOI, or `https://zenodo.org/records/<id>`.
- **Figshare:** institutional Figshare records do not always appear in the main
  Figshare search. When a Handle is present, use its resolved URL. Otherwise resolve
  the article id (from the DOI) through the official Figshare API and redirect to the
  repository's public HTML page.

## Legacy shared-DOI Figshare versions

Some older ANDS-minted Figshare articles (`10.4225` / `10.25909` style) registered a
single DOI across every version. Where the versions are the same poster, the pipeline
collapses them to one record on the current per-version DOI and keeps the older DOIs
as additional entries in `identifiers[]`. So a single record can legitimately carry
more than one DOI: the first is current, the rest (a legacy shared DOI, a concept
DOI) are retained so old links still resolve. Display or link on the first DOI.

## Titles

Use the poster's own `titles[0].title`. For some Figshare deposits the repository
title describes the overarching project the poster belongs to rather than the poster
itself (e.g. poster 31178). Our policy is to keep the poster's title; do not
substitute a repository project title.

## Version fields

- Read the version graph from `relatedIdentifiers`, not from the `version` string:
  `versionRootId` from the `IsVersionOf` target, `isLatestVersion` from the absence of
  an `IsPreviousVersionOf`, and the sequence from position in the
  `IsNewVersionOf` / `IsPreviousVersionOf` chain. See `VERSION_LINKING.md`.
- The top-level `version` field is the depositor's own version string when they gave
  one, otherwise the repository's version number: Figshare's own integer, or for
  Zenodo the concept position (`index + 1`). Every record with a version position has
  one, so a lone poster from either repository shows `1`. Never order versions by
  this field; it can be a free-text string such as a date. Order by the relation chain.
- A family may have no sequence-1 record on the blob when we hold only later versions
  (earlier ones were never harvested). The `IsVersionOf` DOI still resolves upstream.

## License-blocked records are metadata-only

Records whose license does not permit redistributing derived content are kept as
metadata only, under a default-deny rule (see `LICENSE_POLICY.md`). These have no
poster file and no thumbnail. Expect a missing image for them and render a metadata
-only card rather than treating the absent thumbnail as an error.

## Duplicates

The corpus can still contain duplicate indexed entries (the same poster deposited
more than once, or cross-posted to two repositories). These are a known, separate
class from versions and are handled by tombstoning on the platform side, not by the
indexing pipeline. See `DUPLICATE_LINKING_PROPOSAL.md` for the tiers and rationale.

One kind is removed by the pipeline itself (0.39.8): a Zenodo deposit whose DOI
was borrowed from a record in another repository that we also deliver (for
example Zenodo 1196536, which carries Figshare 5467180 v3's DOI). The owning
repository's record is kept and the Zenodo copy is not delivered. From 0.39.11,
when two Zenodo deposits share one borrowed DOI and no other repository owns it,
the newest deposit is kept (Zenodo 14947868 over 14837147, 7446785 over 4420001).
The dropped records were never platform-only; if they were loaded earlier, they
are stale rows the loader does not delete. See `IDENTIFIER_RULES.md`.
