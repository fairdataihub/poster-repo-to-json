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

## Output hygiene (v0.23.0, delivered 2026-07-04)

Five output-cleanliness fixes found by QA-ing real records against the schema
(all in `align_schema`): (1) `$schema` forced to canonical **v0.2** (the LLM
extraction's older `$schema` was surviving the merge); (2) leaked internal fields
(`_source`, `_extraction_time_s`) stripped; (3) null/empty `conference` omitted;
(4) `affiliation` normalized to a list of `{name}` objects (bare strings wrapped,
null dropped) — schema allows `oneOf[string,object]` so either is valid; (5)
reference DOIs removed from `identifiers[]` when already in `relatedIdentifiers[]`.

## Platform integration (posters.science) — how auto-indexed posters are handled

Verified against the canonical repos so our batch output matches the interactive
Poster-Sharing path:
- **Canonical schema** = `fairdataihub/poster-json-schema`, `$id` **v0.2** (matches our fix). Reference `example/poster.json` has NO `_`-internals and uses affiliation strings — both consistent with our output.
- **Auto-indexing pipeline** = `fairdataihub/posters-science-extraction-api` → `job_worker.py` polls a DB and runs **`poster2json.extract`** (the library we vendored as `vendor/poster2json_features`), then `validation.py` validates against `poster_schema.json` (v0.2) + fills missing optional fields with defaults — it does NOT reshape data. `validation.py` explicitly handles affiliation `oneOf[string,object]`.
- **Two paths differ by design:** Poster Sharing is LLM + user-reviewed + live ORCID/ROR API + publishes to Zenodo. Auto-Indexing is deposit-derived. Submitted (share-time) stays Poster-Sharing-only. **Presented parity added (v0.24.0):** `ensure_presented_date` derives a Presented date from the conference (start, or start/end range, `dateInformation` = "Presented at &lt;conference&gt;") for the ~2,646 pre-2025 + ~2,635 2025 records that carry conference dates — matching the Poster-Sharing path.
- **Upstream drift to flag to Sanjay/Dorian:** upstream `poster2json/ror.py:295` still emits the schema-invalid lowercase `"schemeUri"` (we fixed it to `schemeURI` in our vendored copy) — so the Poster-Sharing path currently produces the same invalid affiliation. Fixing it upstream benefits both paths and keeps our vendored copy from drifting.

## Date authority — deposit over LLM-hallucinated years (v0.25.0, delivered 2026-07-04)

An audit found the "2025-hallucination era" left two problems (the deposit Issued
date was always correct — `issued_mismatch=0` corpus-wide — but):
- **publicationYear** disagreed with the Issued date in **1,119 data2025 records**
  (LLM year survived the 2025 merge; e.g. `py=2024` while `Issued=deposit=2025`). Pre-2025 was already correct.
- **conferenceYear/dates** were LLM-won and hallucinated: ~3,823 records with
  `conferenceYear` > pubYear+1 (e.g. `2025` on a 2018 poster), ~521 with a future
  `conferenceStartDate` that had fed a wrong Presented date.

Fix (all deposit-authoritative):
- `reconcile_publication_year` — publicationYear follows the deposit **Issued** date
  (Presented fallback), validated via `normalize_publication_year` (rejects bogus 2029/9999).
- `sanitize_conference_dates` — drops conference Start/End/Year and any derived
  Presented date whose year is >1yr after publicationYear. Runs AFTER reconcile, so
  legitimate same-year conferences (2025 poster at a 2025 conference) are kept.
- `backfill_conference_from_meeting.py` — restores Zenodo conference blocks from the
  authoritative deposit `meeting` (**6,866 pre-2025 + 209 2025**). `conference_from_meeting`
  (extracted from the converter) routes all meeting dates through `normalize_date_value`
  and keeps only clean ISO (`_iso_date_or_none`) — never leaks free-text like "19 november 2024".
- Then `ensure_presented_date` re-derives Presented from the corrected conference.

Order in merge()/normalize_fields: reconcile_publication_year → … → sanitize_conference_dates → ensure_presented_date.

**Hard rule — strip invalid dates (v0.27.0).** A date whose year is outside
[1900, 2026] is not a valid date, so it is STRIPPED, never carried (deposits
occasionally supply bogus 2029/9999). `strip_invalid_dates` runs FIRST (before
reconcile_publication_year), on every `dates[].date` (each side of a range) and
conference start/end/year; an emptied `dates[]` is dropped. This resolves the
former "not fixable" edge cases: e.g. `13986507` (deposit `Issued=2029`) now drops
the bogus date and leaves `publicationYear` absent rather than carrying a future
date. Only 2 pre-2025 records were affected (the bogus-deposit ones), but the rule
holds go-forward.

**String-conference coercion (v0.26.0).** A corpus-wide re-audit after the date fix
verified **0** remaining `py != Issued`, future `conferenceYear`, future
`conferenceStartDate`, or future `Presented` dates across all four sections — but
surfaced **~2,270 records (1,003 pre-2025 + 1,267 2025) with `conference` as a bare
string** (the LLM emits just the name, and the merge only wraps it when the deposit
also has a meeting). That is schema-invalid AND was invisible to
`sanitize_conference_dates` (it guards `isinstance(dict)`). `normalize_conference`
now coerces a non-empty string to `{conferenceName: …}` (empty → dropped), so it is
validated and date-reachable. After the backfill, string-conference count is **0**
and normalize_fields is idempotent (second pass = 0 changes).

## Auto-index ingestion field review (v0.28–0.29, delivered 2026-07-06)

Reviewed our output against the ACTUAL consuming code: `posters-science/scripts/
add-extracted-posters.ts` (the auto-index ingestion — distinct from the submissions
API `upload`/`release/zenodo`). It reads our `*_complete.json`, `mapToDbFields()` →
`PosterMetadata`, `automated=true, status=published`, upserted by DOI.

- **identifiers / creators** — our output matches. Ingestion lowercases the DOI (unique
  key), reads creators name/nameType/nameIdentifiers(objects)/affiliation. NOTE: it does
  NOT store `givenName`/`familyName` or `nameIdentifiers[].schemeURI` (we still emit them —
  schema-correct). It also hardcodes `publisher="Zenodo"` and `format="application/pdf"`.
- **Schema version** — the ingestion never reads `$schema`; our `$schema` is a self-
  declaration for validation only. Both repos ship `sync_schema.py`; the schema is meant
  to be SYNCED from `poster-json-schema` (currently v0.2), not hand-hardcoded. TODO: sync
  our bundled schema + `_SCHEMA_URL` from that repo instead of the hand-set constant.
- **`_license_blocked` REGRESSION (fixed v0.28.0)** — the ingestion reads `_license_blocked`
  to serve a placeholder thumbnail for content-blocked posters. align_schema's underscore-
  strip had removed it from 967 blocked posters. align_schema now exempts it
  (`_INGESTION_KEPT_FIELDS`); the flag was restored corpus-wide (639 pre-2025 + 347 2025).
- **`researchField` vs `domain` (fixed v0.29.0)** — schema uses `researchField` (we emit it),
  but the ingestion reads `data.domain`. align_schema now mirrors `researchField → domain`
  (schema `additionalProperties:true` allows it). ALSO flag to the team: their ingestion
  should read `researchField`.
- **Blocked-poster policy refined** — per decision, blocked (ND/ARR) posters KEEP the deposit
  Abstract (open Zenodo CC0 metadata) and drop only content/captions/researchField/domain +
  the LLM `Other` description. `license_policy.strip_extracted_content` updated.
- **Open item — `unknown` licenses**: `classify_license` docstring says enforcement treats
  `unknown` as `blocked`, but `enforce_license_policy.py` only acts on `blocked` (93 unknown-
  license posters are neither flagged nor stripped). Decide whether to treat unknown as blocked.

## Creator-field cleanup (v0.30.0, delivered 2026-07-06)

Found via an extremes scan (shortest/longest per field) of the 9 identifier+creator
fields; designed + adversarially verified by a multi-agent workflow (the skeptic pass
caught a destructive-drop defect and an ASCII-only letter check that would have deleted
CJK names). Five idempotent, precision-first normalizers:
- **normalize_name_identifiers** — URL-normalize ROR/ISNI/GND `nameIdentifier` + add
  canonical `schemeURI` (mirrors the ORCID handling). ~165 records.
- **drop_invalid_orcids** — drop ORCID `nameIdentifier`s failing the ISO-7064 MOD-11-2
  checksum (6 corpus-wide: `2105-…`, `0999-…`, all-zeros).
- **split_lumped_name** (enhanced) — split `X and Y`/`X & Y` two-full-name lumps; drop
  `and others`/`et al` remnants. ~36 records.
- **normalize_affiliation_in_name + drop_llm_affiliation_creators** — split `Name.
  Affiliation` / `Surname, Affiliation` into `affiliation[]` (person kept); tag definite
  orgs Organizational; **drop LLM-added org creators absent from the deposit** (33
  pre-2025; guarded: never without deposit evidence, never a person-extractable name,
  never a `;` person-list, never empties creators). Deposit-aware backfill
  `backfill_drop_llm_affiliation.py`.
- **drop_letterless_creator_fields** — drop no-letter junk (`2`, `-`); Unicode-aware, so
  CJK/short-alpha names (`Ng`, `丽`) are kept.

Junk-by-field truth: `name` field was clean (0 junk); the earlier "9,041 junk" was 9,025
legitimate single-letter initials + only 16 no-letter values. All delivered, idempotent,
0 residual.

## QC portion 2: affiliation / subjects / titles (v0.31.0, delivered 2026-07-06)

Second extremes scan (affiliation/titles/publisher/publicationYear/subjects). Clean:
affiliationIdentifier (all ROR URLs), scheme/schemeURI, publisher (Zenodo/Figshare;
note the ingestion HARDCODES publisher="Zenodo"), publicationYear (min 1986/max 2026,
0 unrealistic). Anomalies fixed (designed + adversarially verified by workflow):
- **normalize_affiliation_names** — NFKC each affiliation name; drop no-letter junk
  (`" "`, `"&"`); split `;`-lumped multi-institution names into separate entries
  (never comma/`and`; never a ROR-bearing entry so the id is not mis-attached); dedup.
- **normalize_subjects/split_subject** (enhanced) — drop letterless junk (`"."`,`"1"`);
  split on a leading "Keywords and subjects" header, on 2+ spaces, and on repeated
  parenthetical acronyms; keeps single phrases and a `Subject-Verb` compound whole.
- **replace_bad_llm_title + backfill_title_fallback.py** — fall back to the deposit
  title when the LLM title is a fragment/acronym (`LOST`,`SIP`,`null`) or a >250-char
  paragraph. The verifier's "period+12-word" rule was dropped (over-flagged legit long
  titles); rejects placeholder/filename deposit titles.
