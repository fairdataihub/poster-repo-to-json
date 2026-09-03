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

**Latest comes from the repository, but only as of harvest time.** `is_last`
tells us a newer version exists upstream even when we have not harvested it,
which comparing the siblings we hold cannot reveal. The flag is also a snapshot:
Zenodo set it on the record that was newest when we harvested, so a 2023 record
still claims to be latest in a corpus that has since picked up its 2025
successor. Holding a higher sequence in the same family is proof the flag went
stale, so only the highest sequence we hold keeps the repository's own answer.
That preserves both cases at once, and corrected 16 stale flags on the corpus.
Figshare publishes no flag at all; the same rule settles it.

**One version can be more than one file.** A poster re-ingested in a later
harvest batch appears twice in the corpus under the same DOI. Those files are
the same version, not siblings. They collapse to one slot for ordering and
neighbour links, and every file is still annotated. Without this, a record
cross-links to itself and reports a family of two.

## What gets written

Only fields the poster schema already defines. Version linking adds no new
fields and needs no schema change.

```json
"relatedIdentifiers": [
  {"relatedIdentifier": "10.5281/zenodo.7853234",
   "relatedIdentifierType": "DOI",
   "relationType": "IsVersionOf",
   "resourceTypeGeneral": "Text"},
  {"relatedIdentifier": "10.5281/zenodo.7853235",
   "relatedIdentifierType": "DOI",
   "relationType": "IsNewVersionOf",
   "resourceTypeGeneral": "Text"}
]
```

`IsVersionOf` anchors the record to its family. `IsNewVersionOf` points at the
older harvested sibling, `IsPreviousVersionOf` at the newer one. Where the
harvest has a gap the neighbour link skips it rather than inventing a DOI.

The family anchor is a real, resolvable DOI in both repositories. Zenodo
publishes it as the concept DOI. Figshare has no concept DOI, but the article
DOI with the `.vN` suffix removed is registered with DataCite, is findable, and
resolves to the latest version, so it serves the same role:
`10.6084/m9.figshare.21359946.v1` anchors to `10.6084/m9.figshare.21359946`.

A small number of records (23 on the corpus) have no such DOI, mostly
institutional deposits carrying a Handle. Those are grouped internally by a
repository-scoped key such as `figshare:article:21359946`, which is never
written to a poster because it is not an identifier anyone can resolve. Their
siblings still link to each other directly.

Depositor-declared version relations are preserved. Only relations pointing at a
DOI in the record's own computed family are rewritten, that being the exact set
we emit; anything pointing elsewhere is the depositor's and stays. An earlier
draft stripped every version relation before rewriting and destroyed 25 of them,
including record 12681959, which arrived with its own `IsVersionOf`. Records we
assert nothing about are not touched at all, because at that point a stale link
from an old run is indistinguishable from a relation the depositor declared, and
deleting theirs is the worse error.

The top-level `version` field is left alone. It belongs to the depositor, and
Zenodo lets them write anything in it: both records in the worked example hold a
date, and one record in the corpus reads `Posters.science automated`. It cannot
carry ordering, and overwriting it would destroy real metadata. Cleaning up junk
version strings is a field-normalization job, not this one.

### An earlier draft of this feature added a `versionInfo` object

It carried `versionRoot`, `versionSequence`, `isLatestVersion`, `versionCount`
and `versionSource` as plain scalars. It was dropped, because measuring it
against the corpus showed it was almost entirely redundant:

| Field | Recoverable from the existing schema? |
| --- | --- |
| `versionRoot` | Yes, in full: it is the `IsVersionOf` target |
| `isLatestVersion` | Yes, in full: true when no `IsPreviousVersionOf` is present. Checked against every annotated record, with zero counterexamples |
| `versionSequence` | No |
| `versionCount`, `versionSource` | Not consumed by the platform; QA only |

The argument for the object had been that relations cannot express "not the
latest" when the newer sibling was never harvested. That case does not occur:
every record whose repository flag was stale had its successor in the corpus,
which is what made the flag detectably stale in the first place.

That leaves `versionSequence` as the only genuinely unexpressible piece, and it
is not worth a new field. The platform can number a chain itself. The cost is
that a lone upstream-v3 ingests as sequence 1 and would need renumbering if a
later harvest fills the gap, against `@@unique([versionRootId, versionSequence])`.
Sequence is still computed here, and still used to order siblings correctly and
to detect stale latest flags. It is simply not published.

## Mapping to the platform

