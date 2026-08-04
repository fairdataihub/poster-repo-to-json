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

### The three Zenodo deposits (nested license tiers)

The database is deposited as three records forming **nested license tiers**. Every
deposit contains **the complete database: all 31,417 poster records with full
metadata**, and every deposit applies **the same exclusion list** (the block list above).
What differs is which posters *also* carry the extracted poster content, each tier
adding one license on top of the previous:

| Deposit (Zenodo license) | Content for | With content | Metadata only |
|--------------------------|-------------|--------------|---------------|
| **1. CC0** (CC0-1.0) | CC0 and public domain | 826 | 30,591 |
| **2. CC-BY** (CC-BY-4.0) | tier 1 + CC-BY (incl. CC-BY-SA, OSI software, other-open) | 29,939 | 1,478 |
| **3. CC-BY-NC** (CC-BY-NC-4.0) | tier 2 + CC-BY-NC (incl. CC-BY-NC-SA) | 30,425 | 992 |

CC0 content is in every tier (it carries no conditions, so it is compatible with any
deposit license) and CC-BY content is in tiers 2 and 3. **Deposit 3 is the complete
deposit: its content set is identical to the BOSC CoFest database export.**

- **Full metadata for every poster ships in all three deposits.** Repository deposit
  metadata is openly licensed (CC0 on Zenodo and Figshare) regardless of the poster's own
  license, so the complete metadata set can be included everywhere without any license
  issue.
- **The per-poster `rightsList` is authoritative.** The deposit-level Zenodo license is
  the most restrictive license among the posters whose *content* is included; each poster
  keeps its own license, so the CC0 and CC-BY posters inside tier 3 remain CC0 and CC-BY.
- **The block list is identical in all three deposits** (992 records): no-derivatives
  (CC-BY-ND, CC-BY-NC-ND), In Copyright, all rights reserved, undetermined and unlicensed
  posters are metadata only everywhere, because the extracted content is a derivative work
  their license does not permit redistributing.
- **Tier 1 carries CC0 content alone** because it is the only tier whose deposit license
  can be CC0: including any poster that requires attribution would make a CC0 label
  incorrect. The CC-BY posters' content starts at tier 2.
- Metadata-only records carry one of two flags so a consumer knows why:
  `_license_blocked` (block list, everywhere) or `_content_excluded_from_archive`
  (openly licensed, but above this deposit's tier; its content is in a higher tier).

Counts (2026-08-04 export, 31,417 records) — license groups: CC0 and public domain 826;
CC-BY set 29,113 (CC-BY 28,677, CC-BY-SA 314, software 111, other-open 11);
non-commercial 486 (CC-BY-NC 367, CC-BY-NC-SA 119); block list 992 (no-derivatives 533,
restricted/undetermined 459).

### Zenodo records (fill in when uploaded)

- Deposit 1 — CC0: `TBD`
- Deposit 2 — CC-BY: `TBD`
- Deposit 3 — CC-BY-NC (complete): `10.5281/zenodo.21401531`

Once minted, link each tier to its Zenodo DOI on the docs.posters.science license page
and the Behind the Scenes section.
