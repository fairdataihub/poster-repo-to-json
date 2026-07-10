# Publisher: posters-science ingestion change needed (for Sanjay / Dorian)

## Context
The metrics chart "Top Publishers and Repositories" currently shows a single giant
**Zenodo (30,875)** bar plus a few stray singletons. The bar is not our data — the
auto-index ingestion **hardcodes** the publisher.

Our side (`poster-repo-to-json`, v0.36.0) now carries the **real, normalized publisher**
in `data.publisher.name` (the poster2json extraction publisher — EPA, arXiv,
PosterPresentations, universities, ... — cleaned with NFKC + junk-drop, falling back to
`Zenodo`/`Figshare` when a poster has no extracted publisher). So once the ingestion reads
that field, the chart returns to the "before" distribution automatically.

## Change 1 — auto-index: read our publisher instead of hardcoding

`posters-science/scripts/add-extracted-posters.ts`, line ~128:

```diff
- const publisher = "Zenodo";
+ const publisher =
+   (data.publisher && data.publisher.name && String(data.publisher.name).trim())
+     ? String(data.publisher.name).trim()
+     : "Zenodo";
```

That's it — `mapToDbFields` already threads `publisher` into the row (lines ~201/323).
Every auto-indexed poster then reports its real publisher, with `Zenodo`/`Figshare` as the
fallback we already bake in, so the counts come out as the full distribution.

## Change 2 — submission path: force Zenodo (kills the singletons)

`posters-science-extraction-api/job_worker.py`: a poster submitted **through**
posters.science is deposited to Zenodo, so its publisher should be `"Zenodo"`, not the LLM's
guess (that's where "Water Resources Research", "SciLifeLab", "Jura Museum …" singletons come
from). The publish step comment (line ~546) already says "publisher: set to 'Zenodo' on
publish"; make sure that actually writes `"Zenodo"` (line ~550 currently leaves it `None`,
which lets the extracted value through).

## Notes / caveats
- **2025 batch**: that poster2json run extracted `publisher: null` for all ~7,198 posters,
  so those correctly fall back to `Zenodo`/`Figshare`. Pre-2025 (24,165) carries the real
  variety (17,561 real publishers + 6,604 repo fallback). Re-extracting 2025 would recover
  its publishers, but that's a separate job.
- We intentionally did **not** fuzzy-merge institutional variants (e.g. "EPA" vs "EPA Office
  of Research") — that's the same rigor we applied to the other fields (NFKC + junk-drop +
  casing, no semantic merging). If the chart wants EPA-variants collapsed, that's a small
  follow-up (a canonicalization map) on either side — say the word.
