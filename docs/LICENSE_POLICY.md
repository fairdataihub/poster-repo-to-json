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
| Software (copyleft) † | GPL-3.0, GPL-2.0, LGPL-3.0, LGPL-2.1, AGPL, RPL |
| Zenodo non-SPDX | other-open ‡, other-pd |

† **Copyleft licenses permit extraction but constrain redistribution.** GPL and friends
allow derivative works, so extraction is legitimate, but they require the derivative to be
distributed under the *same* license. Since we do not mint a GPL-licensed deposit, copyleft
posters are **metadata-only in every published deposit**. See
[Redistribution compatibility](#redistribution-compatibility).

‡ `other-open` means "an open license Zenodo does not enumerate" — the specific terms are
unknown, so it could be ShareAlike or copyleft. It is safe to extract but **cannot be
certified compatible with any deposit license**, so it is held for review rather than
placed in a bucket.

## Blocked Licenses (blocklist)

These do **not** grant derivative/redistribution rights. No content is extracted from
the poster file; only repository metadata is retained.

| Category | Licenses | Reason |
|----------|----------|--------|
| No-Derivatives | CC-BY-ND-\*, CC-BY-NC-ND-\* (all versions) | ND explicitly prohibits derivative works |
| Restrictive | All Rights Reserved, In Copyright (incl. "Educational Use Permitted" and "Rights-Holder(s) Unlocatable or Unidentifiable") | No redistribution permitted |
| Unresolved | Copyright not evaluated, Copyright undetermined, notspecified | Cannot confirm permission |
| Unknown terms | other-at, other-closed, other-nc, other, other-open, EU-EMI | No defined license terms to evaluate |
| Read-only grants | zenodo-freetoread-1.0 | Grants reading only; no derivative or redistribution right |
| Empty/null | (missing rightsList) | Cannot confirm permission; assume restrictive |
| Not a license | Grant codes, project titles, contact text found in the rights field | Depositor metadata error; treated as "no license" |

**Metadata-only for redistribution (a separate reason).** Copyleft licenses (GPL-2.0/3.0,
GPL-3.0-or-later, LGPL, AGPL, RPL) are *not* blocked from extraction — they permit
derivative works. They are metadata-only in our published deposits because they require the
derivative to stay under the same license and we mint no copyleft deposit. See
[Redistribution compatibility](#redistribution-compatibility).

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

## Redistribution compatibility

The extraction rule above is **binary**: a license either permits deriving content (kept)
or does not (metadata-only). Publishing the corpus needs a second, finer rule, because a
deposit's license is an *offer to the downloader*. If a bundle labelled CC-BY-4.0 contains
work whose own license forbids those terms, the offer is misleading and invites downstream
infringement. So **every deposit must carry a license that all of its content can
legitimately be offered under.**

The governing question for each license is: *may an adaptation be released under different
terms?*

| Family | Adaptation may be relicensed? | Consequence |
|--------|-------------------------------|-------------|
| **Public domain** — CC0-1.0, CC-PDDC, other-pd, ODC-PDDL | Yes, no conditions | Compatible with every deposit |
| **Attribution-only** — CC-BY 1.0-4.0 (incl. ported, e.g. 3.0-US) | Yes, attribution preserved | Ship under CC-BY-4.0 |
| **Permissive software** — MIT, BSD-2/3-Clause, ISC, Apache-2.0, MPL-2.0 | Yes, notices retained | Ship under CC-BY-4.0 (see note) |
| **ShareAlike** — CC-BY-SA | **No** — same or later license with the same elements | Needs its own CC-BY-SA-4.0 deposit |
| **NonCommercial** — CC-BY-NC | Yes, within NC terms | Ship under CC-BY-NC-4.0 |
| **NonCommercial + ShareAlike** — CC-BY-NC-SA | **No** | Needs its own CC-BY-NC-SA-4.0 deposit |
| **Copyleft** — GPL, LGPL, AGPL, RPL | **No** — must stay under the same license | Metadata-only (we mint no copyleft deposit) |
| **No-derivatives** — CC-BY-ND, CC-BY-NC-ND | **No derivative at all** | Metadata-only, always |
| **Restricted / unknown** — In Copyright, ARR, undetermined, other-at/closed/nc, other-open, unlicensed | Cannot confirm | Metadata-only |

Rule of thumb: **permissive licenses relicense; copyleft and ShareAlike do not.**

Notes on the individual cases:

- **Permissive software licenses in a CC-BY deposit are acceptable** because MIT, BSD and
  Apache-2.0 expressly allow redistribution and sublicensing under other terms, conditioned
  on retaining the original copyright and permission notice (Apache-2.0 adds: include the
  license, keep `NOTICE`, state changes). Each record keeps its own `rightsList`, and each
  deposit README states that the per-record license governs, which satisfies that condition.
- **ShareAlike version upgrades are allowed.** CC-BY-SA-2.0 §4(b) permits derivatives under
  "a later version of this License with the same License Elements", so BY-SA 2.0 and 4.0
  share one BY-SA-4.0 deposit. The same holds for CC-BY-NC-SA-2.0.
- **1.0 ShareAlike licenses do not upgrade.** The 1.0 generation lacks the later-version
  clause, so CC-BY-NC-SA-1.0 cannot be offered under NC-SA-4.0. With only a handful of such
  records, they are kept metadata-only rather than given a separate deposit.
- **Copyleft is a redistribution limit, not an extraction limit.** GPL permits derivative
  works; it just requires them to stay GPL. We do not mint a GPL-licensed deposit, so those
  posters are metadata-only in everything we publish.

## The Zenodo deposits

The corpus is published as **five license-separated deposits**. Each poster appears in
exactly one, chosen by its own license; posters whose license forbids redistributing
derivatives appear as **metadata only**.

| Deposit (Zenodo license) | Content for | Notes |
|--------------------------|-------------|-------|
| **1. CC0-1.0** | CC0-1.0, CC-PDDC, other-pd, ODC-PDDL | Also carries **all metadata-only records** (block list + copyleft + review), since repository deposit metadata is CC0 regardless of the poster's own license |
| **2. CC-BY-4.0** | CC-BY 4.0 / 3.0 / 3.0-US / 2.0, MIT, Apache-2.0, BSD | Permissive only; no ShareAlike, no copyleft |
| **3. CC-BY-SA-4.0** | CC-BY-SA-4.0, CC-BY-SA-2.0 | ShareAlike obligation carries to derivatives |
| **4. CC-BY-NC-4.0** | CC-BY-NC 4.0 / 3.0 / 1.0 | Non-commercial reuse only |
| **5. CC-BY-NC-SA-4.0** | CC-BY-NC-SA-4.0, CC-BY-NC-SA-2.0 | Non-commercial **and** ShareAlike |

- **The per-poster `rightsList` is authoritative.** The deposit-level license is the most
  restrictive license among the content in that deposit; individual posters keep their own,
  so a CC0 poster inside the CC-BY deposit remains CC0.
- **Metadata-only records keep** all repository deposit metadata (identifiers, creators with
  affiliations and ORCID/ROR, titles, dates, publisher, subjects, funding, conference,
  rightsList, language) and the deposit abstract. They lose the poster-derived fields
  (`content`, `imageCaptions`, `tableCaptions`, `researchField`, `domain`) and **carry no
  thumbnail**.

### Excluded from all content deposits (metadata only)

| Category | Examples | Reason |
|----------|----------|--------|
| No-derivatives | CC-BY-ND, CC-BY-NC-ND (all versions) | ND forbids derivative works |
| Copyleft | GPL-2.0/3.0, GPL-3.0-or-later, LGPL, AGPL, RPL-1.5 | Derivatives must stay under the same license |
| Restricted | In Copyright (incl. "Educational Use Permitted" and "Rights-Holder Unlocatable" variants), All Rights Reserved | No redistribution right |
| Unresolved | Copyright not evaluated, Copyright undetermined, notspecified | Cannot confirm permission |
| Unknown terms | other-at, other-closed, other-nc, other-open, EU-EMI | Terms not determinable |
| Read-only grants | zenodo-freetoread-1.0 | Grants reading only, no derivative or redistribution right |
| Missing | no license / null | Default-deny |

### Review queue

Licenses that resolve to none of the above are held for manual review before being placed
in a deposit. Current dispositions:

| Value | Disposition |
|-------|-------------|
| `ODC-PDDL` | Public domain → CC0 deposit |
| `AFL-3.0`, `Etalab-2.0`, `ODC-BY` | Permissive / attribution-only → eligible for the CC-BY-4.0 deposit |
| `RPL-1.5` | Reciprocal copyleft → metadata only |
| `CC-NC` | Ambiguous: NonCommercial with no version or attribution element. Needs a team decision or depositor contact; metadata-only until resolved |
| `zenodo-freetoread-1.0`, `notspecified`, `EU-EMI` | Metadata only |
| `ICEA, IST-027819-IP` and other free-text values | **Not licenses.** These are grant codes, project titles, or contact messages placed in the rights field by the depositor. Treated as "no license" (metadata only) and worth reporting upstream as bad deposit metadata |

### Zenodo records (fill in when uploaded)

- Deposit 1 — CC0: `TBD`
- Deposit 2 — CC-BY: `TBD`
- Deposit 3 — CC-BY-SA: `TBD`
- Deposit 4 — CC-BY-NC: `TBD`
- Deposit 5 — CC-BY-NC-SA: `TBD`

Once minted, link each tier to its Zenodo DOI here and on the docs.posters.science
auto-registration page.
