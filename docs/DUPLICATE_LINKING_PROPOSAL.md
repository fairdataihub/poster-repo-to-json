# Linking duplicate posters: proposal for team discussion

Status: **not implemented, needs a team decision.**

Version linking (see [VERSION_LINKING.md](VERSION_LINKING.md), shipped in 0.39.0)
handles families the repository itself declares. It links 16 families holding 32
records. That is the whole population where the repository tells us two records
are the same poster, and it can be done without judgement.

Separately, the same poster often appears in the corpus more than once without
the repository saying so. This document sets out what those look like, why the
obvious fix is unsafe, and a tiered approach we could take instead. It needs a
decision from the team, because the risky part is not the code.

## Size of the problem

Grouping the 31,363 merged posters by normalized title plus first author family
name, over the 30,860 with a title long enough to be meaningful:

| | Groups | Records |
| --- | ---: | ---: |
| Duplicate groups found | 822 | 1,764 |
| Already linked as repository version families | 11 | 24 |
| Remaining | 811 | 1,740 |

Group sizes: 744 pairs, 58 triples, 14 quads, and a tail running to one group of
fourteen.

That tail is the reason this is not a one-line fix.

## Why title and author matching is not enough on its own

The largest group is fourteen Loughborough records, all 2020, all with the same
first author, all titled:

```
Developing Knowledge and Capacity in Water and Sanitation
  10.17028/rd.lboro.12739418.v1    10.17028/rd.lboro.12765437.v1
  10.17028/rd.lboro.12738752.v1    10.17028/rd.lboro.12765491.v1
  10.17028/rd.lboro.12765242.v1    10.17028/rd.lboro.12745130.v1
  10.17028/rd.lboro.12745142.v1    10.17028/rd.lboro.12738692.v1
  10.17028/rd.lboro.12738767.v1    10.17028/rd.lboro.12765218.v1
  ... and four more
```

These are fourteen different posters from one conference programme that share a
series title. Collapsing them would delete thirteen real posters from discovery.
Any rule keyed on title and author alone does exactly that.

## The duplicates are four different things

Sorted by how strong the evidence is:

### Tier A: the same record harvested twice (7 groups, 14 records)

Identical DOI, two files in the corpus. A poster harvested in one batch and
re-harvested in a later one:

```
Constraining sea level variations in the Last Interglacial by modeling ...
  10.6084/m9.figshare.21359946.v1   (filed under zenodo)
  10.6084/m9.figshare.21359946.v1   (filed under figshare)
```

Not two posters and not two versions. A harvest artifact, provable by DOI
equality, with no judgement involved. The version linker already collapses 12
files of this kind where a version signal was present, but does not look for
them in general.

### Tier B: distinct DOIs, identical abstract (246 groups, 512 records)

```
TUG-RSE: Pulling Students into Research Software Engineering
  10.5281/zenodo.8140579
  10.5281/zenodo.8140593
  10.25080/gerudo-f2bc6f59-023
```

Same title, same first author, byte-identical abstract, and in this case a
third deposit under the SciPy proceedings prefix. Deposited more than once,
sometimes deliberately (repository plus proceedings), sometimes by accident.

### Tier C: distinct DOIs, identical full author list, groups of 2 to 3 (325 groups, 670 records)

```
Retrieval Study of Brown Dwarfs Across the L-T Sequence
  10.5281/zenodo.7342237
  10.5281/zenodo.6538084
```

Every author matches, not just the first. Strong but not conclusive: a research
group can present related work under a reused title.

### Tier D: title and first author only (244 groups, 568 records)

```
Accelerating Oxygen Reduction Catalysts through Preventing Poisoning ...
  10.5281/zenodo.1065378
  10.5281/zenodo.1065407
  10.5281/zenodo.1065440
```

The weakest evidence, and where the fourteen-member Loughborough group sits.
Some of these are real duplicates, some are conference series, some are
different posters from one lab with a house style in titling. This tier cannot
be resolved automatically.

Across same-repository groups, 710 share a DOI prefix and 22 do not, so most
duplication happens within a single institution's repository rather than across
institutions.

## What we would write, if we do this

Nothing here needs a new schema field. DataCite already has the vocabulary, and
using it keeps duplicates clearly distinct from versions:

| Relation | Meaning | Fits |
| --- | --- | --- |
| `IsIdenticalTo` | same resource, different identifier | Tier A, Tier B |
| `IsVariantFormOf` / `IsOriginalFormOf` | same work, different form | Tier C |

Both are already in the schema's `relationType` enum, and a record can carry
these alongside its version relations without conflict.

Deliberately **not** proposed: reusing `versionInfo`. A duplicate deposit is not
version N of anything, and forcing it into `versionSequence` would put a
fabricated ordering on records where no ordering exists. The platform's
`isLatestVersion` would then hide real posters from discovery on the strength of
a guess.

## Proposed approach

Split by evidence tier and stop where the evidence stops.

**Phase 1: Tier A, automatic.** Collapse on identical DOI. Zero judgement, 7
groups. Worth doing regardless of what we decide about the rest, and arguably a
harvest bug to fix upstream rather than a linking feature.

**Phase 2: Tiers B and C, automatic linking with `IsIdenticalTo` where the
abstract matches exactly, and a generated review list for the rest.** Around 571
groups. Linking is reversible and additive: it adds a relation, it does not
remove a poster from the corpus or from discovery. If we are wrong about a pair,
we have asserted a false relationship, not deleted a poster.

**Phase 3: Tier D, human review only.** 244 groups is a few hours of work for
someone who can look at two posters and say whether they are the same. We
generate the queue with the evidence attached; we do not guess.

**Not proposed at any tier: deleting or hiding records.** Everything stays in
the corpus. Whether the platform then shows one card per duplicate group is a
separate product decision, and one that should be made after we see how accurate
the linking turns out to be, not before.

## Questions for the team

1. **Do we want duplicates surfaced as links, or collapsed in discovery?**
   Linking is safe and reversible. Collapsing changes what users see and makes a
   false positive expensive. These are separable, and the answer may differ per
   tier.

2. **Is exact abstract match strong enough to link without review?** It is the
   line between Phase 2 running automatically and everything going to a queue.
   246 groups turn on this.

3. **Who reviews Tier D, and is 244 groups worth anyone's time?** An honest
   option is to link Tiers A to C and leave Tier D alone permanently.

4. **Should Tier A be fixed in the harvester instead?** The same DOI landing in
   the corpus twice looks like an ingest bug. Fixing the cause is better than
   annotating the symptom.

5. **What does the platform do with `IsIdenticalTo`?** If nothing consumes it,
   Phases 1 to 3 produce metadata nobody reads. Worth agreeing with Dorian
   before we build it.

## Numbers for reference

```
corpus records with a usable title            30,860
duplicate groups (title + first author)          822
records in them                                1,764
  Tier A  identical DOI                           7 groups     14 records
  Tier B  identical abstract                    246 groups    512 records
  Tier C  identical full author list, 2-3       325 groups    670 records
  Tier D  title + first author only             244 groups    568 records
group sizes   2:744  3:58  4:14  5:1  6:2  7:1  8:1  14:1
same-repository groups: 710 share a DOI prefix, 22 do not
```

Reproduce with the analysis scripts noted in the 0.39.0 changelog entry, run
against `/storage/poster-work/{pre2025,data2025}/merged` on hpcf.
