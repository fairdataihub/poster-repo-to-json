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

__version__ = "0.39.0"

from .extractor import PosterExtractor
from .schema_converter import SchemaConverter, load_bundled_schema
from .merger import MetadataMerger
from .version_linking import (
    VersionFamily,
    apply_version_info,
    from_figshare,
    from_record,
    from_zenodo,
    link_families,
)

__all__ = [
    "PosterExtractor",
    "SchemaConverter",
    "MetadataMerger",
    "load_bundled_schema",
    "VersionFamily",
    "apply_version_info",
    "from_record",
    "from_zenodo",
    "from_figshare",
    "link_families",
]
