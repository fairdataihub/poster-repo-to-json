#!/usr/bin/env python3
"""
Schema converter - Converts repository metadata to posters-science JSON schema.

Supports:
- Zenodo metadata format
- Figshare metadata format
- DataCite metadata format

Output conforms to the posters-science schema (based on DataCite with poster extensions).
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from tqdm import tqdm

logger = logging.getLogger(__name__)


class SchemaConverter:
    """Converts repository metadata to posters-science schema."""
    
    SCHEMA_URL = "https://posters.science/schema/v0.1/poster_schema.json"
    
    def __init__(self):
        """Initialize converter."""
        pass
    
    def convert_zenodo(self, record: Dict) -> Dict:
        """
        Convert Zenodo record to posters-science schema.
        
        Args:
            record: Raw Zenodo record (from API or zenodo.json)
            
        Returns:
            Converted record in posters-science schema
        """
        metadata = record.get("metadata", {})
        
        result = {
            "$schema": self.SCHEMA_URL,
        }
        
        # DOI and identifiers
        doi = record.get("doi")
        if doi:
            result["doi"] = doi
            result["identifiers"] = [{"identifier": doi, "identifierType": "DOI"}]
        
        # Zenodo record ID
        zenodo_id = record.get("id")
        if zenodo_id:
            if "identifiers" not in result:
                result["identifiers"] = []
            result["identifiers"].append({
                "identifier": str(zenodo_id),
                "identifierType": "Zenodo"
            })
        
        # Creators
        creators = []
        for creator in metadata.get("creators", []):
            creator_entry = {
                "name": creator.get("name", ""),
                "nameType": "Personal",
            }
            
            if creator.get("affiliation"):
                creator_entry["affiliation"] = [{"name": creator["affiliation"]}]
            
            if creator.get("orcid"):
                creator_entry["nameIdentifiers"] = [{
                    "nameIdentifier": creator["orcid"],
                    "nameIdentifierScheme": "ORCID",
                }]
            
            creators.append(creator_entry)
        
        if creators:
            result["creators"] = creators
        
        # Title
        title = metadata.get("title")
        if title:
            result["titles"] = [{"title": title}]
        
        # Publisher
        result["publisher"] = {"name": "Zenodo"}
        
        # Publication year
        pub_date = metadata.get("publication_date")
        if pub_date:
            try:
                year = int(pub_date[:4])
                result["publicationYear"] = year
            except (ValueError, TypeError):
                pass
            result["dates"] = [{"date": pub_date, "dateType": "Issued"}]
        
        # Resource type
        result["types"] = {
            "resourceType": "Scientific Poster",
            "resourceTypeGeneral": "Image"
        }
        
        # Description/Abstract
        description = metadata.get("description")
        if description:
            # Clean HTML tags
            clean_desc = re.sub(r"<[^>]+>", "", description)
            result["descriptions"] = [{
                "description": clean_desc,
                "descriptionType": "Abstract"
            }]
        
        # Keywords
        keywords = metadata.get("keywords", [])
        if keywords:
            result["subjects"] = [{"subject": kw} for kw in keywords]
        
        # Language
        language = metadata.get("language")
        if language:
            result["language"] = language
        
        # License
        license_info = metadata.get("license")
        if license_info:
            result["rightsList"] = [{
                "rights": license_info.get("id", ""),
                "rightsIdentifier": license_info.get("id", ""),
            }]
        
        # Access rights
        access_right = metadata.get("access_right")
        if access_right:
            result["accessRights"] = access_right
        
        # Conference/Meeting information
        meeting = metadata.get("meeting", {})
        if meeting:
            conference = {}
            if meeting.get("title"):
                conference["name"] = meeting["title"]
            if meeting.get("acronym"):
                conference["acronym"] = meeting["acronym"]
            if meeting.get("dates"):
                conference["dates"] = meeting["dates"]
            if meeting.get("place"):
                conference["place"] = meeting["place"]
            if meeting.get("url"):
                conference["url"] = meeting["url"]
            if meeting.get("session"):
                conference["session"] = meeting["session"]
            
            if conference:
                result["conference"] = conference
        
        # Funding/Grants
        grants = metadata.get("grants", [])
        if grants:
            funders = []
            for grant in grants:
                funder_entry = {}
                if grant.get("funder", {}).get("name"):
                    funder_entry["funderName"] = grant["funder"]["name"]
                if grant.get("title"):
                    funder_entry["awardTitle"] = grant["title"]
                if grant.get("code"):
                    funder_entry["awardNumber"] = grant["code"]
                if funder_entry:
                    funders.append(funder_entry)
            
            if funders:
                result["fundingReferences"] = funders
        
        # Related identifiers
        related = metadata.get("related_identifiers", [])
        if related:
            result["relatedIdentifiers"] = [
                {
                    "relatedIdentifier": r.get("identifier"),
                    "relatedIdentifierType": r.get("scheme", "").upper(),
                    "relationType": r.get("relation", "").replace("_", " ").title(),
                }
                for r in related if r.get("identifier")
            ]
        
        # File information
        files = record.get("files", [])
        if files:
            result["files"] = [
                {
                    "filename": f.get("key"),
                    "size": f.get("size"),
                    "checksum": f.get("checksum"),
                    "downloadUrl": f.get("links", {}).get("self"),
                }
                for f in files
            ]
        
        return result
    
    def convert_figshare(self, record: Dict) -> Dict:
        """
        Convert Figshare record to posters-science schema.
        
        Args:
            record: Raw Figshare record
            
        Returns:
            Converted record in posters-science schema
        """
        result = {
            "$schema": self.SCHEMA_URL,
        }
        
        # DOI and identifiers
        doi = record.get("doi")
        if doi:
            result["doi"] = doi
            result["identifiers"] = [{"identifier": doi, "identifierType": "DOI"}]
        
        # Figshare ID
        figshare_id = record.get("id")
        if figshare_id:
            if "identifiers" not in result:
                result["identifiers"] = []
            result["identifiers"].append({
                "identifier": str(figshare_id),
                "identifierType": "Figshare"
            })
        
        # Creators
        creators = []
        for author in record.get("authors", []):
            creator_entry = {
                "name": author.get("full_name", ""),
                "nameType": "Personal",
            }
            
            if author.get("orcid_id"):
                creator_entry["nameIdentifiers"] = [{
                    "nameIdentifier": author["orcid_id"],
                    "nameIdentifierScheme": "ORCID",
                }]
            
            creators.append(creator_entry)
        
        if creators:
            result["creators"] = creators
        
        # Title
        title = record.get("title")
        if title:
            result["titles"] = [{"title": title}]
        
        # Publisher
        result["publisher"] = {"name": "Figshare"}
        
        # Publication year
        pub_date = record.get("published_date")
        if pub_date:
            try:
                year = int(pub_date[:4])
                result["publicationYear"] = year
            except (ValueError, TypeError):
                pass
            result["dates"] = [{"date": pub_date, "dateType": "Issued"}]
        
        # Resource type
        result["types"] = {
            "resourceType": "Scientific Poster",
            "resourceTypeGeneral": "Image"
        }
        
        # Description
        description = record.get("description")
        if description:
            clean_desc = re.sub(r"<[^>]+>", "", description)
            result["descriptions"] = [{
                "description": clean_desc,
                "descriptionType": "Abstract"
            }]
        
        # Tags/Keywords
        tags = record.get("tags", [])
        if tags:
            result["subjects"] = [{"subject": tag} for tag in tags]
        
        # Categories
        categories = record.get("categories", [])
        if categories:
            if "subjects" not in result:
                result["subjects"] = []
            for cat in categories:
                if isinstance(cat, dict):
                    result["subjects"].append({"subject": cat.get("title", "")})
                else:
                    result["subjects"].append({"subject": str(cat)})
        
        # License
        license_info = record.get("license")
        if license_info:
            result["rightsList"] = [{
                "rights": license_info.get("name", ""),
                "rightsURI": license_info.get("url", ""),
            }]
        
        # Access
        if record.get("is_public"):
            result["accessRights"] = "open"
        else:
            result["accessRights"] = "restricted"
        
        # Funding
        funding = record.get("funding_list", [])
        if funding:
            funders = []
            for f in funding:
                funder_entry = {}
                if f.get("funder_name"):
                    funder_entry["funderName"] = f["funder_name"]
                if f.get("title"):
                    funder_entry["awardTitle"] = f["title"]
                if f.get("grant_code"):
                    funder_entry["awardNumber"] = f["grant_code"]
                if funder_entry:
                    funders.append(funder_entry)
            
            if funders:
                result["fundingReferences"] = funders
        
        # Files
        files = record.get("files", [])
        if files:
            result["files"] = [
                {
                    "filename": f.get("name"),
                    "size": f.get("size"),
                    "downloadUrl": f.get("download_url"),
                }
                for f in files
            ]
        
        return result
    
    def detect_source(self, record: Dict) -> str:
        """
        Detect the source repository of a record.
        
        Args:
            record: Raw metadata record
            
        Returns:
            'zenodo', 'figshare', or 'unknown'
        """
        # Zenodo indicators
        if "metadata" in record and record.get("conceptrecid"):
            return "zenodo"
        if record.get("links", {}).get("self", "").startswith("https://zenodo"):
            return "zenodo"
        
        # Figshare indicators
        if "url_public_api" in record or "figshare" in str(record.get("url", "")):
            return "figshare"
        if "defined_type" in record:
            return "figshare"
        
        return "unknown"
    
    def convert(self, record: Dict, source: Optional[str] = None) -> Dict:
        """
        Convert a record from any supported source.
        
        Args:
            record: Raw metadata record
            source: Source repository ('zenodo', 'figshare', or auto-detect)
            
        Returns:
            Converted record in posters-science schema
        """
        if source is None:
            source = self.detect_source(record)
        
        if source == "zenodo":
            return self.convert_zenodo(record)
        elif source == "figshare":
            return self.convert_figshare(record)
        else:
            logger.warning(f"Unknown source: {source}")
            return {"error": f"Unknown source: {source}", "original": record}
    
    def convert_file(
        self,
        input_file: str,
        output_file: str,
        source: Optional[str] = None,
    ) -> Dict:
        """
        Convert a JSON file containing repository metadata.
        
        Args:
            input_file: Input JSON file path
            output_file: Output JSON file path
            source: Source repository (auto-detect if None)
            
        Returns:
            Converted record
        """
        with open(input_file, "r", encoding="utf-8") as f:
            record = json.load(f)
        
        converted = self.convert(record, source)
        
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(converted, f, indent=2, ensure_ascii=False)
        
        return converted
    
    def convert_directory(
        self,
        input_dir: str,
        output_dir: str,
        source: Optional[str] = None,
    ) -> Dict:
        """
        Convert all JSON files in a directory.
        
        Args:
            input_dir: Input directory
            output_dir: Output directory
            source: Source repository (auto-detect if None)
            
        Returns:
            Statistics dictionary
        """
        input_path = Path(input_dir)
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        json_files = list(input_path.glob("*.json"))
        logger.info(f"Converting {len(json_files)} files from {input_dir}")
        
        stats = {"success": 0, "error": 0}
        
        for json_file in tqdm(json_files, desc="Converting"):
            try:
                output_file = output_path / json_file.name
                self.convert_file(str(json_file), str(output_file), source)
                stats["success"] += 1
            except Exception as e:
                logger.error(f"Error converting {json_file}: {e}")
                stats["error"] += 1
        
        logger.info(f"Conversion complete: {stats['success']} success, {stats['error']} errors")
        return stats

