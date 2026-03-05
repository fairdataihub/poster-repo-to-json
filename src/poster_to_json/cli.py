#!/usr/bin/env python3
"""
Command-line interface for poster-to-json.

Usage:
    poster-to-json extract --input ./posters --output ./extractions
    poster-to-json convert --input ./metadata --output ./converted --source zenodo
    poster-to-json merge --extractions ./extractions --metadata ./converted --output ./merged
    poster-to-json pipeline --posters ./posters --metadata ./metadata --output ./output
    poster-to-json full-pipeline --output ./output  (scrape + QC + extract end-to-end)
"""

import argparse
import json
import logging
import sys
from pathlib import Path

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
    """Extract content from posters using poster2json."""
    extractor = PosterExtractor()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if input_path.is_file():
        output_path.mkdir(parents=True, exist_ok=True)
        result = extractor.extract(str(input_path))
        out_file = output_path / f"{input_path.stem}_extracted.json"
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        status = "OK" if "error" not in result else f"ERROR: {result['error']}"
        print(f"{input_path.name}: {status} -> {out_file}")
    else:
        stats = extractor.extract_directory(
            str(input_path), str(output_path), max_files=args.max,
        )
        print(f"\nExtraction complete: {stats['success']}/{stats['total']} successful")


def cmd_convert(args):
    """Convert repository metadata to schema."""
    converter = SchemaConverter()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if input_path.is_file():
        converter.convert_file(str(input_path), str(output_path), source=args.source)
        print(f"Converted: {input_path} -> {output_path}")
    else:
        stats = converter.convert_directory(str(input_path), str(output_path), source=args.source)
        print(f"\nConversion complete: {stats['success']} success, {stats['error']} errors")


def cmd_merge(args):
    """Merge poster2json extractions with repository metadata."""
    merger = MetadataMerger()

    stats = merger.merge_directories(
        extraction_dir=args.extractions,
        metadata_dir=args.metadata,
        output_dir=args.output,
    )

    print(f"\nMerge complete:")
    print(f"  Merged:          {stats['merged']}")
    print(f"  Extraction only: {stats['extraction_only']}")
    print(f"  Metadata only:   {stats['metadata_only']}")
    print(f"  Errors:          {stats['errors']}")


def cmd_pipeline(args):
    """Run pipeline: extract (poster2json) -> convert metadata -> merge."""
    output_base = Path(args.output)

    extraction_dir = output_base / "01_extractions"
    converted_dir = output_base / "02_converted"
    merged_dir = output_base / "03_merged"

    for d in [extraction_dir, converted_dir, merged_dir]:
        d.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("POSTER-TO-JSON PIPELINE")
    print("=" * 60)
    print(f"Posters:  {args.posters}")
    print(f"Metadata: {args.metadata or '(none)'}")
    print(f"Output:   {args.output}")
    print()

    # Step 1: Extract with poster2json (PRIMARY source)
    print("[1/3] Extracting poster content via poster2json...")
    extractor = PosterExtractor()
    stats = extractor.extract_directory(
        str(args.posters), str(extraction_dir), max_files=args.max,
    )
    print(f"  Extracted: {stats['success']}/{stats['total']}")

    # Step 2: Convert repository metadata
    if args.metadata:
        print("\n[2/3] Converting repository metadata...")
        converter = SchemaConverter()
        meta_path = Path(args.metadata)

        for subdir in ["zenodo", "figshare"]:
            source_dir = meta_path / subdir
            if source_dir.exists():
                out_dir = converted_dir / subdir
                out_dir.mkdir(exist_ok=True)
                conv_stats = converter.convert_directory(
                    str(source_dir), str(out_dir), source=subdir,
                )
                print(f"  {subdir}: {conv_stats['success']} converted")
    else:
        print("\n[2/3] No metadata provided, skipping conversion...")

    # Step 3: Merge (extraction is base, metadata backfills)
    if args.metadata:
        print("\n[3/3] Merging (poster2json base + metadata backfill)...")
        merger = MetadataMerger()

        for subdir in ["zenodo", "figshare"]:
            meta_dir = converted_dir / subdir
            if meta_dir.exists():
                out_dir = merged_dir / subdir
                out_dir.mkdir(exist_ok=True)
                merge_stats = merger.merge_directories(
                    str(extraction_dir), str(meta_dir), str(out_dir),
                )
                print(f"  {subdir}: {merge_stats['merged']} merged")
    else:
        print("\n[3/3] No metadata to merge, copying extractions...")
        import shutil
        for f in extraction_dir.glob("*.json"):
            shutil.copy(f, merged_dir / f.name)

    print("\n" + "=" * 60)
    print("PIPELINE COMPLETE")
    print("=" * 60)
    print(f"  01_extractions/ - poster2json output (primary)")
    print(f"  02_converted/   - Repository metadata (schema-converted)")
    print(f"  03_merged/      - Final merged records")


