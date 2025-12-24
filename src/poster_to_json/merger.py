#!/usr/bin/env python3
"""
Metadata merger - Combines extracted poster content with bibliographic metadata.

Merges:
- Bibliographic metadata from repository (Zenodo/Figshare) - converted to schema
- Extracted content from poster (posterContent, captions)

The extracted content takes priority for content fields, while repository
metadata provides bibliographic information.
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

from tqdm import tqdm

logger = logging.getLogger(__name__)


class MetadataMerger:
    """Merges extracted poster content with bibliographic metadata."""
    
    # Fields to take from extraction (poster content)
    EXTRACTION_FIELDS = [
        "posterContent",
        "imageCaption",
        "tableCaption",
        "unstructuredContent",
    ]
    
    # Fields to prefer from repository metadata
    METADATA_FIELDS = [
        "$schema",
        "doi",
        "identifiers",
        "creators",
        "titles",
        "publisher",
        "publicationYear",
        "dates",
        "types",
        "descriptions",
        "subjects",
        "language",
        "formats",
        "rightsList",
        "accessRights",
        "conference",
        "fundingReferences",
        "relatedIdentifiers",
        "files",
    ]
    
    def __init__(self):
        """Initialize merger."""
        pass
    
    def merge(
        self,
        metadata: Dict,
        extraction: Dict,
        prefer_extraction_creators: bool = False,
    ) -> Dict:
        """
        Merge metadata with extraction results.
        
        Args:
            metadata: Converted repository metadata (posters-science schema)
            extraction: Extracted content from poster
            prefer_extraction_creators: If True, use extracted creators over metadata
            
        Returns:
            Merged record with both bibliographic and content information
        """
        result = {}
        
        # Start with metadata fields
        for field in self.METADATA_FIELDS:
            if field in metadata:
                result[field] = metadata[field]
        
        # Add extraction fields (these are unique to extracted content)
        for field in self.EXTRACTION_FIELDS:
            if field in extraction and extraction[field]:
                # Filter out empty captions
                if field in ["imageCaption", "tableCaption"]:
                    captions = extraction[field]
                    valid_captions = []
                    for cap in captions:
                        # Only keep captions with non-empty values
                        valid_cap = {k: v for k, v in cap.items() if v and str(v).strip()}
                        if valid_cap:
                            valid_captions.append(valid_cap)
                    if valid_captions:
                        result[field] = valid_captions
                elif field == "posterContent":
                    # Filter out sections with empty content
                    pc = extraction[field]
                    if isinstance(pc, dict) and "sections" in pc:
                        valid_sections = []
                        for section in pc["sections"]:
                            title = section.get("sectionTitle", "").strip()
                            content = section.get("sectionContent", "").strip()
                            if title and content:
                                valid_sections.append({
                                    "sectionTitle": title,
                                    "sectionContent": content
                                })
                        if valid_sections:
                            result[field] = {"sections": valid_sections}
                            if pc.get("unstructuredContent"):
                                result[field]["unstructuredContent"] = pc["unstructuredContent"]
                    else:
                        result[field] = pc
                else:
                    result[field] = extraction[field]
        
        # Handle creators specially
        if prefer_extraction_creators and "creators" in extraction:
            # Use extracted creators if they look more complete
            ext_creators = extraction.get("creators", [])
            meta_creators = metadata.get("creators", [])
            
            if ext_creators and (
                len(ext_creators) > len(meta_creators) or
                any(c.get("affiliation") for c in ext_creators)
            ):
                result["creators"] = ext_creators
        
        # Merge titles if extraction has a different title
        if "titles" in extraction:
            ext_titles = extraction.get("titles", [])
            if ext_titles:
                # Check if extracted title is different
                meta_title = result.get("titles", [{}])[0].get("title", "")
                ext_title = ext_titles[0].get("title", "")
                
                if ext_title and ext_title != meta_title:
                    # Keep both, extracted as alternate
                    if "titles" not in result:
                        result["titles"] = []
                    result["titles"].append({
                        "title": ext_title,
                        "titleType": "Other"  # Schema-valid type for extracted titles
                    })
        
        # Add extraction metadata
        result["_merged_at"] = datetime.now().isoformat()
        if "_extraction_source" in extraction:
            result["_extraction_source"] = extraction["_extraction_source"]
        if "_extracted_at" in extraction:
            result["_extracted_at"] = extraction["_extracted_at"]
        
        return result
    
    def merge_files(
        self,
        metadata_file: str,
        extraction_file: str,
        output_file: str,
    ) -> Dict:
        """
        Merge metadata and extraction JSON files.
        
        Args:
            metadata_file: Path to converted metadata JSON
            extraction_file: Path to extracted content JSON
            output_file: Path for merged output
            
        Returns:
            Merged record
        """
        with open(metadata_file, "r", encoding="utf-8") as f:
            metadata = json.load(f)
        
        with open(extraction_file, "r", encoding="utf-8") as f:
            extraction = json.load(f)
        
        merged = self.merge(metadata, extraction)
        
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)
        
        return merged
    
    def merge_directories(
        self,
        metadata_dir: str,
        extraction_dir: str,
        output_dir: str,
        match_by: str = "stem",
    ) -> Dict:
        """
        Merge all matching files from two directories.
        
        Args:
            metadata_dir: Directory with converted metadata files
            extraction_dir: Directory with extraction files
            output_dir: Output directory for merged files
            match_by: How to match files ('stem' = filename without extension)
            
        Returns:
            Statistics dictionary
        """
        meta_path = Path(metadata_dir)
        ext_path = Path(extraction_dir)
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        
        # Index metadata files
        meta_files = {}
        for f in meta_path.glob("*.json"):
            key = f.stem
            # Handle common naming patterns
            for suffix in ["_converted", "_metadata", ""]:
                clean_key = key.replace(suffix, "")
                meta_files[clean_key] = f
        
        # Index extraction files
        ext_files = {}
        for f in ext_path.glob("*.json"):
            key = f.stem
            # Remove common suffixes
            for suffix in ["_extracted", "_extraction", ""]:
                clean_key = key.replace(suffix, "")
            
            # Extract repository ID from patterns like:
            # - zenodo_3905326_ohbm_2020 -> 3905326
            # - figshare_12345_name -> 12345
            id_match = re.match(r'^(?:zenodo|figshare)_(\d+)', clean_key)
            if id_match:
                record_id = id_match.group(1)
                ext_files[record_id] = f
            else:
                ext_files[clean_key] = f
        
        # Find matches
        matched_keys = set(meta_files.keys()) & set(ext_files.keys())
        logger.info(f"Found {len(matched_keys)} matching files to merge")
        
        stats = {"merged": 0, "metadata_only": 0, "extraction_only": 0, "errors": 0}
        
        for key in tqdm(matched_keys, desc="Merging"):
            try:
                meta_file = meta_files[key]
                ext_file = ext_files[key]
                out_file = out_path / f"{key}_complete.json"
                
                self.merge_files(str(meta_file), str(ext_file), str(out_file))
                stats["merged"] += 1
                
            except Exception as e:
                logger.error(f"Error merging {key}: {e}")
                stats["errors"] += 1
        
        # Handle unmatched files
        for key in set(meta_files.keys()) - matched_keys:
            stats["metadata_only"] += 1
        
        for key in set(ext_files.keys()) - matched_keys:
            stats["extraction_only"] += 1
        
        logger.info(f"Merge complete: {stats}")
        return stats
    
    @staticmethod
    def validate_merged(record: Dict) -> List[str]:
        """
        Validate a merged record for completeness.
        
        Args:
            record: Merged record to validate
            
        Returns:
            List of missing required fields
        """
        required_fields = [
            "doi",
            "titles",
            "creators",
            "posterContent",
        ]
        
        missing = []
        for field in required_fields:
            if field not in record or not record[field]:
                missing.append(field)
        
        # Check posterContent structure
        if "posterContent" in record:
            pc = record["posterContent"]
            if not isinstance(pc, dict):
                missing.append("posterContent (invalid type)")
            elif "sections" not in pc or not pc["sections"]:
                missing.append("posterContent.sections")
        
        return missing

