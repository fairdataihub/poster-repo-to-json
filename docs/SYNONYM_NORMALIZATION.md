# Synonym Normalization & Entity Collapsing

How this repo collapses surface variants of the same entity (publishers, funders,
affiliations, subjects, conference locations) onto a single canonical form, and how it
avoids the classic failure modes of naive semantic clustering.

All code referenced here lives under:

- `scripts/post_processing/` — the batch tools (`build_synlustre.py`, `apply_synlustre.py`,
  `build_ror_index.py`, `conference_location_geocode.py`, `clean_llm_publishers.py`,
  `validate_vmeasure.py`, `drop_junk_affiliations.py`, `fix_bad_affiliations.py`)
- `src/poster_to_json/field_normalize.py` — the deterministic per-record field cleaners that
  run *before* clustering (junk-drop, placeholder-drop, NFKC, publisher cleaning)

Every setting below was read out of the code, not the design. Where a value is a runtime
argument rather than a hard-coded constant, that is called out explicitly.

---

## 1. Overview & pipeline

Normalization happens in two distinct layers:

1. **Deterministic per-record cleaning** (`field_normalize.py`). Runs during merge and as
   corpus backfills. It never guesses semantics — it canonicalizes a single value (NFKC,
   whitespace collapse, trim) and drops *junk values* (placeholders, URLs, letterless
   tokens, LLM hedge prose), never the record. This is the input contract for clustering:
   by the time a field reaches the clusterer, obvious garbage is already gone.

2. **Corpus-wide synonym collapsing** (`build_synlustre.py` → `apply_synlustre.py`, plus the
   geocode path for locations). This is the "synonym-lustre" method: embed the *distinct*
   surface terms of a field, cluster them, and map every member of a cluster to one
   canonical representative.

The end-to-end flow for a clusterable field (publisher / funder / affiliation / subject):

```
corpus (merged JSON)
   │  collect_terms()  -> Counter{ term: corpus_frequency }   (cached to <out>.terms.json)
   ▼
distinct terms
   │  _embed()         -> gte-large embeddings                (cached to <out>.emb.<hash>.npy)
   ▼
embeddings
   │  _holdout()       -> acronyms/short tokens pulled OUT (map to themselves)
   │  HDBSCAN(leaf)    -> semantic clusters on the remainder
   │  _split_by_ror()  -> split any cluster spanning >=2 RORs   (optional, --ror-index)
   ▼
clusters
   │  canonical = most-frequent variant (centroid-closest breaks ties)
   ▼
synlustre map: { variant -> canonical }   (pickle) + human-review TSV
   │  apply_synlustre.py
   ▼
corpus rewritten in place (list fields deduped after remap)
```

Conference **locations** do not use embeddings at all — they are normalized by geocoding
(section 6), because a string embedding would wrongly merge two different cities in the same
country ("Graz" and "Vienna" both read as "city in Austria").

The whole design principle is **precision over coverage**: it is always better to leave two
variants un-merged than to collapse two genuinely distinct entities. Every rule below is
biased that way.

---

## 2. The synonym-lustre method

Source: `scripts/post_processing/build_synlustre.py`. It mirrors the `pubverse_brett`
cd/tf-idf clustering approach: embed distinct terms, cluster, map each member to the term
closest to its cluster centroid, with frequency breaking ties.

### 2.1 Term collection

`collect_terms(merged_glob, field, cache_path)` walks the merged corpus and builds a
`Counter` of distinct term → corpus frequency. Extraction differs per field:

| Field | Source path in each record |
|-------|----------------------------|
| `publisher` | `publisher.name` (or bare-string `publisher`) |
| `funder` | `fundingReferences[].funderName` |
| `affiliation` | `creators[].affiliation[].name` |
| `subject` | `subjects[].subject` |

The counter is cached to `<out>.terms.json` so repeated eps sweeps do not re-scan the corpus
(the slow part).

### 2.2 Embeddings — gte-large

