# Version linking for auto-indexed posters

## The problem

Auto-indexing treats every repository record as its own poster. When a depositor
publishes a poster and then publishes a corrected version of it, Zenodo and
Figshare both keep the original and mint a new record. Our harvest picks up
both, and the corpus shows two posters with no relationship between them.

The platform gained a versioning model in posters-science PR #49: versions are
kept as separate rows anchored to the first one, with `versionRootId`,
`versionSequence` and `isLatestVersion` on each row, and discovery surfaces only
the latest. This document covers the matching work on the indexing side, so the
records we hand over already carry the family structure the platform needs.

We keep every version. Nothing is merged or deleted.

## What counts as a version

A repository version family is one the repository itself declares. That is a
narrower thing than "the same poster appears twice", and the distinction matters
because only the first kind can be linked without guessing.

**Zenodo** groups versions under a concept record. Every record carries
`conceptrecid` and usually `conceptdoi`, plus a version graph:

```json
{
  "id": 17435084,
  "doi": "10.5281/zenodo.17435084",
  "conceptrecid": "10572542",
  "conceptdoi": "10.5281/zenodo.10572542",
  "metadata": {
    "relations": {
      "version": [
        {"index": 2, "is_last": true,
         "parent": {"pid_type": "recid", "pid_value": "10572542"}}
      ]
    }
  }
}
```

**Figshare** keeps one stable article `id` across versions and mints a DOI per
version ending in `.vN`, with a 1-based integer `version` field. There is no
concept DOI and no equivalent of `is_last`.

Signal coverage across the 39,471 harvested records:

| Signal | Records | Coverage |
| --- | ---: | ---: |
| Zenodo `conceptrecid` | 28,545 | 100% of Zenodo |
| Zenodo `conceptdoi` | 27,125 | 95% of Zenodo |
| Figshare `version` | 10,926 | 100% of Figshare |
| Figshare `.vN` DOI | 10,580 | 97% of Figshare |

Coverage is effectively total, so the family key is never guessed.

## Two rules that drive the design

**Sequence comes from the repository, not from our ordering.** Zenodo's `index`
is 0-based and counts the whole family as Zenodo knows it, while our harvest can
hold only part of it. Concept 10572542 is the worked example: it has at least
three versions, and we harvested index 1 and index 2 but never index 0. Those
two records are version 2 and version 3. Renumbering them locally to 1 and 2
would assert that the second version is the original, and would break the
platform's `@@unique([versionRootId, versionSequence])` on the next harvest that
picks up the missing one. We store `index + 1` and leave the hole.

**Latest comes from the repository too.** `is_last` tells us a newer version
exists upstream even when we have not harvested it. Comparing the siblings we
hold cannot reveal that. Figshare gives no such flag, so there the highest
harvested version is the best available answer and is settled across the corpus
rather than per record.

## What gets written

Two representations of the same facts, because they serve different readers.

**DataCite relations**, in `relatedIdentifiers`, for anyone consuming the poster
JSON as DataCite:

```json
"relatedIdentifiers": [
  {"relatedIdentifier": "10.5281/zenodo.10572542",
   "relatedIdentifierType": "DOI",
   "relationType": "IsVersionOf",
   "resourceTypeGeneral": "Text"},
  {"relatedIdentifier": "10.5281/zenodo.13168995",
   "relatedIdentifierType": "DOI",
   "relationType": "IsNewVersionOf",
   "resourceTypeGeneral": "Text"}
]
```

`IsVersionOf` anchors the record to the family. `IsNewVersionOf` points at the
older harvested sibling, `IsPreviousVersionOf` at the newer one. Where the
harvest has a gap the neighbour link skips it rather than inventing a DOI.

Relations alone cannot express everything, which is why there is a second form.
A record that Zenodo says is not the latest, whose newer sibling we have not
harvested, has no DOI to point at and no way to say "not latest" in DataCite.

**`versionInfo`**, a plain object the ingest can read straight into its columns:

```json
"versionInfo": {
  "versionRoot": "10.5281/zenodo.10572542",
  "versionRootType": "DOI",
  "versionSequence": 3,
  "isLatestVersion": true,
  "versionCount": 2,
  "versionSource": "zenodo-relations",
  "repositoryVersion": "2025-10-24"
}
```

`versionRoot` is the concept DOI when one exists. When it does not, it is a
repository-scoped key (`zenodo:recid:10572542`, `figshare:article:21359946`).
Those keys group correctly but are not resolvable identifiers, so they appear
here only and never as a `relatedIdentifier`.

`versionCount` is how many members of the family we hold, which is not
necessarily how many exist upstream. `versionSource` records which signal
produced the family, for QA.

`repositoryVersion` preserves the depositor's own version string. Zenodo lets
depositors write anything in `metadata.version` and dates are common (both
records in the worked example use one), so it cannot drive ordering. The
top-level `version` field carries the positional sequence instead.

The schema already permits all of this: `relatedIdentifiers` accepts the four
DataCite version relations, and the root object is `additionalProperties: true`.
No schema change is required, though `versionInfo` is worth documenting in
[poster-json-schema](https://github.com/fairdataihub/poster-json-schema).

## Mapping to the platform

| poster.json | Prisma column |
| --- | --- |
| `versionInfo.versionRoot` | resolve to the family's row, set `versionRootId` (null on the root itself) |
| `versionInfo.versionSequence` | `versionSequence` |
| `versionInfo.isLatestVersion` | `isLatestVersion` |
| absent `versionInfo` | root row, sequence 1, latest true |

Sequences can have gaps where a version was never harvested. That is compatible
with `@@unique([versionRootId, versionSequence])` and is the point: filling the
gap later does not renumber anything that already exists.

## Running it

Per-record conversion sets the family key and position, since that is all one
deposit can tell you. Sibling cross-links and the settled Figshare latest-flag
need the whole corpus:

```bash
# Report only
python scripts/post_processing/link_versions.py --corpus /storage/poster_corpus --dry-run

# Link in place, reading version graphs from the raw harvest
python scripts/post_processing/link_versions.py \
    --corpus /storage/poster_corpus \
    --raw /storage/harvest/zenodo.ndjson \
    --raw /storage/harvest/figshare.ndjson \
    --report versions.csv
```

Pass `--raw` when you have the harvested metadata: Zenodo's version graph lives
there and nowhere else. Without it the script falls back to `versionInfo`
already on the poster JSON, which works for corpora converted with 0.39.0 or
later.

The script is idempotent. Relinking after a re-harvest drops sibling pointers
that no longer apply instead of accumulating them, and a record that stops being
part of a family has its annotation removed and its depositor version string
restored.

## What this does not cover

Repository-declared families are a small population. Grouping the harvested
records by concept id finds 19 families holding 38 records.

Matching on title and first author across the merged corpus finds far more: 737
groups covering 1,579 records, of which 686 are within one repository and 51
span Zenodo and Figshare. Those are not versions in the repository's sense.
They are separate deposits with separate DOIs, cross-postings to two
repositories, or the same poster presented at two conferences a year apart, and
some are simply different posters that share a title.

Linking them is a worthwhile separate problem with a different shape. It needs
a different relation (DataCite has `IsIdenticalTo` and `IsVariantFormOf`), and
it needs a review step, because title-and-author matching produces false
positives that the repository signals never do. Nothing here forecloses it: a
record can carry both kinds of relation.
