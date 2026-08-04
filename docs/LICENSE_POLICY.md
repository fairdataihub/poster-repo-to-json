# License Policy for posters.science Corpus

## Principle

Structured JSON extraction from a scientific poster constitutes a derivative work.
The pipeline checks each poster's license before processing. If the license does not
clearly grant permission to redistribute derivatives, no content is extracted from
the poster file. Only repository metadata is retained (identifiers, creators, titles,
dates, publisher, rights, funding, etc.).

## Allowed Licenses (whitelist)

These licenses explicitly permit derivative works. Extracted poster content is **kept**.

| Category | Licenses |
|----------|----------|
| Public domain | CC0-1.0 |
| CC Attribution | CC-BY-4.0, CC-BY-3.0, CC-BY-2.5, CC-BY-2.0, CC-BY-1.0 |
| CC Attribution-ShareAlike | CC-BY-SA-4.0, CC-BY-SA-3.0, CC-BY-SA-2.5, CC-BY-SA-2.0 |
| CC Attribution-NonCommercial | CC-BY-NC-4.0, CC-BY-NC-3.0, CC-BY-NC-2.5, CC-BY-NC-2.0 |
| CC Attribution-NonCommercial-ShareAlike | CC-BY-NC-SA-4.0, CC-BY-NC-SA-3.0, CC-BY-NC-SA-2.5, CC-BY-NC-SA-2.0 |
| Software (permissive) | MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC, Unlicense, MPL-2.0 |
| Software (copyleft) | GPL-3.0, GPL-2.0, LGPL-3.0, LGPL-2.1 |
| Zenodo non-SPDX | other-open, other-pd |

## Blocked Licenses (blocklist)

These do **not** grant derivative/redistribution rights. No content is extracted from
the poster file; only repository metadata is retained.

| Category | Licenses | Reason |
|----------|----------|--------|
| No-Derivatives | CC-BY-ND-\*, CC-BY-NC-ND-\* (all versions) | ND explicitly prohibits derivative works |
| Restrictive | All Rights Reserved, In Copyright | No redistribution permitted |
| Unresolved | Copyright not evaluated, Copyright undetermined | Cannot confirm permission |
| Unknown terms | other-at, other-closed, other-nc, other | No defined license terms to evaluate |
| Empty/null | (missing rightsList) | Cannot confirm permission; assume restrictive |

## What is and is not included

When a poster's license is blocked, **nothing is extracted from the poster file** — the
poster is not run through poster2JSON at all, so there is no poster content, no
poster-derived metadata, and no thumbnail. We only use the repository-provided deposition
metadata to index the poster, and only when that metadata is itself openly accessible
(which is the case, e.g., for Zenodo and Figshare, whose deposition metadata is CC0).

A `_license_blocked: true` flag marks the record as metadata-only. The poster-derived
fields below are therefore absent:

- `content` (text sections)
- `descriptions` (abstracts)
- `imageCaptions` (figure captions)
- `tableCaptions` (table captions)
- `researchField` (research domain)

The fields that **are** included come from the repository deposition metadata (CC0 on
Zenodo and Figshare), never from the poster file:

- `identifiers`, `relatedIdentifiers`
- `creators`, `contributors`
- `titles`
- `publisher`, `publicationYear`
- `subjects`, `dates`
- `rightsList`
- `fundingReferences`
- `conference`
- `formats`, `sizes`, `version`
- `language`
- `$schema`

## New or unlisted licenses

Any license that appears in **neither** list above — including a poster with **no license**
or an **unrecognized license string** — is **treated as blocked by default**. Such a poster
is indexed with repository deposition metadata only (no poster content, no thumbnail),
exactly like any other blocked poster.

When the pipeline encounters a license outside both lists, it must be **raised with the
team**: we decide together which bucket it belongs in and add it to the most appropriate
list. The default-blocked stance holds until that review is complete.

## Pipeline integration

License enforcement is a **default step of the pipeline itself**: every merged record is
classified and, if its license is not open (blocked, or unknown/unlisted → default-deny),
its poster-derived content is stripped and `_license_blocked` is set. This runs inside
`MetadataMerger.merge` (`poster_to_json.merger`) via `enforce_license`, so no poster leaves
the merge step with content it is not licensed to redistribute. It can be disabled only by
setting `MetadataMerger.ENFORCE_LICENSE = False` (internal runs).

