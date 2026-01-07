# posters-science-repo-to-json (Beta)

> **Part of the Machine-Actionable Scientific Poster Initiative**

Extract scientific poster content to machine-actionable JSON using LLMs. Uses Ollama Llama 3.1 8B for JSON structuring and Qwen2-VL-7B via Transformers for vision OCR.

⚠️ **Beta Software**: This package is under active development. APIs may change.

## Machine-Actionable Posters

Scientific posters contain valuable research findings that are often inaccessible to computational analysis. This package transforms poster content into structured, machine-actionable JSON following the **posters-science schema** (based on DataCite with poster-specific extensions).

### Pipeline Position

1. **Collect** posters ([posters-science-repo-scraper](https://github.com/fairdataihub/posters-science-repo-scraper))
2. **Validate** and classify ([posters-science-repo-qc](https://github.com/fairdataihub/posters-science-repo-qc))
3. **Extract** to machine-actionable JSON (this package)

## Installation

```bash
# Install from GitHub (basic - Ollama only)
pip install git+https://github.com/fairdataihub/posters-science-repo-to-json.git

# Install with Transformers OCR support (Qwen2-VL)
pip install "poster-to-json[transformers] @ git+https://github.com/fairdataihub/posters-science-repo-to-json.git"

# Or install locally for development
git clone https://github.com/fairdataihub/posters-science-repo-to-json.git
cd posters-science-repo-to-json
pip install -e ".[transformers]"
```

### Prerequisites

1. **Ollama** - Required for Llama 3.1 JSON structuring
```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.1:8b-instruct-q8_0
```

2. **pdfalto** (optional but recommended) - For PDF layout analysis
```bash
git clone https://github.com/kermitt2/pdfalto.git
cd pdfalto && mkdir build && cd build
cmake .. && make
export PDFALTO_PATH=/path/to/pdfalto/build/pdfalto
```

3. **CUDA GPU** - Recommended for Qwen2-VL OCR (8GB+ VRAM)

## Quick Start

### Extract Content from Posters

```bash
# Extract from PDF/image files
poster-to-json extract --input ./posters --output ./extractions

# Use Ollama vision instead of Transformers (lower quality but simpler)
poster-to-json extract --input ./posters --output ./extractions --ollama-vision
```

### Convert Repository Metadata

```bash
# Convert Zenodo metadata to posters-science schema
poster-to-json convert --input ./metadata/zenodo --output ./converted --source zenodo

# Convert Figshare metadata
poster-to-json convert --input ./metadata/figshare --output ./converted --source figshare
```

### Merge Extracted Content with Metadata

```bash
poster-to-json merge \
    --metadata ./converted \
    --extraction ./extractions \
    --output ./complete
```

### Full Pipeline

```bash
# Run complete extraction pipeline
poster-to-json pipeline \
    --posters ./downloads/zenodo \
    --metadata ./metadata \
    --output ./pipeline_output
```

## Python API

```python
from poster_to_json import PosterExtractor, SchemaConverter, MetadataMerger

# Extract content from a poster
extractor = PosterExtractor(use_transformers_ocr=True)
result = extractor.extract("poster.pdf")
print(result["posterContent"]["sections"])
extractor.cleanup()

# Convert repository metadata
converter = SchemaConverter()
converted = converter.convert_zenodo(raw_zenodo_record)

# Merge extraction with metadata
merger = MetadataMerger()
complete = merger.merge(converted, result)
```

## Output Schema

The output conforms to the **posters-science schema** (based on DataCite with poster-specific extensions):

```json
{
  "$schema": "https://posters.science/schema/v0.1/poster_schema.json",
  "doi": "10.5281/zenodo.12345678",
  "titles": [{"title": "Machine Learning for Scientific Poster Analysis"}],
  "creators": [
    {
      "name": "Smith, John",
      "affiliation": [{"name": "University of Example"}]
    }
  ],
  "posterContent": {
    "sections": [
      {"sectionTitle": "Introduction", "sectionContent": "..."},
      {"sectionTitle": "Methods", "sectionContent": "..."},
      {"sectionTitle": "Results", "sectionContent": "..."},
      {"sectionTitle": "Conclusions", "sectionContent": "..."}
    ]
  },
  "imageCaption": [{"caption1": "Figure 1: ..."}],
  "tableCaption": [{"caption1": "Table 1: ..."}],
  "conference": {
    "name": "International Conference on Machine Learning",
    "dates": "July 2025",
    "place": "Vienna, Austria"
  }
}
```

## Models

| Model | Purpose | Requirements |
|-------|---------|--------------|
| Llama 3.1 8B (Ollama) | JSON structuring | ~10GB VRAM |
| Qwen2-VL-7B (Transformers) | Vision OCR | ~16GB VRAM |

## Pipeline Output Structure

```
pipeline_output/
├── 01_converted/           # Repository metadata → posters-science schema
│   ├── zenodo/
│   └── figshare/
├── 02_extractions/         # Extracted poster content
│   ├── poster1_extracted.json
│   └── poster2_extracted.json
└── 03_merged/              # Complete machine-actionable records
    ├── zenodo/
    └── figshare/
```

## Related Packages

| Package | Purpose |
|---------|---------|
| [posters-science-repo-scraper](https://github.com/fairdataihub/posters-science-repo-scraper) | Scrape posters from repositories |
| [posters-science-repo-qc](https://github.com/fairdataihub/posters-science-repo-qc) | Validate and classify posters |
| [machine-actionable-posterextraction-beta](https://github.com/fairdataihub/machine-actionable-posterextraction-beta) | Core extraction API (Docker) |

## License

MIT License - See [LICENSE](LICENSE) for details.

## Citation

```bibtex
@software{posters_science_to_json,
  title = {Posters.science JSON Extraction Pipeline},
  author = {FAIRDataIHub},
  year = {2025},
  url = {https://github.com/fairdataihub/posters-science-repo-to-json},
  note = {Beta release - Machine-Actionable Scientific Poster Initiative}
}
```