| poster.json | Prisma column |
| --- | --- |
| `IsVersionOf` target | resolve to the family's row, set `versionRootId` (null on the root itself) |
| no `IsPreviousVersionOf` present | `isLatestVersion` true |
| position in the `IsNewVersionOf` / `IsPreviousVersionOf` chain | `versionSequence` |
| no version relations at all | root row, sequence 1, latest true |

Two things to know before ingesting.

**A family may have no sequence-1 row.** 2,210 records are the only version of
their family we hold, and it is not the first: we have v2 or later and the
earlier versions were never harvested. Their `IsVersionOf` DOI exists upstream
but has no row in our data to point `versionRootId` at. Either the lowest
sequence present acts as the root, or the anchor DOI is stored without a row.

**Numbering from the chain is not the repository's numbering.** A lone
upstream-v3 will number as 1. That is fine until a later harvest brings in v1
and v2, at which point the rows need renumbering against
`@@unique([versionRootId, versionSequence])`. Worth deciding up front whether
that matters enough to store the repository sequence after all.

## Running it

Per-record conversion sets the family key and position, since that is all one
deposit can tell you. Sibling cross-links and the settled Figshare latest-flag
need the whole corpus:

```bash
# Report only
python scripts/post_processing/link_versions.py \
    --corpus /storage/poster-work/pre2025/merged --dry-run

# Link in place, reading version graphs from the raw harvest
python scripts/post_processing/link_versions.py \
    --corpus /storage/poster-work/pre2025/merged \
    --corpus /storage/poster-work/data2025/merged \
    --raw /storage/poster-work/pre2025/metadata \
    --raw /storage/poster-work/data2025/metadata \
    --report versions.csv
```

Pass `--raw` when you have the harvested metadata: Zenodo's version graph lives
there and nowhere else. It accepts a directory of per-record JSON, which is how
the harvest sits on disk, as well as ndjson or a JSON array. It is required: the
poster JSON carries the resulting relations but not the sequence they were
derived from, so a corpus cannot be relinked from its own output.

**Pass every corpus slice that could share a family in one run.** All 16
multi-version families on the corpus span the pre2025 and data2025 batches,
which follows from what a version is: a poster deposited in one year and revised
in a later one has its versions in different harvest batches by construction.
Linking the batches separately finds nothing.

## Result on the corpus

Run on 2026-09-03 against 31,363 posters and 39,471 raw harvested records:

| | |
| --- | ---: |
| Records with a version signal | 31,147 |
| Records without one | 216 |
| Distinct families | 31,119 |
| Families where we hold more than one version | 16 |
| Records in those families | 32 |
| Lone later versions (we hold v2+, earlier never harvested) | 2,210 |
| Stale latest flags corrected | 16 |
| Duplicate files collapsed | 12 |
| Records with no resolvable family DOI | 23 |
| Files written | 2,219 |

The 2,210 lone later versions are the largest group and are not an error. The
harvester picks up the current state of a deposit, not its history, so a poster
revised twice before we ever saw it enters the corpus at version 3 with versions
1 and 2 absent. Recording that is useful: it says the record is not the original
and identifies the family, so a later harvest that picks up the earlier versions
slots them in without renumbering anything.

Sequence distribution of those lone records: 1,698 at v2, 334 at v3, 94 at v4,
44 at v5, and a tail to v9.

The script is idempotent. Relinking after a re-harvest drops sibling pointers
that no longer apply instead of accumulating them, and a record that stops being
part of a family has its annotation removed and its depositor version string
restored.

## What this does not cover

Repository-declared families are a small population: 16 of them on the corpus,
holding 32 records.

Matching on title and first author finds far more. 822 groups covering 1,764
records, of which only 11 groups are ones this feature already linked. Those are
not versions in the repository's sense. They are separate deposits with separate
DOIs, cross-postings to two repositories, the same poster presented at two
conferences a year apart, and some that are simply different posters sharing a
title: the largest group is fourteen Loughborough posters that share a
conference series title and are fourteen distinct pieces of work.

That is a different problem needing a different relation (DataCite has
`IsIdenticalTo` and `IsVariantFormOf`) and a review step, because
title-and-author matching produces false positives that repository signals never
do. Nothing here forecloses it: a record can carry both kinds of relation.

The evidence tiers, the proposed phasing and the open questions are written up
in [DUPLICATE_LINKING_PROPOSAL.md](DUPLICATE_LINKING_PROPOSAL.md) for the team
to decide on.