`_embed()` uses **`Alibaba-NLP/gte-large-en-v1.5`** via `sentence-transformers`, loaded with
`trust_remote_code=True`. Encoding parameters (verified in code):

- `convert_to_numpy=True`
- `normalize_embeddings=True` (L2-normalized, so euclidean distance ≈ cosine)
- `batch_size=256`
- `show_progress_bar=True`

Embeddings are cached to `<out>.emb.<sha1-of-phrases>.npy`. The cache key is a SHA-1 of the
newline-joined phrase list (first 16 hex chars), so any change to the term set invalidates
the cache automatically.

**PCA is OFF by default** (`--pca-dims 0`). The code comment is explicit: PCA costs
granularity — the fine distinctions that keep similar-but-distinct entities apart — so it is
opt-in only for very large N, and even then you keep *many* dims. When enabled, PCA output is
re-L2-normalized before clustering.

### 2.3 Clustering — HDBSCAN

`build_synlustre()` runs, on the *clusterable* subset only (see holdout, section 3):

```python
HDBSCAN(min_cluster_size=2,
        min_samples=1,
        cluster_selection_epsilon=eps,       # --eps, default 0.35
        cluster_selection_method="leaf",
        metric="euclidean")
```

- `min_cluster_size=2` — the smallest real merge is two variants of one entity.
- `min_samples=1` — minimal density requirement; keeps sparse-but-real variants clusterable.
- `cluster_selection_method="leaf"` — selects fine-grained leaf clusters rather than large
  agglomerated ones (again, precision over coverage).
- `metric="euclidean"` on L2-normalized vectors — equivalent to cosine distance.
- `cluster_selection_epsilon` is the one knob you tune per field (section 7).

Labels for held-out terms stay at `-1` (noise); noise maps to itself and is omitted from the
map (never written as a remap).

### 2.4 Canonical selection — most-frequent, centroid tiebreak

Inside `_emit()`:

```python
dists = cdist([sub.mean(axis=0)], sub, "euclidean")[0]
rep_i = max(range(len(idxs)), key=lambda i: (counts[members[i]], -dists[i]))
```

The canonical is the **most frequent variant** in the cluster — this gives a clean, familiar
chart label. **Centroid-closeness only breaks ties** among equally-frequent variants (the
`-dists[i]` secondary key). Only real remaps are stored (`variant -> rep` for `variant != rep`);
a variant that is already the canonical is not written.

### 2.5 Outputs

- `--out` — a pickle of `{ variant: canonical }`.
- `--review` — a human-review TSV: `representative<TAB>freq<TAB>merged_variants`, sorted by
  total cluster frequency, each variant annotated with its own count. This is the artifact you
  eyeball before applying.

### 2.6 Applying the map

`apply_synlustre.py` rewrites the corpus in place. For scalar `publisher` it swaps the name;
for the list fields (`funder`, `affiliation`, `subject`) it remaps each entry **and dedups**,
because two entries in the same record can collapse onto the same canonical. It is idempotent.

Example:

```bash
# build (pubverse env)
~/myenv/bin/python scripts/post_processing/build_synlustre.py \
    --field publisher \
    --merged-glob '/storage/poster-work/*/merged/*/*.json' \
    --out    /storage/poster-work/synlustre_publisher.pickle \
    --review /storage/poster-work/synlustre_publisher_review.tsv \
    --eps 0.30

# review synlustre_publisher_review.tsv by eye, then apply
~/myenv/bin/python scripts/post_processing/apply_synlustre.py \
    --field publisher \
    --synlustre /storage/poster-work/synlustre_publisher.pickle \
    --merged-dir /storage/poster-work/data2025/merged \
    --dry-run --show 40
# drop --dry-run to write
```

---

## 3. The acronym / short-token HOLDOUT rule

Source: `_holdout()` in `build_synlustre.py`. This is the single most important guardrail.

```python
def _holdout(term):
    t = str(term).strip()
    if len(t) < 4:
        return True
    if " " not in t and any(c.isalpha() for c in t) and t == t.upper():
        return True
    return False
```

