"""
poster-to-json — Full pipeline for scientific poster metadata extraction.

Pipeline:
1. Scrape poster metadata from Zenodo/Figshare (via poster-scraper)
2. Validate and classify posters (via poster-qc)
3. Extract structured JSON from posters (via poster2json)
4. Convert repository metadata to posters-science schema
5. Merge: poster2json output is PRIMARY, repo metadata backfills gaps

Output conforms to poster_schema.json (DataCite-based with poster extensions).
"""

__version__ = "0.34.0"

from .extractor import PosterExtractor
from .schema_converter import SchemaConverter, load_bundled_schema
from .merger import MetadataMerger

__all__ = [
    "PosterExtractor",
    "SchemaConverter",
    "MetadataMerger",
    "load_bundled_schema",
]
