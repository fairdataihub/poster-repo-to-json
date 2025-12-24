"""
Poster to JSON - Extract scientific poster content to machine-actionable JSON.

This package provides tools to:
- Extract text from PDF posters using pdfalto
- Perform OCR on image-based posters using Qwen2.5-VL
- Structure extracted content into JSON using Llama 3.1
- Convert repository metadata (Zenodo/Figshare) to posters-science schema
- Merge extracted content with bibliographic metadata

Models:
- Ollama Llama 3.1 8B Instruct: JSON structuring
- Transformers Qwen2.5-VL: Vision OCR (optional, for image-based posters)
"""

__version__ = "0.1.0"

from .extractor import PosterExtractor
from .schema_converter import SchemaConverter
from .merger import MetadataMerger

__all__ = [
    "PosterExtractor",
    "SchemaConverter",
    "MetadataMerger",
]

