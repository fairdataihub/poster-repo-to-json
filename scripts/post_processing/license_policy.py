#!/usr/bin/env python3
"""License policy -- thin shim re-exporting the package module.

The single source of truth is ``poster_to_json.license_policy`` (so the pipeline/merger and
the post-processing scripts share one implementation). Standalone scripts import
``from license_policy import ...`` and get the package version via this shim.
"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from poster_to_json.license_policy import (  # noqa: F401,E402
    ALLOWED_LICENSES,
    BLOCKED_LICENSES,
    EXTRACTED_CONTENT_FIELDS,
    classify_license,
    enforce_license,
    normalize_license,
    strip_extracted_content,
)