A term is **held out of clustering** (mapped to itself, left at label `-1`) if:

1. it is **shorter than 4 characters**, OR
2. it is a **single all-caps token** — no space, contains a letter, and equals its own
   uppercase form (e.g. `ZHAW`, `GTC`, `MIDAS`, `UK`, `LUH`).

### Why — the ZHAW→Z class of errors

A 3–4 char token or an all-caps acronym carries too little signal for the embedding to place
reliably. It lands near unrelated short forms and the cluster then picks a *wrong*
representative. The code comment names the exact failures this prevents:

- `ZHAW -> "Z"`
- `LUH -> "HU"`

`fix_bad_affiliations.py` documents the full damage from the *pre-holdout* era: the old merge
collapsed short forms onto wrong 2-char canonicals — `United Kingdom -> UK`, `GTC -> GT`,
`LUH -> HU`, `L-up -> UP`, `UWS -> UW`, `Wisconsin -> WI`, `UL (Germany) -> UL`,
`MIDAS -> AU`. The bad canonical set it repairs is `{UP, UL, AU, UK, HU, UW, GT, WI}`.

The holdout ensures **only distinguishable multi-word / mixed-case names are ever merged**.
Every phrase is still embedded (so the embedding cache stays valid and complete), but only the
non-held-out subset is fed to HDBSCAN; labels are scattered back afterward and held-out terms
remain their own singletons.

---

## 4. ROR-based splitting

Semantic embeddings sometimes cluster two *different* institutions that are lexically close —
the canonical example is **University of Washington** vs **Washington University**. Passing a
ROR index to `build_synlustre.py` lets it split such clusters back apart.

### 4.1 Building the index — `build_ror_index.py`

Input is an official ROR v2-schema data dump (from the ROR Zenodo community). For each org it
records:

- `name2ror` — every normalized org name → its ROR id.
- `display` — the org's `ror_display` name (falls back to the first name, then the id).
- `loc` — GeoNames `(country_code, city)` from the org's first location
  (`geonames_details.country_code`, `name` or `country_name`).

Name normalization (`norm()`): NFKD → ASCII-fold → lowercase → strip non-`[a-z0-9 ]` → collapse
whitespace.

Two safeguards:

- **Ambiguous-name drop.** A normalized name that maps to **≥2 different RORs** is ambiguous
  and is removed from `name2ror` entirely, so it can never trigger a false split.
- **Trivial-name skip.** Names `<3` chars or with no letter are skipped.

```bash
python scripts/post_processing/build_ror_index.py \
    --dump v2.x-2024-xx-xx-ror-data.json \
    --out  /storage/poster-work/ror_index.pickle
# prints: orgs=... name2ror=... (dropped N ambiguous)
```

### 4.2 Splitting clusters — `_split_by_ror()`

Passed via `--ror-index`, it runs on every HDBSCAN cluster of size > 1:

1. Look up each member's ROR through `name2ror[_norm_ror(term)]` (the `_norm_ror` helper in
   `build_synlustre.py` matches `build_ror_index.norm` exactly: NFKD → ASCII → lowercase →
   `[^a-z0-9 ]`→space → collapse).
2. If the cluster spans **≤1 distinct ROR**, it is returned intact.
3. Otherwise it is **partitioned by ROR**. Each ROR becomes a sub-group.
4. **ROR-less members** (no name match) are assigned to the nearest ROR sub-group by embedding
   distance to that sub-group's centroid.

Each resulting sub-group is then canonicalized independently by `_emit()`. The run reports how
many multi-institution clusters were split.

```bash
~/myenv/bin/python scripts/post_processing/build_synlustre.py \
    --field affiliation \
    --merged-glob '/storage/poster-work/*/merged/*/*.json' \
    --out    /storage/poster-work/synlustre_affiliation.pickle \
    --review /storage/poster-work/synlustre_affiliation_review.tsv \
    --eps 0.07 \
    --ror-index /storage/poster-work/ror_index.pickle
```

