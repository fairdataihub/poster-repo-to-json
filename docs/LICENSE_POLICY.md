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

License enforcement runs as a default post-processing step after merge and before
Azure sync. See `scripts/post_processing/enforce_license_policy.py`.

The policy definitions (whitelist/blocklist) live in
`scripts/post_processing/license_policy.py`.
