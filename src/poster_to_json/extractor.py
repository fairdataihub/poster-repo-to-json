#!/usr/bin/env python3
"""
Poster content extractor — thin wrapper around poster2json.

Calls poster2json.extract_poster() to extract structured JSON from
scientific posters (PDF/images). poster2json handles all the heavy
lifting: pdfalto text extraction, Qwen2-VL vision OCR, and
Llama 3.1 8B JSON structuring.

See: https://github.com/fairdataihub/poster2json
"""

import json
import logging
from pathlib import Path
from typing import Dict

from tqdm import tqdm

logger = logging.getLogger(__name__)


class PosterExtractor:
    """
    Extract structured JSON from scientific posters via poster2json.

    Usage:
        extractor = PosterExtractor()
        result = extractor.extract("poster.pdf")
    """

    def __init__(self):
        """Initialize extractor (poster2json models load lazily on first call)."""
        pass

    def extract(self, poster_path: str) -> Dict:
        """
        Extract structured JSON from a poster file.

        Delegates to poster2json.extract_poster(), which handles:
        - PDF text extraction (pdfalto + PyMuPDF fallback)
        - Image OCR (Qwen2-VL)
        - JSON structuring (Llama 3.1 8B)
        - Post-processing and schema conformance

        Args:
            poster_path: Path to PDF or image file

        Returns:
            Dictionary conforming to poster_schema.json
        """
        from poster2json import extract_poster

        logger.info(f"Extracting: {poster_path}")
        return extract_poster(poster_path)

    def extract_directory(
        self,
        input_dir: str,
        output_dir: str,
        max_files: int = None,
    ) -> Dict:
        """
        Extract from all poster files in a directory.

        Args:
            input_dir: Directory containing poster files
            output_dir: Directory for output JSONs
            max_files: Maximum number of files to process

        Returns:
            Stats dict with success/error counts
        """
        input_path = Path(input_dir)
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        files = []
        for ext in [".pdf", ".png", ".jpg", ".jpeg"]:
            files.extend(input_path.rglob(f"*{ext}"))
        files = sorted(files)

        if max_files:
            files = files[:max_files]

        stats = {"success": 0, "error": 0, "total": len(files)}

        for poster_file in tqdm(files, desc="Extracting"):
            try:
                result = self.extract(str(poster_file))
                out_file = output_path / f"{poster_file.stem}_extracted.json"
                with open(out_file, "w", encoding="utf-8") as f:
                    json.dump(result, f, indent=2, ensure_ascii=False)

                if "error" not in result:
                    stats["success"] += 1
                else:
                    stats["error"] += 1
                    logger.warning(f"Extraction error for {poster_file}: {result['error']}")

            except Exception as e:
                logger.error(f"Error extracting {poster_file}: {e}")
                stats["error"] += 1

        logger.info(f"Extraction complete: {stats}")
        return stats