---

## 5. Deterministic pre-clustering cleaners (`field_normalize.py`)

Clustering only sees what survives the deterministic layer. The relevant normalizers:

- **`_clean_publisher()`** — NFKC-normalize, collapse whitespace, trim; return `None` (junk)
  if `len<=2`, no letter, a placeholder, or a URL. `normalize_publisher()` keeps the
  extraction publisher (EPA, arXiv, PosterPresentations, universities, …) and only falls back
  to the source repository (Zenodo/Figshare) when there is no usable extracted publisher.
- **`normalize_subjects()` / `split_subject()`** — split lumped keyword blobs (leading
  "Keywords:"/"Subjects:"/"Topics:" header, top-level commas/semicolons, runs of 2+ spaces,
  repeated parenthetical acronyms), drop empty/placeholder/URL/email/letterless values, dedup.
  Bracketed taxonomy terms and single-spaced phrases stay whole.
- **`_is_placeholder()`** — the shared junk-value gate (`"n/a"`, `"unknown"`, `"not
  specified"`, `"conference name here"`, `"city, country"`, …).
- **`normalize_affiliation_in_name()` / `drop_llm_affiliation_creators()`** — relocate
  affiliation text that bled into a creator name, tag definite organizations, and drop
  LLM-added org-as-author entries. These shape the affiliation field the clusterer later sees.

Two affiliation backfills clean up residue that clustering must not see:

- **`drop_junk_affiliations.py`** — removes affiliation entries whose NFKC-stripped name is a
  single character (`"e"`, `"a"`, `"i"`, …); a one-char string is never a real institution.
- **`fix_bad_affiliations.py`** — repairs the small set of affiliations damaged by the
  *pre-holdout* merge (the `{UP,UL,AU,UK,HU,UW,GT,WI}` bad canonicals) by restoring each
  affected creator's affiliation from a pre-merge snapshot (matched by creator name-token set),
  and coerces bare-string affiliations to list form.

---

## 6. Geocoding-based conference-location normalization

Source: `scripts/post_processing/conference_location_geocode.py`. Conference locations get a
dedicated, non-embedding path. A string embedding would merge "Graz" and "Vienna" (both
"city in Austria"); geocoding tells them apart and unifies the spelling/format variants of
each. This is the location analogue of the ROR split.

Four resumable, cached stages:

| Stage | What it does |
|-------|--------------|
| `--collect` | Scan `conference.conferenceLocation` across the corpus; cache distinct strings + counts to `<work>.terms.json`. NFKC + whitespace-collapse per string (`_norm_q`). |
| `--geocode` | Geocode each distinct string via **Nominatim (OpenStreetMap)** through `geopy`, resolving to country + city. Caches to `<work>.geo.json`. |
| `--build` | Group by resolved `(country_code, city.lower())`; canonical = `"City, Country"`. Emit `<work>.map.pickle` + `<work>_review.tsv`. |
| `--apply` | Rewrite `conference.conferenceLocation` in the corpus from the map. |

Geocoding specifics (verified):

- `Nominatim(user_agent="posters-science-conf-loc/1.0", timeout=10)`.
- Rate-limited via `geopy.extra.rate_limiter.RateLimiter`, `min_delay_seconds = --sleep`
  (**default 1.1s**), `swallow_exceptions=True`. The default respects Nominatim's ≥1 req/sec
  usage policy.
- Query uses `addressdetails=True, language="en"`.
- **City resolution** walks: `city → town → village → municipality → state → county`.
- The geocode cache is flushed to disk every 50 lookups, so the stage is fully resumable.
- **Build only keeps entries** that resolved to both a `city` and a `country`; unresolved
  strings are left untouched in the corpus.

