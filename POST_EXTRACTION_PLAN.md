# Post-Extraction Action Plan

**Updated:** 2026-05-11
**Corpus:** 24,226 classified posters → 23,713 extracted (439 remaining, 74 errors)

## Decisions (locked in)

- **No re-extraction.** Post-process only.
- **`researchField`** = domain string (one of 4 OpenAlex domains), classified by `jimnoneill/paper-to-field`. Always accept top prediction.
- **Auto-generated descriptions** → `descriptionType: "Other"` per poster-json-schema v0.2.
- **ORCID** via authenticated API (Client ID `APP-N30L8SSUUJPTTGW0`, credentials in env vars).
- **Strip all placeholders** from SchemaConverter output. See `PLACEHOLDER_FIXES_FOR_POSTER2JSON.md`.
- **License display names** always normalized to full SPDX name (v0.5.9).
- **`version` field** now extracted from posters (v0.5.9).
- **Final output:** Individual JSONs per poster, uploaded to Azure alongside PDFs.
- **Embedding model:** `Alibaba-NLP/gte-large-en-v1.5` (434M, BERT-based encoder, 8K context, 1024-dim).
- **Embed from JSON** (structured title + description + section content), not raw OCR text.

---

## Execution Plan

### Phase A: Finish extraction — NEARLY DONE

439 remaining, 2 GPUs still running. Should finish today.

### Phase B: Post-process all extractions (on ws209)

#### B1. `_postprocess_json` on all extractions (poster2json v0.5.9)

Batch script loads each `*_extracted.json` + raw text from `_raw_text/{stem}.json`, runs `_postprocess_json(data, raw_text=text)`, saves back.

Handles: schema v0.1→v0.2, conference normalization, caption dedup, hallucinated conference/publisher stripping, Unicode cleanup, identifier regex extraction, funder normalization (ROR API), publisher/affiliation ROR enrichment, publisher-suspect validation, language detection, **license display name normalization (v0.5.9)**, SPDX normalization, researchField cleanup → null.

#### B2. ORCID enrichment (authenticated API)

OAuth `client_credentials` flow. Credentials as env vars on ws209. ~72K lookups, disk-cached. Run overnight.

#### B3. Description retag: auto-generated → "Other"

Retag `descriptionType` from `"Abstract"` to `"Other"` for auto-gen phrases.

#### B4. Domain classification via `paper-to-field`

`jimnoneill/paper-to-field` (BioM-ELECTRA-Large, 335M) on ALL posters. Always accept top prediction.

### Phase C: Update merger code (on this machine, parallel with B)

#### C1. Fix `SchemaConverter`

- `SCHEMA_URL` → v0.2
- Gut `_ensure_required_fields`: remove ALL placeholder defaults except `types` and `formats`
- `_normalize_language` → return `null` instead of `"en"` for empty/unrecognized

#### C2. Fix `MetadataMerger`

- Remove single-description enforcement. Both Abstract and Other coexist.
- Preserve enrichment fields (ORCID, ROR, `_validation`, `researchField`)
- Strip placeholders from metadata before merge

#### C3. Fix `run_merge.py`

- Force re-conversion with fixed SchemaConverter
- Verify matching logic for all poster stems

### Phase D: Final assembly

1. Run `run_merge.py` on full corpus
2. Corpus validation + spot checks
3. Azure sync — individual JSONs alongside PDFs

### Phase E: pgvector embedding database

#### E1. Set up PostgreSQL + pgvector

Stand up a PostgreSQL instance with the pgvector extension. Schema:

```sql
CREATE EXTENSION vector;

CREATE TABLE posters (
    id            SERIAL PRIMARY KEY,
    stem          TEXT UNIQUE NOT NULL,
    source        TEXT,              -- zenodo / figshare
    title         TEXT,
    pub_year      INTEGER,
    domain        TEXT,              -- researchField (4 OpenAlex domains)
    language      TEXT,
    metadata      JSONB NOT NULL,    -- full poster JSON
    embedding     vector(1024)       -- gte-large-en-v1.5
);

CREATE INDEX ON posters USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX ON posters USING gin (metadata jsonb_path_ops);
CREATE INDEX ON posters (domain);
CREATE INDEX ON posters (pub_year);
```

#### E2. Generate embeddings

Run `Alibaba-NLP/gte-large-en-v1.5` on all poster JSONs. Input text per poster:

```
Title: {title}
Authors: {creator names}
Abstract: {first description}
Keywords: {subjects}
Content: {section contents joined}
```

- 434M params, ~1 GB VRAM in fp16. Fits trivially on a 3090.
- 8,192 token context covers all posters (99th percentile was ~3K tokens).
- Batch encode with sentence-transformers. ~24K posters in minutes.

#### E3. Load into pgvector

Bulk insert poster metadata + embedding vectors. Build IVFFlat index.

#### E4. Search API

Expose similarity search: query text → embed → cosine similarity → top-K results. Combine with metadata filters (domain, year, language).

---

## Execution Order

```
Phase A (extraction)         ───► done (today)
Phase B (postprocess)        ───► B1 → B2 → B3 → B4 (on ws209)
Phase C (merger fixes)       ───► C1 → C2 → C3 (this machine, parallel with B)
Phase D (merge + validate)   ───► after B + C
Phase E (pgvector)           ───► after D (needs final merged JSONs)
  E1  PostgreSQL + pgvector setup
  E2  Generate embeddings (gte-large-en-v1.5)
  E3  Bulk load
  E4  Search API
```

---

## Files to create/modify

| File | Action | Phase |
|------|--------|-------|
| `scripts/batch_postprocess.py` | **New** — batch runner for B1-B4 | B |
| `poster2json/orcid.py` (on ws209) | Patch for OAuth client_credentials | B2 |
| `src/poster_to_json/schema_converter.py` | Strip placeholders, v0.2 schema | C1 |
| `src/poster_to_json/merger.py` | Multi-description, enrichment-aware | C2 |
| `scripts/run_merge.py` | Force re-conversion, updated paths | C3 |
| `scripts/generate_embeddings.py` | **New** — embed posters with gte-large-en-v1.5 | E2 |
| `scripts/load_pgvector.py` | **New** — bulk load into PostgreSQL | E3 |
| `PLACEHOLDER_FIXES_FOR_POSTER2JSON.md` | Upstream fix list for poster2json | Done |
