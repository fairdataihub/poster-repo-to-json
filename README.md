# poster-repo-to-json

> **Part of the Machine-Actionable Scientific Poster Initiative**

Full pipeline for converting scientific posters into machine-actionable JSON. Uses [poster2json](https://github.com/fairdataihub/poster2json) for extraction (pdfalto + Qwen2-VL + Llama 3.1 8B), then enriches with Zenodo/Figshare repository metadata. Output conforms to the [poster-json-schema](https://posters.science/schema/v0.1/poster_schema.json) (DataCite 4.6 with poster extensions).

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
                                               (backfill only)
```

**poster2json output is PRIMARY.** Repository metadata only fills in fields that poster2json left empty (DOIs, licenses, funding, etc.) — it never overwrites extracted content.

## Installation

```bash
# Install from GitHub (pulls poster2json, poster-scraper, poster-qc automatically)
pip install git+https://github.com/fairdataihub/poster-repo-to-json.git

# Or install locally for development
git clone https://github.com/fairdataihub/poster-repo-to-json.git
cd poster-repo-to-json
pip install -e .
```

### Prerequisites

poster2json requires:
- **CUDA GPU** with 16GB+ VRAM
- **pdfalto** for PDF layout analysis (optional but recommended)

```bash
# pdfalto
git clone https://github.com/kermitt2/pdfalto.git
cd pdfalto && mkdir build && cd build && cmake .. && make
export PDFALTO_PATH=/path/to/pdfalto/build/pdfalto
```

## Quick Start

### Batch Extraction (Recommended)

The `run_extraction.py` script handles the full pipeline with resume support:

```bash
# Default paths: posters/ and metadata/ in the repo directory
python run_extraction.py

# Custom paths
python run_extraction.py \
    --posters /storage/poster-pdf-meta_downloads \
    --metadata /storage/poster-pdf-meta_metadata \
    --output ./output

# Process first 100 only
python run_extraction.py --max 100

# Dry run — see what would be processed
python run_extraction.py --dry-run
```

**Resume after crash:** Just re-run the same command. Already-completed files are skipped automatically.

### CLI Commands

```bash
# Extract from poster files via poster2json
poster-to-json extract --input ./posters --output ./extractions

# Convert repository metadata to schema
poster-to-json convert --input ./metadata/zenodo --output ./converted --source zenodo

# Merge extractions with metadata (extraction is base, metadata backfills)
poster-to-json merge --extractions ./extractions --metadata ./converted --output ./merged

# Run pipeline (extract -> convert -> merge)
poster-to-json pipeline --posters ./posters --metadata ./metadata --output ./output

# Full end-to-end (scrape -> download -> QC -> extract -> merge)
poster-to-json full-pipeline --output ./output
```

## Python API

```python
from poster_to_json import PosterExtractor, SchemaConverter, MetadataMerger

# Extract content via poster2json
extractor = PosterExtractor()
extraction = extractor.extract("poster.pdf")
print(extraction["content"]["sections"])

# Convert repository metadata
converter = SchemaConverter()
metadata = converter.convert_zenodo(raw_zenodo_record)

# Merge: extraction is base, metadata backfills gaps
merger = MetadataMerger()
complete = merger.merge(extraction, metadata)
```

## Output Schema

Output conforms to the **poster-json-schema** (DataCite 4.6 with poster extensions):

```json
{
  "$schema": "https://posters.science/schema/v0.1/poster_schema.json",
  "identifiers": [{"identifier": "10.5281/zenodo.12345678", "identifierType": "DOI"}],
  "titles": [{"title": "Machine Learning for Scientific Poster Analysis"}],
  "creators": [
    {
      "name": "Smith, John",
      "nameIdentifiers": [{"nameIdentifier": "0000-0001-...", "nameIdentifierScheme": "ORCID"}],
      "affiliation": [{"name": "University of Example"}]
    }
  ],
  "content": {
    "sections": [
      {"sectionTitle": "Introduction", "sectionContent": "..."},
      {"sectionTitle": "Methods", "sectionContent": "..."},
      {"sectionTitle": "Results", "sectionContent": "..."}
    ]
  },
  "imageCaptions": [{"caption": "Figure 1: Overview of the approach"}],
  "tableCaptions": [{"caption": "Table 1: Experimental results"}],
  "conference": {"conferenceName": "ICML 2025", "conferenceYear": 2025}
}
```

## Directory Structure

```
poster-repo-to-json/
├── src/poster_to_json/
│   ├── extractor.py         # Thin wrapper around poster2json
│   ├── schema_converter.py  # Zenodo/Figshare → poster_schema.json
│   ├── merger.py             # poster2json base + metadata backfill
│   ├── cli.py                # CLI commands
│   └── poster_schema.json    # Bundled schema
├── run_extraction.py         # Batch extraction with resume
├── posters/                  # Input: classified poster PDFs
│   ├── zenodo/
│   └── figshare/
├── metadata/                 # Input: per-record JSON metadata
│   ├── zenodo/
│   └── figshare/
└── output/                   # Output: created by run_extraction.py
    ├── extractions/          # poster2json raw output
    ├── converted/            # Metadata converted to schema
    └── merged/               # Final merged records
```

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