def cmd_full_pipeline(args):
    """Run the complete end-to-end pipeline: scrape -> download -> QC -> extract -> merge."""
    output_base = Path(args.output)
    output_base.mkdir(parents=True, exist_ok=True)

    scrape_dir = output_base / "00_scraped"
    download_dir = output_base / "01_downloads"
    qc_dir = output_base / "02_qc"
    posters_dir = qc_dir / "posters"
    converted_dir = output_base / "03_converted"
    extraction_dir = output_base / "04_extractions"
    merged_dir = output_base / "05_merged"

    for d in [scrape_dir, download_dir, qc_dir, posters_dir,
              converted_dir, extraction_dir, merged_dir]:
        d.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("POSTER-TO-JSON FULL PIPELINE")
    print("=" * 60)
    print(f"Output: {args.output}")
    print()

    # ---- Step 1: Scrape ----
    if args.skip_scrape:
        print("[1/5] Skipping scrape (--skip-scrape)")
        if args.scraped_metadata:
            scrape_dir = Path(args.scraped_metadata)
            print(f"  Using existing metadata from: {scrape_dir}")
    else:
        print("[1/5] Scraping poster metadata from repositories...")
        repos = args.repositories.split(",") if args.repositories else ["zenodo", "figshare"]

        if "zenodo" in repos:
            try:
                from poster_scraper.zenodo import ZenodoScraper
                print("  Scraping Zenodo...")
                scraper = ZenodoScraper()
                scraper.fetch_all_posters(
                    output_file=str(scrape_dir / "zenodo.json"),
                    max_records=args.max_scrape,
                )
            except Exception as e:
                logging.error(f"Zenodo scrape failed: {e}")

        if "figshare" in repos:
            try:
                from poster_scraper.figshare import FigshareScraper
                print("  Scraping Figshare...")
                scraper = FigshareScraper()
                scraper.fetch_all_posters(
                    output_file=str(scrape_dir / "figshare.json"),
                    max_records=args.max_scrape,
                )
            except Exception as e:
                logging.error(f"Figshare scrape failed: {e}")

    # ---- Step 2: Download ----
    if args.skip_download:
        print("\n[2/5] Skipping download (--skip-download)")
        if args.posters_dir:
            download_dir = Path(args.posters_dir)
            print(f"  Using existing posters from: {download_dir}")
    else:
        print("\n[2/5] Downloading poster files...")
        try:
            from poster_scraper.downloader import PosterDownloader
            downloader = PosterDownloader(
                output_dir=str(download_dir),
                max_downloads=args.max_download,
            )

            zenodo_meta = scrape_dir / "zenodo.json"
            if zenodo_meta.exists():
                downloader.download_from_zenodo(str(zenodo_meta))

            figshare_meta = scrape_dir / "figshare.json"
            if figshare_meta.exists():
                downloader.download_from_figshare(str(figshare_meta))

            downloader.print_summary()
        except Exception as e:
            logging.error(f"Download failed: {e}")

    # ---- Step 3: QC / Classify ----
    if args.skip_qc:
        print("\n[3/5] Skipping QC (--skip-qc)")
        posters_dir = download_dir
        if args.posters_dir:
            posters_dir = Path(args.posters_dir)
    else:
        print("\n[3/5] Running quality control and classification...")
        try:
            from poster_qc.validator import PosterValidator
            from poster_qc.classifier import PosterClassifier

            print("  Validating files...")
            validator = PosterValidator()
            results, stats = validator.validate_directory(
                directory=str(download_dir),
                recursive=True,
                output_file=str(qc_dir / "validation.json"),
            )
            print(f"    Valid: {stats['valid']}, Invalid: {stats['invalid']}")

            valid_files = [r["path"] for r in results if r.get("valid")]
            if valid_files:
                print(f"  Classifying {len(valid_files)} valid files...")
                classifier = PosterClassifier()
                classify_results = classifier.classify_batch(valid_files)

                import shutil
                poster_count = 0
                for cr in classify_results:
                    if cr.get("is_poster"):
                        src = Path(cr["path"])
                        if src.exists():
                            shutil.copy2(src, posters_dir / src.name)
                            poster_count += 1

                with open(qc_dir / "classification.json", "w") as f:
                    json.dump(classify_results, f, indent=2)

                print(f"    Classified as posters: {poster_count}")
            else:
                print("    No valid files to classify")
        except Exception as e:
            logging.error(f"QC failed: {e}")
            print(f"  QC failed ({e}), using downloads directly...")
            posters_dir = download_dir

    # ---- Step 4: Extract with poster2json (PRIMARY source) ----
    print(f"\n[4/5] Extracting poster content via poster2json from: {posters_dir}")
    extractor = PosterExtractor()
    ext_stats = extractor.extract_directory(
        str(posters_dir), str(extraction_dir), max_files=args.max,
    )
    print(f"  Extracted: {ext_stats['success']}/{ext_stats['total']}")

    # ---- Step 5: Convert metadata + Merge ----
    print("\n[5/5] Converting metadata and merging (extraction base + metadata backfill)...")
    converter = SchemaConverter()
    merger = MetadataMerger()

    for subdir in ["zenodo", "figshare"]:
        meta_file = scrape_dir / f"{subdir}.json"
        if meta_file.exists():
            out_dir = converted_dir / subdir
            out_dir.mkdir(exist_ok=True)

            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    records = json.load(f)

                if isinstance(records, list):
                    for rec in tqdm(records, desc=f"Converting {subdir}"):
                        rec_id = rec.get("id") or rec.get("doi", "unknown")
                        out_file = out_dir / f"{rec_id}.json"
                        converted = converter.convert(rec, source=subdir)
                        with open(out_file, "w", encoding="utf-8") as fout:
                            json.dump(converted, fout, indent=2, ensure_ascii=False)
                elif isinstance(records, dict):
                    out_file = out_dir / meta_file.name
                    converted = converter.convert(records, source=subdir)
                    with open(out_file, "w", encoding="utf-8") as fout:
                        json.dump(converted, fout, indent=2, ensure_ascii=False)
            except Exception as e:
                logging.error(f"Error converting {subdir} metadata: {e}")

            # Merge: extraction is base, metadata backfills
            merge_out = merged_dir / subdir
            merge_out.mkdir(exist_ok=True)
            try:
                stats = merger.merge_directories(
                    str(extraction_dir), str(out_dir), str(merge_out),
                )
                print(f"  {subdir}: {stats['merged']} merged")
            except Exception as e:
                logging.error(f"Error merging {subdir}: {e}")

    # Copy extraction-only files (no metadata match)
    ext_files = set(f.stem.replace("_extracted", "") for f in extraction_dir.glob("*.json")
                    if f.name not in ("extraction_summary.json", "batch_results.json"))
    merged_files = set()
    for subdir in merged_dir.iterdir():
        if subdir.is_dir():
            merged_files.update(f.stem.replace("_complete", "") for f in subdir.glob("*.json"))

    unmerged = ext_files - merged_files
    if unmerged:
        import shutil
        no_meta_dir = merged_dir / "extraction_only"
        no_meta_dir.mkdir(exist_ok=True)
        for stem in unmerged:
            src = extraction_dir / f"{stem}_extracted.json"
            if src.exists():
                shutil.copy(src, no_meta_dir / f"{stem}.json")
        print(f"  extraction-only (no metadata): {len(unmerged)}")

    # Summary
    print("\n" + "=" * 60)
    print("FULL PIPELINE COMPLETE")
    print("=" * 60)
    print(f"Output directory: {output_base}")
    print(f"  00_scraped/       - Repository metadata")
    print(f"  01_downloads/     - Downloaded poster files")
    print(f"  02_qc/            - QC results + classified posters")
    print(f"  03_converted/     - Metadata converted to schema")
    print(f"  04_extractions/   - poster2json output (primary)")
    print(f"  05_merged/        - Final merged records")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="poster-to-json",
        description="Extract scientific poster content to machine-actionable JSON via poster2json",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Extract command
    extract_parser = subparsers.add_parser("extract", help="Extract content from poster files via poster2json")
    extract_parser.add_argument("-i", "--input", required=True, help="Input poster file or directory")
    extract_parser.add_argument("-o", "--output", default="./extractions", help="Output directory")
    extract_parser.add_argument("--max", type=int, help="Maximum files to process")
    extract_parser.set_defaults(func=cmd_extract)

    # Convert command
    convert_parser = subparsers.add_parser("convert", help="Convert repository metadata to schema")
    convert_parser.add_argument("-i", "--input", required=True, help="Input metadata file or directory")
    convert_parser.add_argument("-o", "--output", required=True, help="Output file or directory")
    convert_parser.add_argument("-s", "--source", choices=["zenodo", "figshare", "auto"], default="auto")
    convert_parser.set_defaults(func=cmd_convert)

    # Merge command
    merge_parser = subparsers.add_parser("merge", help="Merge poster2json extractions with metadata")
    merge_parser.add_argument("-e", "--extractions", required=True, help="Directory with poster2json extractions")
    merge_parser.add_argument("-m", "--metadata", required=True, help="Directory with converted metadata")
    merge_parser.add_argument("-o", "--output", required=True, help="Output directory")
    merge_parser.set_defaults(func=cmd_merge)

    # Pipeline command
    pipeline_parser = subparsers.add_parser("pipeline", help="Run pipeline (extract -> convert -> merge)")
    pipeline_parser.add_argument("-p", "--posters", required=True, help="Directory with poster files")
    pipeline_parser.add_argument("-m", "--metadata", help="Directory with repository metadata (optional)")
    pipeline_parser.add_argument("-o", "--output", default="./pipeline_output", help="Output directory")
    pipeline_parser.add_argument("--max", type=int, help="Maximum posters to process")
    pipeline_parser.set_defaults(func=cmd_pipeline)

    # Full pipeline command
    full_parser = subparsers.add_parser("full-pipeline", help="Complete pipeline (scrape -> QC -> extract -> merge)")
    full_parser.add_argument("-o", "--output", default="./full_pipeline_output", help="Output directory")
    full_parser.add_argument("--max", type=int, help="Maximum posters to extract")
    full_parser.add_argument("--max-scrape", type=int, help="Max records to scrape per repository")
    full_parser.add_argument("--max-download", type=int, help="Max files to download per repository")
    full_parser.add_argument("--repositories", default="zenodo,figshare", help="Repositories to scrape")
    full_parser.add_argument("--skip-scrape", action="store_true", help="Skip scraping step")
    full_parser.add_argument("--skip-download", action="store_true", help="Skip download step")
    full_parser.add_argument("--skip-qc", action="store_true", help="Skip QC/classification step")
    full_parser.add_argument("--scraped-metadata", help="Path to existing scraped metadata directory")
    full_parser.add_argument("--posters-dir", help="Path to existing posters directory")
    full_parser.set_defaults(func=cmd_full_pipeline)

    args = parser.parse_args()
    setup_logging(args.verbose)

    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
