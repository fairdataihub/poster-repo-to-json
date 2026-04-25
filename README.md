# poster-repo-to-json (staging)

> **Part of the Machine-Actionable Scientific Poster Initiative**

Full pipeline for converting scientific posters into machine-actionable JSON. Uses [poster2json](https://github.com/fairdataihub/poster2json) for extraction (pdfalto + Qwen2-VL + Llama 3.1 8B), then enriches with Zenodo/Figshare repository metadata. Output conforms to the [poster-json-schema](https://posters.science/schema/v0.1/poster_schema.json) (DataCite 4.6 with poster extensions).

**This `staging` branch** contains the refactored, production-hardened pipeline used to process the full Zenodo+Figshare corpus (~24K posters) on a multi-GPU node.

## Pipeline Position

1. **Collect** posters ([poster-repo-scraper](https://github.com/fairdataihub/poster-repo-scraper))
2. **Validate** and classify ([poster-repo-qc](https://github.com/fairdataihub/poster-repo-qc))
3. **Extract and enrich** to machine-actionable JSON (this package)

## How It Works

```
poster.pdf ──► poster2json ──► scaffold JSON ──► merge ──► final JSON
                                                   ▲
                                                   │
Zenodo/Figshare metadata ──► SchemaConverter ──────┘
                                               (backfill)
```

**poster2json output is PRIMARY.** Repository metadata only fills in fields that poster2json left empty or is authoritative for (conference name/dates, DOIs, licenses, funding, etc.). The extracted poster content (title, abstract, sections, captions) is gospel.

### Merge Rules (staging branch)

| Field | Source of truth |
|-------|----------------|
| `titles`, `descriptions`, `content`, `imageCaptions`, `tableCaptions` | **Extraction only** (never overwritten) |
| `conference` | **Metadata supersedes extraction** (LLM hallucinates conferences; repositories have authoritative data) |
| `identifiers` | **Metadata only** (real poster DOI + record ID). DOIs found in poster text get moved to `relatedIdentifiers` with `relationType: "References"` |
| `dates`, `language`, `types`, `rightsList`, `fundingReferences`, `publisher` | Extraction base, metadata backfill if missing |
| `creators` | Extraction base; metadata backfills ORCIDs, affiliations |
| `subjects` | Union of both (deduped, case-insensitive) |

### Quality Enforcement

- **Single description only** — schema expects one abstract; if the LLM dumps multiple description entries, the merger keeps only the first `Abstract`-typed one.
- **Post-batch QC** (`scripts/post_batch_qc.py`) writes a TSV of failed extractions for retry (multi-description, no-content, corrupt JSON, OCR failures).

## Installation

```bash
# Clone with submodules
git clone https://github.com/fairdataihub/poster-repo-to-json.git
cd poster-repo-to-json
git checkout staging

# Install this package
pip install -e .

# Install poster2json as a sibling directory (patched version)
git clone https://github.com/fairdataihub/poster2json.git ../poster2json
cd ../poster2json && pip install -e .
```

### Prerequisites

- **CUDA GPU** with 16GB+ VRAM (or 6GB+ with 4-bit quantization)
- **pdfalto** for PDF layout analysis

```bash
git clone https://github.com/kermitt2/pdfalto.git
cd pdfalto && mkdir build && cd build && cmake .. && make
sudo cp pdfalto /usr/local/bin/
```

## Single-Poster Usage

```bash
# One-shot extraction via poster2json + merge with metadata
python run_extraction.py \
    --posters /path/to/poster/pdfs \
    --metadata /path/to/repo/metadata \
    --output ./output
```

## Multi-GPU Batch Pipeline

The `scripts/` directory contains the production batch pipeline used for large corpora:

```
scripts/
├── batch_extract_v2.py      # Two-phase extraction (text first, LLM second)
├── run_2gpu.sh              # Launcher for 2-GPU parallel processing
├── post_batch_qc.py         # Quality check + failed extraction log
├── run_merge.py             # Convert metadata + merge incrementally
└── clean_stale_errors.py    # Remove stale error files with recoverable cached text
```

### Architecture: Two-Phase Extraction

**Phase 1 (CPU+GPU):** Extract raw text from all posters.
- `pdfalto` for text-based PDFs (fast, CPU-only)
- Qwen2-VL OCR fallback for image-based PDFs (GPU)
- Raw text cached to disk (`_raw_text/`) so it survives crashes

**Phase 2 (GPU):** Load Llama 3.1 8B once, process all texts sequentially.
- Model stays resident (no reload overhead between posters)
- 4-bit NF4 quantization by default (~5GB VRAM, quality preserved)
- Individual saves per poster (crash-safe resume)

### Running on 2 GPUs

```bash
# Set poster2json location
export POSTER2JSON_PATH=/path/to/poster2json

# Split input across GPUs (zenodo first, figshare second)
# Place symlinks in ./gpu_splits/gpu0, gpu1, gpu2, gpu3

# Launch (runs gpu0+gpu1 first, then gpu2+gpu3 after they finish)
bash scripts/run_2gpu.sh
```

**Why 2 GPUs, not 4?** Running all 4 RTX 3090s at full load (350W each) on a single consumer circuit trips overcurrent protection. Running 2 at 250W stays within a standard 15A/20A circuit. `nvidia-smi -pl 250` limits draw.

### Post-Batch Quality Check

```bash
python scripts/post_batch_qc.py
# Writes: /path/to/output/extractions/../failed_extractions.tsv
```

Checks for:
- `multi_description` — LLM dumped section content into descriptions instead of `content.sections`
- `no_content` — no sections extracted
- `corrupt_json` — mid-write crash
- `extraction_error` — Phase 1 failures (OCR couldn't recover)

### Backfilling New poster2json Features

When poster2json ships new enrichment features (e.g. SPDX license normalization, ROR affiliation IDs, heuristic language detection, `researchField` on the OpenAlex 4 domains), existing extraction JSONs can be updated in-place without re-running the LLM:

```bash
export POSTER2JSON_PATH=/path/to/poster2json
python scripts/backfill_features.py --extractions /path/to/output/extractions
```

The backfill is idempotent and reuses cached raw text from `_raw_text/` for language detection. It applies:

- SPDX license normalization (`rightsList` entries get canonical SPDX IDs + URIs)
- Subject dedupe + NFKC cleanup
- ROR enrichment on `creators.affiliation` and `publisher` (canonical names + ROR IDs)
- Heuristic language detection from raw text (overrides LLM-hallucinated language)
- `researchField` placeholder strip (drops "Other", "Unknown", etc. → `null`)
- NFKC normalization on titles and descriptions

The feature modules (`normalize.py`, `ror.py`, `language.py`) are vendored in `vendor/poster2json_features/` for reference.

### Stale Error Recovery

If earlier runs had bugs (OOM crashes, broken pdfalto flag, etc.), those stale error JSONs block re-processing. This script removes them if the raw text is cached (Phase 1 succeeded but Phase 2 failed):

```bash
python scripts/clean_stale_errors.py
# Re-running the pipeline will reprocess them with instant Phase 1 (cache hit)
```

### Incremental Merging

```bash
python scripts/run_merge.py
# Converts repository metadata + merges with available extractions.
# Safe to re-run — only processes new files.
```

## CLI Commands

```bash
# Single-step commands (wraps the Python API)
poster-to-json extract --input ./posters --output ./extractions
poster-to-json convert --input ./metadata/zenodo --output ./converted --source zenodo
poster-to-json merge --extractions ./extractions --metadata ./converted --output ./merged
poster-to-json pipeline --posters ./posters --metadata ./metadata --output ./output
```

## Python API

```python
from poster_to_json import PosterExtractor, SchemaConverter, MetadataMerger

extractor = PosterExtractor()
extraction = extractor.extract("poster.pdf")

converter = SchemaConverter()
metadata = converter.convert_zenodo(raw_zenodo_record)

merger = MetadataMerger()
complete = merger.merge(extraction, metadata)
```

## Output Schema

Conforms to the **poster-json-schema** (DataCite 4.6 with poster extensions):

```json
{
  "$schema": "https://posters.science/schema/v0.1/poster_schema.json",
  "identifiers": [{"identifier": "10.5281/zenodo.12345678", "identifierType": "DOI"}],
  "titles": [{"title": "..."}],
  "creators": [{"name": "Smith, John", "nameIdentifiers": [...], "affiliation": [...]}],
  "descriptions": [{"descriptionType": "Abstract", "description": "..."}],
  "content": {
    "sections": [
      {"sectionTitle": "Introduction", "sectionContent": "..."},
      {"sectionTitle": "Methods", "sectionContent": "..."},
      {"sectionTitle": "Results", "sectionContent": "..."}
    ]
  },
  "imageCaptions": [{"caption": "Figure 1: ..."}],
  "tableCaptions": [{"caption": "Table 1: ..."}],
  "conference": {"conferenceName": "...", "conferenceYear": 2025},
  "relatedIdentifiers": [{"relatedIdentifier": "10.1234/...", "relatedIdentifierType": "DOI", "relationType": "References"}]
}
```

## Directory Structure

```
poster-repo-to-json/
├── src/poster_to_json/
│   ├── extractor.py         # Thin wrapper around poster2json
│   ├── schema_converter.py  # Zenodo/Figshare → poster_schema.json
│   ├── merger.py             # Extraction base + metadata backfill (conference superseding, single description)
│   ├── cli.py                # CLI commands
│   └── poster_schema.json    # Bundled schema
├── scripts/                  # Production batch pipeline
│   ├── batch_extract_v2.py
│   ├── run_2gpu.sh
│   ├── post_batch_qc.py
│   ├── run_merge.py
│   └── clean_stale_errors.py
├── run_extraction.py         # Single-instance batch extraction
├── posters/                  # Input: classified poster PDFs
├── metadata/                 # Input: per-record JSON metadata
└── output/
    ├── extractions/          # poster2json raw output (+ _raw_text/ cache)
    ├── converted/            # Metadata converted to schema
    └── merged/               # Final merged records
```

## Operational Notes

- **Windows Task Scheduler** is used to launch `run_2gpu.sh` so it survives SSH disconnects: `schtasks /create /tn PosterExtraction /tr "wsl.exe -d Ubuntu-24.04 -e bash /path/to/run_2gpu.sh" /sc once /st 00:00 /f /rl highest && schtasks /run /tn PosterExtraction`
- **Power limits** (`nvidia-smi -pl 250`) reset on reboot — re-apply after any power event.
- **Vision OCR needs torchvision** and cuDNN is disabled at the module level to avoid a driver incompatibility on CUDA 12.9 + cuDNN 9.19.

## Related Packages

| Package | Purpose |
|---------|---------|
| [poster2json](https://github.com/fairdataihub/poster2json) | Core extraction engine (pdfalto + Qwen2-VL + Llama 3.1) |
| [poster-repo-scraper](https://github.com/fairdataihub/poster-repo-scraper) | Scrape poster metadata from Zenodo/Figshare |
| [poster-repo-qc](https://github.com/fairdataihub/poster-repo-qc) | Validate and classify posters with PosterSentry |
| [poster-json-schema](https://posters.science/schema/v0.1/poster_schema.json) | DataCite 4.6-based schema for scientific posters |

## License

MIT License - See [LICENSE](LICENSE) for details.

## Citation

```bibtex
@software{poster_repo_to_json,
  title = {poster-repo-to-json: Machine-Actionable Scientific Poster Pipeline},
  author = {{FAIR Data Innovations Hub}},
  year = {2026},
  url = {https://github.com/fairdataihub/poster-repo-to-json}
}
```