```bash
# staged (pubverse env; needs geopy)
W=/storage/poster-work/loc
G='/storage/poster-work/*/merged/*/*.json'
~/myenv/bin/python scripts/post_processing/conference_location_geocode.py --collect --merged-glob "$G" --work "$W"
~/myenv/bin/python scripts/post_processing/conference_location_geocode.py --geocode --work "$W"
~/myenv/bin/python scripts/post_processing/conference_location_geocode.py --build   --work "$W"
~/myenv/bin/python scripts/post_processing/conference_location_geocode.py --apply   --merged-glob "$G" --work "$W"
```

---

## 7. LLM-publisher junk cleaning

Source: `scripts/post_processing/clean_llm_publishers.py`. The 2025 backfill filled publishers
with a local LLM, whose output carries junk the basic `_clean_publisher` gate misses. This
tool applies the corpus's publisher rigor to those values and drops anything that fails back to
the repository fallback (Zenodo/Figshare by source). It reuses `_clean_publisher` and
`_is_placeholder` from `field_normalize.py` so the rules stay in one place.

`clean_publisher()` pipeline:

1. **`_unwrap()`** — strip surrounding quotes / `*` / backticks *only when balanced* (leaves
   an internal-quote name like `University of Campania "Luigi Vanvitelli"` intact).
2. **Preamble strip** — remove `"The publisher is …"` / `"Organization: …"` style leads.
3. **`_clean_publisher()`** — NFKC, URL/placeholder/`len<=2`/no-letter gate.
4. **Generic non-institution drop** — a fixed set (`"a university"`, `"research institute"`,
   `"the organization"`, `"company"`, …), tested on the lowercased string *without* stripping
   non-ASCII, so `"Εθνική … University"` does not collapse to bare `"university"`.
5. **Hedge drop** — LLM non-answer prose (`"not specified"`, `"appears to be"`, `"the poster …"`,
   `"i cannot"`, `"unclear"`, …).
6. **Citation drop** — `"… et al"` is a citation, not a publisher.
7. **Paragraph drop** — `>15` words is prose, not a name (skipped under `--conservative`, for
   extraction publishers with legitimately long institution names).

Anything returning `None` falls back to the source repository name.

```bash
~/myenv/bin/python scripts/post_processing/clean_llm_publishers.py \
    --merged-dir /storage/poster-work/data2025/merged --dry-run --show 40
# --conservative to keep long real institution names (skips the >15-word drop)
```

---

## 8. Recommended HDBSCAN settings per use case

The clustering shape (gte-large, `min_cluster_size=2`, `min_samples=1`, leaf, euclidean,
PCA off) is fixed. The one field-specific knob is `cluster_selection_epsilon` (`--eps`). Lower
eps = tighter, more conservative merges; higher eps = broader merges.

| Use case | Field / tool | Recommended eps | Rationale |
|----------|--------------|-----------------|-----------|
| Publishers | `build_synlustre --field publisher` | **0.30** | Publisher names are relatively distinctive; a moderate eps merges spelling/format variants of a press without collapsing distinct presses. |
| Funders | `build_synlustre --field funder` | **0.07** | Many agencies share near-identical wording (national ministries, "… Research Council"); a very tight eps prevents merging distinct funders. |
| Affiliations | `build_synlustre --field affiliation --ror-index …` | **0.07** | Institutions are dense and lexically close (many "University of X"); tight eps + the ROR split keep distinct institutions apart. |
| Subjects | `build_synlustre --field subject` | **0.10** | Keywords vary widely in surface form; a slightly looser eps folds obvious variants while keeping distinct topics separate. |
| Conference locations | `conference_location_geocode.py` (geocode) | n/a — **geocode, not embed** | Embedding merges same-country cities; geocoding to `(country, city)` is the correct disambiguator. |

**Confirmed vs. not-confirmed in code:** the code's built-in default is `--eps 0.35`
(`build_synlustre.py`, argparse), and `validate_vmeasure.py`'s default sweep is
`0.20,0.25,0.30,0.35,0.40,0.45,0.50`. The per-field values in the table above are **operating
recommendations passed on the command line**, not constants stored anywhere in the repo — no
source file hard-codes 0.30 / 0.07 / 0.10 per field. Treat them as the tuned starting points
and always re-validate with V-measure against a gold set for the corpus at hand (section 9).

