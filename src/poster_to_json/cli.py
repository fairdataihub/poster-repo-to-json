#!/usr/bin/env python3
"""
Command-line interface for poster-to-json.

Usage:
    poster-to-json extract --input ./posters --output ./extractions
    poster-to-json convert --input ./metadata --output ./converted --source zenodo
    poster-to-json merge --metadata ./converted --extraction ./extractions --output ./complete
    poster-to-json pipeline --posters ./posters --metadata ./metadata --output ./output
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from datetime import datetime

from tqdm import tqdm

from .extractor import PosterExtractor
from .schema_converter import SchemaConverter
from .merger import MetadataMerger


def setup_logging(verbose: bool = False):
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def cmd_extract(args):
    """Extract content from posters."""
    extractor = PosterExtractor(
        use_transformers_ocr=getattr(args, 'transformers_vision', False),
    )
    
    input_path = Path(args.input)
    output_path = Path(args.output)
    output_path.mkdir(parents=True, exist_ok=True)
    
    # Find poster files
    if input_path.is_file():
        files = [input_path]
    else:
        files = []
        for ext in [".pdf", ".png", ".jpg", ".jpeg"]:
            files.extend(input_path.rglob(f"*{ext}"))
    
    if args.max:
        files = files[:args.max]
    
    print(f"Extracting from {len(files)} files...")
    
    results = []
    for poster_file in tqdm(files, desc="Extracting"):
        try:
            result = extractor.extract(str(poster_file))
            
            # Save result
            out_file = output_path / f"{poster_file.stem}_extracted.json"
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            
            # Success if we got valid content (even if truncated)
            has_content = (
                "posterContent" in result or 
                "titles" in result or 
                "creators" in result
            )
            results.append({
                "file": str(poster_file),
                "success": has_content and "error" not in result,
                "truncated": result.get("_truncated", False),
                "output": str(out_file),
            })
            
        except Exception as e:
            logging.error(f"Error extracting {poster_file}: {e}")
            results.append({
                "file": str(poster_file),
                "success": False,
                "error": str(e),
            })
    
    extractor.cleanup()
    
    # Summary
    success = sum(1 for r in results if r.get("success"))
    print(f"\nExtraction complete: {success}/{len(results)} successful")
    
    # Save results summary
    summary_file = output_path / "extraction_summary.json"
    with open(summary_file, "w") as f:
        json.dump(results, f, indent=2)


def cmd_convert(args):
    """Convert repository metadata to schema."""
    converter = SchemaConverter()
    
    input_path = Path(args.input)
    output_path = Path(args.output)
    
    if input_path.is_file():
        converter.convert_file(
            str(input_path),
            str(output_path),
            source=args.source,
        )
        print(f"Converted: {input_path} -> {output_path}")
    else:
        stats = converter.convert_directory(
            str(input_path),
            str(output_path),
            source=args.source,
        )
        print(f"\nConversion complete: {stats['success']} success, {stats['error']} errors")


def cmd_merge(args):
    """Merge metadata with extractions."""
    merger = MetadataMerger()
    
    stats = merger.merge_directories(
        metadata_dir=args.metadata,
        extraction_dir=args.extraction,
        output_dir=args.output,
    )
    
    print(f"\nMerge complete:")
    print(f"  Merged:          {stats['merged']}")
    print(f"  Metadata only:   {stats['metadata_only']}")
    print(f"  Extraction only: {stats['extraction_only']}")
    print(f"  Errors:          {stats['errors']}")


def cmd_pipeline(args):
    """Run full pipeline: convert -> extract -> merge."""
    output_base = Path(args.output)
    
    # Create output directories
    converted_dir = output_base / "01_converted"
    extraction_dir = output_base / "02_extractions"
    merged_dir = output_base / "03_merged"
    
    for d in [converted_dir, extraction_dir, merged_dir]:
        d.mkdir(parents=True, exist_ok=True)
    
    print("=" * 60)
    print("POSTERS-SCIENCE EXTRACTION PIPELINE")
    print("=" * 60)
    print(f"Posters: {args.posters}")
    print(f"Metadata: {args.metadata}")
    print(f"Output: {args.output}")
    print()
    
    # Step 1: Convert metadata
    if args.metadata:
        print("\n[1/3] Converting metadata...")
        converter = SchemaConverter()
        
        meta_path = Path(args.metadata)
        for subdir in ["zenodo", "figshare"]:
            source_dir = meta_path / subdir
            if source_dir.exists():
                out_dir = converted_dir / subdir
                out_dir.mkdir(exist_ok=True)
                stats = converter.convert_directory(
                    str(source_dir),
                    str(out_dir),
                    source=subdir,
                )
                print(f"  {subdir}: {stats['success']} converted")
    else:
        print("\n[1/3] No metadata provided, skipping conversion...")
    
    # Step 2: Extract content
    print("\n[2/3] Extracting poster content...")
    extractor = PosterExtractor(use_transformers_ocr=getattr(args, 'transformers_vision', False))
    
    posters_path = Path(args.posters)
    poster_files = []
    for ext in [".pdf", ".png", ".jpg", ".jpeg"]:
        poster_files.extend(posters_path.rglob(f"*{ext}"))
    
    if args.max:
        poster_files = poster_files[:args.max]
    
    for poster_file in tqdm(poster_files, desc="Extracting"):
        try:
            result = extractor.extract(str(poster_file))
            
            out_file = extraction_dir / f"{poster_file.stem}_extracted.json"
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
                
        except Exception as e:
            logging.error(f"Error: {poster_file}: {e}")
    
    extractor.cleanup()
    print(f"  Extracted: {len(poster_files)} posters")
    
    # Step 3: Merge
    if args.metadata:
        print("\n[3/3] Merging metadata with extractions...")
        merger = MetadataMerger()
        
        for subdir in ["zenodo", "figshare"]:
            meta_dir = converted_dir / subdir
            if meta_dir.exists():
                out_dir = merged_dir / subdir
                out_dir.mkdir(exist_ok=True)
                stats = merger.merge_directories(
                    str(meta_dir),
                    str(extraction_dir),
                    str(out_dir),
                )
                print(f"  {subdir}: {stats['merged']} merged")
    else:
        print("\n[3/3] No metadata to merge, copying extractions...")
        import shutil
        for f in extraction_dir.glob("*.json"):
            shutil.copy(f, merged_dir / f.name)
    
    # Summary
    print("\n" + "=" * 60)
    print("PIPELINE COMPLETE")
    print("=" * 60)
    print(f"Output directory: {output_base}")
    print(f"  - Converted metadata: {converted_dir}")
    print(f"  - Extractions: {extraction_dir}")
    print(f"  - Merged records: {merged_dir}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="poster-to-json",
        description="Extract scientific poster content to machine-actionable JSON",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # Extract command
    extract_parser = subparsers.add_parser(
        "extract",
        help="Extract content from poster files",
    )
    extract_parser.add_argument(
        "-i", "--input",
        required=True,
        help="Input poster file or directory",
    )
    extract_parser.add_argument(
        "-o", "--output",
        default="./extractions",
        help="Output directory for extracted JSON",
    )
    extract_parser.add_argument(
        "--max",
        type=int,
        help="Maximum files to process",
    )
    extract_parser.add_argument(
        "--transformers-vision",
        action="store_true",
        help="Use Transformers Qwen2-VL for vision OCR instead of Ollama (requires more GPU memory)",
    )
    extract_parser.set_defaults(func=cmd_extract)
    
    # Convert command
    convert_parser = subparsers.add_parser(
        "convert",
        help="Convert repository metadata to posters-science schema",
    )
    convert_parser.add_argument(
        "-i", "--input",
        required=True,
        help="Input metadata file or directory",
    )
    convert_parser.add_argument(
        "-o", "--output",
        required=True,
        help="Output file or directory",
    )
    convert_parser.add_argument(
        "-s", "--source",
        choices=["zenodo", "figshare", "auto"],
        default="auto",
        help="Source repository (default: auto-detect)",
    )
    convert_parser.set_defaults(func=cmd_convert)
    
    # Merge command
    merge_parser = subparsers.add_parser(
        "merge",
        help="Merge metadata with extractions",
    )
    merge_parser.add_argument(
        "-m", "--metadata",
        required=True,
        help="Directory with converted metadata",
    )
    merge_parser.add_argument(
        "-e", "--extraction",
        required=True,
        help="Directory with extraction results",
    )
    merge_parser.add_argument(
        "-o", "--output",
        required=True,
        help="Output directory for merged records",
    )
    merge_parser.set_defaults(func=cmd_merge)
    
    # Pipeline command
    pipeline_parser = subparsers.add_parser(
        "pipeline",
        help="Run full extraction pipeline",
    )
    pipeline_parser.add_argument(
        "-p", "--posters",
        required=True,
        help="Directory containing poster files",
    )
    pipeline_parser.add_argument(
        "-m", "--metadata",
        help="Directory containing repository metadata (optional)",
    )
    pipeline_parser.add_argument(
        "-o", "--output",
        default="./pipeline_output",
        help="Output directory",
    )
    pipeline_parser.add_argument(
        "--max",
        type=int,
        help="Maximum posters to process",
    )
    pipeline_parser.add_argument(
        "--transformers-vision",
        action="store_true",
        help="Use Transformers Qwen2-VL for vision OCR (requires more GPU memory)",
    )
    pipeline_parser.set_defaults(func=cmd_pipeline)
    
    # Parse and execute
    args = parser.parse_args()
    setup_logging(args.verbose)
    
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

