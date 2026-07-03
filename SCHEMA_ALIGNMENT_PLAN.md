# Poster.json Schema Alignment Plan

Aligns `poster-repo-to-json` (converter + merger + normalizers) and the delivered
24k Zenodo + 7k Figshare corpus with the **Poster.json Field Coverage** mapping.

> **Convention:** where this plan says *Zenodo* it also means *Figshare* — the rule
> is the same, only the native field path differs (see the Figshare column).

## Context: the legacy-vs-new-API split

The field-coverage TSV was written against Zenodo's **current InvenioRDM API**
(`person_or_org.*`, `custom_fields["meeting:meeting"]`, `metadata.funding[]`,
`metadata.subjects[]`, `metadata.additional_descriptions[]`). But the 24k we
scraped are the **legacy Zenodo REST** shape:

```json
"creators":[{"name":"Family, Given","affiliation":"<string|null>","orcid":"0000-..."}],
"keywords":["..."], "license":{"id":"cc-by-4.0"}, "meeting":{...},
"language":"...", "relations":{...}, "description":"...", "publication_date":"..."
```

So `given_name` / `family_name` / `person_or_org.type` / `affiliations[].id (ROR)` /
`funding[]` / `additional_descriptions[]` are **0% present** in stored Zenodo data.
**Figshare** stored metadata **is** rich (`authors[].first_name/last_name/orcid_id`,
`funding_list[]`, `categories[]`, `license`, `references[]`, `related_materials[]`).

## Locked decisions

1. **Re-fetch source = DataCite REST API** (`https://api.datacite.org/dois/{doi}`).
   It returns the deposit already in DataCite/poster.json schema and covers **both**
   Zenodo and Figshare DOIs. We re-fetch ONLY the gaps legacy lacks; everything else
   is derived from stored metadata (hybrid).
2. **Funding = deposit-authoritative; drop LLM-only funders.** Funding is currently
   LLM-guessed (hallucination risk); the deposit's DataCite `fundingReferences` wins,
   and funders that exist only in the extraction are discarded.
3. **Strip to the TSV.** Remove fields the TSV marks *None* that we currently over-emit:
   `rightsList[].rightsUri/rightsIdentifier/rightsIdentifierScheme/schemeUri` and
   `dates[]` of type **Submitted** and **Presented**. Keep only `rightsList[].rights`
   and `dates[]` **Issued**.
4. **Creator name text = fullest-form union** (unchanged) — deposit + LLM names,
   dedup keeping the most complete form. Structured identifiers (ORCID / ROR /
   nameType / given / family) come from the authoritative deposit; the LLM only
   fills gaps, never overwrites.
5. **`publisher.publisherIdentifier` = empty pre-publish.** posters.science stamps
   `10.17616/R3QP53` + scheme at publish time; the pipeline must not inject a value.

---

## Itemized actions

Tags: `[no-change]` verified correct · `[code]` code-only fix + deterministic backfill ·
`[refetch]` needs the DataCite pull then backfill · `[strip]` remove per decision 3.

### (A) Hardcoded

| # | Field | Now | Target | Action |
|---|---|---|---|---|
| A1 | types.resourceType | `"Scientific Poster"` (schema_converter.py:262,512,181) | `"Poster"` | `[code]` change 3 literals |
| A2 | types.resourceTypeGeneral | `"Image"` (:264,514,181) | `"Poster"` (DataCite 4.7) | `[code]` change 3 literals |
| A3 | identifiers[].identifierType | `"DOI"`/`"Other"`/`"Handle"` | same | `[no-change]` |
| A4 | nameIdentifiers[].nameIdentifierScheme | `"ORCID"` (:230,479) | same | `[no-change]` |
| A5 | nameIdentifiers[].schemeURI | **missing** | `"https://orcid.org"` | `[code]` add in both converters + backfill |
| A6 | affiliation[].affiliationIdentifierScheme | `"ROR"` (only when id) | same | `[no-change]`; refetch code must also set it |
| A7 | descriptionType (abstract) | `"Abstract"` | same | `[no-change]` |
| A8 | descriptionType (summary) | `"Other"` on demote | same | `[no-change]` |
| A9 | publisher.publisherIdentifier / Scheme / schemeURI | injects bare repo-ROR (invalid) | **empty pre-publish** | `[code]` skip enrich_publisher publisher branch (decision 5) |
| A10 | dates[].dateType | per entry | Issued only (see B-dates) | see B |

