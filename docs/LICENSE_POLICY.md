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

## Redistribution tiers (the three Zenodo archives)

The extraction rule above is **binary**: a license either permits deriving content
(kept) or does not (metadata-only). Public redistribution needs one more axis, because
commercial and non-commercial reuse cannot share a single archive license. The
openly-processed posters are therefore split into **three license-separated archives**
for deposit:

| Tier | Archive (Zenodo license) | Licenses | Reuse |
|------|--------------------------|----------|-------|
| Commercial + derivatives | **A** — CC-BY-4.0 | CC-BY (2.0-4.0), CC-BY-SA, and OSI software licenses (MIT, Apache-2.0, GPL-2.0/3.0, LGPL, BSD, ISC, MPL, Unlicense), other-open | Attribution; commercial use and derivatives allowed |
| Public domain + restricted metadata | **B** — CC0-1.0 | CC0-1.0 and other-pd (full content); all metadata-only records ride here | CC0 posters unrestricted; restricted records are metadata-only |
| Non-commercial | **C** — CC-BY-NC-4.0 | CC-BY-NC, CC-BY-NC-SA | Attribution; non-commercial only; derivatives allowed |

Rules for the split:

- **The per-poster `rightsList` is authoritative.** The archive-level Zenodo license
  (CC-BY-4.0 / CC0-1.0 / CC-BY-NC-4.0) covers the compilation and metadata; each poster
  keeps its own license inside `posterJson.rightsList`.
- **No-derivatives (ND) rule:** CC-BY-ND and CC-BY-NC-ND posters are **always
  metadata-only**, never full content, in any archive. They ride in Archive B.
- **Share-alike:** CC-BY-SA (in A) and CC-BY-NC-SA (in C) are kept but flagged in the
  archive README; derivatives must be shared under the same license. Software copyleft
  (GPL/LGPL) carries the same kind of obligation.
- **Why the restricted metadata rides in the CC0 archive:** repository deposit metadata
  is openly licensed (CC0 on Zenodo and Figshare) regardless of the poster's own license,
  so the metadata-only records are distributed under CC0 in Archive B. No content that a
  restrictive license forbids redistributing is ever included.

Counts as of the 2026-08-04 export (31,417 records): Archive A 29,113 (CC-BY 28,677,
CC-BY-SA 314, software 111, other-open 11); Archive B 1,818 (826 CC0/public-domain full +
992 metadata-only); Archive C 486 (CC-BY-NC 367, CC-BY-NC-SA 119).

Zenodo DOIs (add once minted): Archive A `10.5281/zenodo.21401531`; Archive B `TBD`;
Archive C `TBD`. Link each tier to its archive DOI on the docs.posters.science license
page and the Behind the Scenes section.