Applied: ~1,137 affiliation records, ~323 subject records, 35 title fallbacks; 0 errors,
idempotent. Schema note: still v0.2 but it changed content-only today (publisher now
nullable) -- reinforces syncing the schema file over hardcoding.

## QC portion 3: dates / version / relatedIdentifiers / descriptions (v0.32.0, 2026-07-06)

Third extremes scan (dates/language/types/relatedIdentifiers/version/rights/descriptions).
Clean: language (valid 2-letter), all relatedIdentifier enums (type/relationType/
resourceTypeGeneral), and descriptions are already Abstract-first (31,231/31,254 -- the
"promote deposit description to MAIN" SHOULD is met; descriptions[0] = platform main desc).
types is not read (platform hardcodes "Poster"). Anomalies fixed (workflow-designed +
adversarially verified):
- **collapse_multidate_ranges** -- collapse a malformed dates[].date with 3+ '/'-joined
  ISO dates to min/max (or a single date after de-dup). 23 records.
- **normalize_version** -- drop a version that is a URL / >25ch sentence / spam
  (trademark, phone-run, marketing); keep dotted+short versions (phone-pattern excludes
  '.'). 25 records.
- **drop_junk_related_identifiers** -- drop placeholder/<=3-char/encoded-junk entries;
  `%2C` or `%20`-in-a-non-URL is junk, but a single `%20` in an http URL is kept (refined
  from the verifier's over-broad rule: 44 -> 19 dropped, real reference URLs preserved).
- **drop_junk_descriptions** -- drop letterless / <=2-char / raw-JSON-blob descriptions
  (the LLM dumped `{"references": [...]}` as prose); keep real prose incl. CJK + long
  abstracts (added `import json`). 747 records.
Delivered, idempotent, 0 errors.

## QC portion 4: funding / conference / content / captions (v0.33.0, 2026-07-07)

Fourth extremes scan (fundingReferences/conference/content/imageCaptions/tableCaptions).
KEY correction: award "junk" was a false alarm -- grant numbers are legitimately numeric
(5,210 valid, 2 truly junk); and long funder "sentences" include real official agency
names (e.g. "Agencia Nacional de Promocion..."). So the real signal is acknowledgement
PHRASING, not length. Anomalies fixed (built inline, precision-first + dry-run-reviewed):
- **drop_junk_funding** -- drop a fundingReferences entry whose funderName is no-letter or
  an acknowledgement-sentence misparse ("This study was supported by..."); KEEP real agency
  names and any entry with a funderIdentifier; clear a no-alnum awardNumber (numeric grants
  kept). 202 records.
- **clean_conference_junk** -- clear a no-letter / <=2-char conferenceName and a no-letter /
  single-char / >30-char conferenceAcronym; conference dates untouched. 50 records.
- **drop_junk_sections** -- drop fully-junk content sections; strip no-letter titles; DEMOTE
  an over-long (>200ch) title into sectionContent (never drop -- dry-run caught the first cut
  dropping real mis-slotted content; fixed to preserve every char). ~858 records.
- **drop_junk_captions** -- drop no-letter / <=2-char image+table captions; keep real ones
  (even long). ~448 records.
User decision: clean obvious junk in the LLM content (sections/captions) precision-first,
keep all real content. Delivered, idempotent, 0 errors, junk verified 0-remaining.

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

**Phase 3 — DataCite re-fetch.** ✅ DONE (v0.21.0, delivered 2026-07-03). Fetched
`api.datacite.org/dois/{doi}` for all Zenodo (17,055 pre-2025 + 5,324 in 2025;
`fetch_datacite.py`, cached, concurrent, resumable) and enriched via
`enrich_from_datacite.py`: **nameType 100%** (DataCite-authoritative, wins over the
org-heuristic; "otherwise Personal"), **structured givenName/familyName 93–98%**,
**funding deposit-authoritative** (ROR funder ids; LLM-only funders dropped), plus
subjects + depositor-declared relatedIdentifiers (version-graph filtered). C7
(affiliation ROR) **not achievable** — DataCite affiliation is name-only (depositors
rarely register ROR), so the text-matcher remains the ROR source. Figshare needed no
re-fetch. Delivered: pre-2025 19,660 files, 2025 open subset 3,658 files, 0 failures,
0 integrity issues.

**ALL PHASES COMPLETE.** The 24k+7k corpus is aligned to the target schema.

**Phase 4 — optional polish.** ✅ DONE (v0.22.0, delivered 2026-07-03):
- **NFKC-uniform subjects** — `normalize_subjects` NFKC-normalizes values.
- **ORCID URL-normalization** — bare `0000-...` → `https://orcid.org/0000-...` (schema
  example form); all corpus ORCIDs now URL-form.
- **Title QC** — `title_qc.py` audit report (3,628 pre-2025 records where LLM title vs
  deposit title diverge, sim<0.4) at `C:\Users\jimno\Downloads\title_qc_pre2025.tsv`.
  Report-only; LLM title stays authoritative.

Evaluated + **NOT applied**:
- **Fullest-form creator upgrade** (`backfill_fullest_names.py`) — only ~11 records had a
  genuinely fuller single-author extraction form; most "fuller" forms are junk-appended
  (role suffixes / affiliation bleed / emails), so it'd corrupt names for negligible gain.
  given/family are already structured from DataCite. Tool kept in repo, not run.
- **Figshare category FoR/ANZSRC codes** — would populate `subjects[].subjectScheme` /
  `classificationCode`, which the TSV marks `None` → conflicts with decision 3
  (strip-to-TSV). Skipped; revisit if the schema owner wants them.