### (B) Zenodo / Figshare-authoritative

| # | Field | Now | Target | Action |
|---|---|---|---|---|
| B1 | identifiers[].identifier | top-level `doi` | deposit DOI | `[no-change]` |
| B2 | publisher.name | forced repo name | `metadata.publisher` (= "Zenodo") | `[no-change]` |
| B3 | version | LLM only (converter never assigns) | `metadata.version` / `record.version` | `[code]` read deposit, deposit-preferred + backfill |
| B4 | publicationYear | `publication_date[:4]`, out-of-range dropped | year of publication_date | `[no-change]` (document the drop) |
| B5 | dates[].date (Issued) | `publication_date` | same | `[no-change]` |
| B6 | dates[].date (Submitted) | emitted from `created` | **None** | `[strip]` + backfill |
| B7 | dates[].date (Presented) | emitted from `meeting` | **None** | `[strip]` + backfill |
| B8 | conference.* (name/loc/uri/acronym/year) | legacy `metadata.meeting.*` | same | `[no-change]` |
| B9 | conference.startDate/endDate | only splits on `" - "` | parse free-text ranges | `[code]` fallback via date_normalize |
| B10 | rightsList[].rights | legacy `license.id` → SPDX | SPDX id | `[no-change]` |
| B11 | rightsList[].rightsUri/Identifier/Scheme/schemeUri | emitted (SPDX quartet) | **None** | `[strip]` + backfill |
| B12 | relatedIdentifiers[].relatedIdentifier | legacy flat path (**verify key exists**) | deposit relations | `[code]` verify + Figshare `references[]`/`related_materials[]` |
| B13 | relatedIdentifiers[].relationType | `RELATION_MAP` (lossy → "References") | full DataCite enum | `[code]` expand map |
| B14 | relatedIdentifiers[].resourceTypeGeneral | **never populated** | from deposit resource_type | `[code]` read legacy flat value |
| B15 | fundingReferences[].funderName | **LLM-guessed** (reads empty `grants[]`) | deposit `funding[].funder.name` | `[refetch]` deposit wins, drop LLM-only |
| B16 | fundingReferences[].funderIdentifier(+Type,+schemeUri) | not emitted (name-resolver → Crossref) | ROR from `funding[].funder.id` | `[refetch]` |
| B17 | fundingReferences[].awardNumber | reads empty `grant.code` | `funding[].award.number` | `[refetch]` |
| B18 | fundingReferences[].awardTitle | reads empty `grant.title` | `funding[].award.title` (in the record) | `[refetch]` |
| B19 | fundingReferences[].awardUri | never produced | `funding[].award.identifiers[url]`; Figshare `funding_list[].url` | `[refetch]` (Zenodo) / `[code]` (Figshare native) |
| B20 | descriptions (abstract) | legacy `metadata.description` → primary | promote deposit desc to main | `[no-change]` (matches target for legacy) |

### (C) Zenodo + poster2json

| # | Field | Now | Target | Action |
|---|---|---|---|---|
| C1 | creators[].name | deposit-base + union + fullest-form dedup | fullest-form union (decision 4) | `[no-change]`; optional: route surname-matched LLM authors through `_merge_name` |
| C2 | creators[].givenName | comma-split heuristic | deposit `person_or_org.given_name`; LLM fills gaps | `[refetch]` (Figshare native `first_name` already read) |
| C3 | creators[].familyName | comma-split heuristic | deposit `person_or_org.family_name` | `[refetch]` (Figshare native `last_name` already read) |
| C4 | creators[].nameType | **hardcoded `Personal`** (blocks fallback) | deposit `type` wins; else LLM/tag_org_creators | `[refetch]` + leave unset when absent so fallbacks fire |
| C5 | nameIdentifiers[].nameIdentifier (ORCID) | legacy `creator.orcid` (works) | deposit ORCID; LLM fills gaps | `[no-change]`; optional URL-normalize (pairs with A5) |
| C6 | affiliation[].name | legacy single string (works) | deposit affiliations[].name | `[no-change]` single; `[refetch]` for multi-affiliation |
| C7 | affiliation[].affiliationIdentifier | ROR from text-matcher only | deposit `affiliations[].id`; matcher = gap-fill | `[refetch]` |
| C8 | affiliation[].schemeUri | **invalid key casing** `schemeUri` | `schemeURI` = `https://ror.org` | `[code]` fix emitters + backfill rename |
| C9 | subjects[].subject | union of `keywords` + LLM, dedup | union + NFKC dedup | `[no-change]`; optional NFKC-uniform hardening |
| C10 | descriptions (summary) | 100% LLM (`additional_descriptions` absent) | deposit "Other" if non-empty, else LLM | `[refetch]` |