---

## 9. V-measure validation

Source: `scripts/post_processing/validate_vmeasure.py`. This picks the best eps for a field by
scoring the real pipeline against a hand-labelled gold set — not a proxy. It **imports and
reuses `_embed` and `_holdout` directly from `build_synlustre.py`**, so the sweep exercises the
exact shipping recipe (gte-large, L2-normalized, leaf euclidean HDBSCAN,
`min_cluster_size=2` / `min_samples=1`).

### Gold format

A TSV (default) or JSON mapping each surface variant to its true canonical:

```
Univ. of Washington                 <TAB> University of Washington
University of Washington            <TAB> University of Washington
Washington University in St. Louis  <TAB> Washington University
Wash U                              <TAB> Washington University
```

A header line whose 2nd column is `true_canonical` is skipped. Order is preserved so the
embedding cache key stays stable across runs.

### Faithful noise handling

`build_synlustre` feeds only non-`_holdout` terms to HDBSCAN and leaves the rest at `-1`, where
each such term is effectively its own singleton canonical. Collapsing every `-1` point into one
cluster would falsely tell V-measure that all held-out/noise terms are the same entity, tanking
homogeneity. So before scoring, `predict_labels()` **explodes noise**: every `-1` point (held-out
acronyms + HDBSCAN noise) is reassigned a unique singleton label above any real cluster id. This
is the faithful V-measure of the pipeline's actual behaviour.

### Scoring

For each eps in the grid it runs the real clustering and computes
`sklearn.metrics.homogeneity_completeness_v_measure(true_labels, pred)`, then reports the eps
that maximizes V-measure (plus its homogeneity and completeness).

- **Homogeneity** — each predicted cluster contains only one true entity (penalizes
  over-merging, the precision failure this project fears most).
- **Completeness** — all variants of a true entity land in one cluster (penalizes
  under-merging).
- **V-measure** — their harmonic mean; the single number to maximize.

```bash
~/myenv/bin/python scripts/post_processing/validate_vmeasure.py \
    --field affiliation \
    --gold /storage/poster-work/gold_affiliation.tsv \
    --eps-list 0.05,0.07,0.10,0.15,0.20,0.25,0.30 \
    --emb-cache /storage/poster-work/vmeasure_affiliation.emb
```

Output is a per-eps table (`eps / homogeneity / completeness / v_measure / n_pred`) followed
by `BEST eps=… V=…`. Use the winning eps as the `--eps` you feed `build_synlustre.py`, then
eyeball the review TSV before applying.

---

## Quick reference — end-to-end for one clusterable field

```bash
ENV=~/myenv/bin/python
G='/storage/poster-work/*/merged/*/*.json'
M=/storage/poster-work/data2025/merged

# 0. (once) build the ROR index for affiliation splitting
$ENV scripts/post_processing/build_ror_index.py --dump ror-data.json --out ror_index.pickle

# 1. tune eps against a gold set
$ENV scripts/post_processing/validate_vmeasure.py --field affiliation \
     --gold gold_affiliation.tsv --eps-list 0.05,0.07,0.10,0.15,0.20 \
     --emb-cache /tmp/vm_aff.emb

# 2. build the synlustre map at the winning eps
$ENV scripts/post_processing/build_synlustre.py --field affiliation \
     --merged-glob "$G" --out syn_aff.pickle --review syn_aff_review.tsv \
     --eps 0.07 --ror-index ror_index.pickle

# 3. review syn_aff_review.tsv by eye

# 4. apply (dry-run first)
$ENV scripts/post_processing/apply_synlustre.py --field affiliation \
     --synlustre syn_aff.pickle --merged-dir "$M" --dry-run --show 40
$ENV scripts/post_processing/apply_synlustre.py --field affiliation \
     --synlustre syn_aff.pickle --merged-dir "$M"
```