The policy definitions and logic (whitelist/blocklist, license-string normalization,
`classify_license`, `strip_extracted_content`, `enforce_license`) live in
`src/poster_to_json/license_policy.py`. License strings are normalized (fold
case/spacing/punctuation + resolve aliases to canonical SPDX ids) before matching, so
`CC BY 4.0` resolves to `CC-BY-4.0`.

`scripts/post_processing/enforce_license_policy.py` re-runs enforcement over an
already-built corpus (idempotent), and `restore_blocked_content.py` re-attaches content to
records that a stricter/older classifier wrongly stripped but are now allowed.

## License types and the three Zenodo archives

The extraction rule above is **binary**: a license either permits deriving content
(kept) or does not (metadata-only). Redistribution needs the finer split below. Each
poster falls into one of **four license types**, and the openly-processed posters are
deposited as **three license-separated archives**.

### License types

| Type | Licenses | Content | Commercial | Derivatives |
|------|----------|---------|------------|-------------|
| **Fully open** | CC-BY (2.0-4.0), CC-BY-SA, CC0-1.0, other-open, other-pd, OSI software (MIT, Apache-2.0, GPL-2.0/3.0, LGPL, BSD, ISC, MPL, Unlicense) | kept | yes | yes |
| **Non-commercial** | CC-BY-NC, CC-BY-NC-SA | kept | **no** | yes |
| **Non-derivative** | CC-BY-ND, CC-BY-NC-ND | **metadata only** | n/a | **no** |
| **Restricted / undetermined** | In Copyright, All Rights Reserved, Copyright not evaluated/undetermined, other-at/closed/nc, unlicensed/null | **metadata only** | no | no |

- **Non-commercial** is its own type: the content is kept, but reuse is limited to
  non-commercial purposes.
- **Non-derivative (ND)** is its own type: ND forbids derivative works, so ND posters are
  **always metadata-only**, never full content, in every archive, even though extraction
  would otherwise be permitted for some of them.
- Share-alike (CC-BY-SA, CC-BY-NC-SA) is kept within its type but flagged in the archive
  README; derivatives must be shared under the same license. Software copyleft (GPL/LGPL)
  carries the same kind of obligation.

### The three archives

| Archive (Zenodo license) | Contents | Reuse |
|--------------------------|----------|-------|
| **A — CC-BY** (CC-BY-4.0) | All CC-BY posters (with CC-BY-SA, software, other-open) | Attribution; commercial use and derivatives |
| **B — CC0 + exclusion metadata** (CC0-1.0) | CC0 and public-domain posters (full) + all non-derivative and restricted posters as metadata-only | CC0 posters unrestricted; restricted are metadata-only |
| **C — Non-commercial** (CC-BY-NC-4.0) | All CC-BY-NC posters **plus all CC-BY posters** | Everything usable non-commercially in one download; the CC-BY subset is also commercial |

- **Archive C intentionally includes the CC-BY posters** so a non-commercial reuser gets
  everything they can use in a single download. The CC-BY posters therefore appear in both
  A and C; that duplication is deliberate.
- **The per-poster `rightsList` is authoritative.** The archive-level Zenodo license
  covers the compilation and metadata; each poster keeps its own license, and the CC-BY
  posters in Archive C remain CC-BY.
- **Why the restricted metadata rides in the CC0 archive:** repository deposit metadata is
  openly licensed (CC0 on Zenodo and Figshare) regardless of the poster's own license, so
  the metadata-only records are distributed under CC0 in Archive B. No content that a
  restrictive license forbids redistributing is ever included.

Counts (2026-08-04 export, 31,417 records) — types: fully open 29,939 (CC-BY 28,677,
CC-BY-SA 314, CC0 822, software 111, other-open/pd 15); non-commercial 486;
non-derivative 533; restricted/undetermined 459. Archives: A 29,113; B 1,818
(826 CC0/public-domain full + 992 metadata-only); C 29,599 (29,113 CC-BY set + 486
non-commercial).

### Zenodo records (fill in when uploaded)

- Archive A — CC-BY: `10.5281/zenodo.21401531`
- Archive B — CC0 + exclusion metadata: `TBD`
- Archive C — Non-commercial: `TBD`

Once minted, link each license type to its archive DOI on the docs.posters.science
license page and the Behind the Scenes section.