### (D) poster2json / None — no action
`sizes` (unpopulated) · `formats` (extraction-first + file/PDF fallback) · `titles.title`
(LLM wins, deposit backfills; optional future QC compare) · `titles.titleType` (absent) ·
`language` (body-text detector is final authority; Zenodo lang distrusted) · subjects
scheme/valueUri/subjectScheme/classificationCode (absent; latent: Figshare `categories[]`
FoR codes dropped) · conference identifier/series (absent) · content/captions/researchField
(extraction passthrough; researchField re-classified to OpenAlex domains).

---

## DataCite re-fetch mapping (`api.datacite.org/dois/{doi}` → `data.attributes`)

| poster.json | DataCite attributes path | Merge rule |
|---|---|---|
| creators[].givenName | `creators[].givenName` | deposit authoritative; comma-split fallback; LLM gap-fill |
| creators[].familyName | `creators[].familyName` | deposit authoritative; comma-split fallback |
| creators[].nameType | `creators[].nameType` | deposit wins; unset when absent → LLM/tag_org_creators fire |
| affiliation[].affiliationIdentifier | `creators[].affiliation[].affiliationIdentifier` (ROR) | deposit authoritative; matcher = exact-only gap-fill |
| affiliation[].name (multi) | `creators[].affiliation[].name` | optional multi-affiliation |
| fundingReferences[] | `fundingReferences[]` (funderName/funderIdentifier/awardNumber/awardTitle/awardUri) | deposit authoritative; drop LLM-only |
| descriptions (summary) | `descriptions[]` where `descriptionType=="Other"` | deposit "Other" wins over LLM |

**Not re-fetched** (legacy suffices): DOI, titles, abstract (`metadata.description`),
subjects (`keywords`), rights (`license`), conference (`meeting`), publicationYear/Issued,
ORCID (`creators[].orcid`). Figshare needs **no** re-fetch — its stored metadata already
carries first/last/orcid_id/funding_list/categories/license/references.

---

## Execution phases

**Phase 1 — code-only value/casing/strip fixes (no network; deterministic 31k backfill):**
✅ DONE (v0.19.0, delivered 2026-07-02): A1, A2, A5, A9, C8, B3, B6/B7 (strip), B11 (strip)
— `align_schema()` normalizer + `add_version.py`; validated clean corpus-wide (0 bad types,
0 publisher-ids, 0 Submitted/Presented dates, 0 rights sub-fields, all 40k ROR affils fixed,
10,661 deposit versions added). **Deferred within Phase 1:** B9 (conference free-text ranges),
B12–B14 + B19 (relatedIdentifiers / Figshare references) — pending the Phase 2 key-existence verify.

**Phase 2 — verify + deferred code items.** ✅ DONE (v0.20.0, delivered 2026-07-03).
Verify findings (of 4k Zenodo sampled): `related_identifiers` present ~10%, `grants`
(legacy funding, populated!) ~23%, `relations` = version graph only (not used).
Shipped: B9 (conference free-text ranges — 3,683 records), B12–B14 (full DataCite
relationType enum + resourceTypeGeneral; Figshare `references`/`related_materials` —
1,886 records), B19 (Figshare `funding_list[].url` → awardUri). `backfill_phase2.py`.
**Note:** legacy Zenodo `grants` already yields funderName/awardNumber/awardTitle +
a Crossref Funder ID for ~23% — so funding is NOT all LLM-guessed; Phase 3 mainly
adds ROR + awardUri + drops LLM-only funders. **Zenodo relatedIdentifiers** folded
into Phase 3 (DataCite returns them clean).

**Phase 3 — DataCite re-fetch (`api.datacite.org/dois/{doi}`) + converter/merger + backfill:**
C2, C3, C4, C7 (creators given/family/type/ROR), B15–B18 (funding: deposit-authoritative,
drop LLM-only), C10 (summary description), and Zenodo relatedIdentifiers. Batch the
~17k Zenodo DOI pulls once. Figshare needs no re-fetch (stored metadata already rich).

**Phase 4 — optional hardening:** NFKC-uniform subjects, fullest-form upgrade for
surname-matched creators, title QC comparison, Figshare category FoR codes.
