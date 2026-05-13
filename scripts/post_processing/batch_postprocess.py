#!/usr/bin/env python3
"""
Batch post-processing pipeline for extracted poster JSONs.

Runs on all successful extractions:
  B1. poster2json _postprocess_json (schema migration, enrichment, cleanup)
  B2. ORCID enrichment via authenticated API
  B3. Description retag (auto-generated Abstract → Other)
  B4. Domain classification via paper-to-field

Usage:
    python batch_postprocess.py --extractions ./extractions --raw-text ./raw_text
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from tqdm import tqdm

sys.path.insert(0, "/home/james/poster2json")

AUTO_GEN_PREFIXES = (
    "this poster presents",
    "the poster describes",
    "this study presents",
    "this research",
    "the authors present",
    "this work presents",
    "we present",
    "in this poster",
    "this poster describes",
    "the poster presents",
    "this poster illustrates",
    "this poster reports",
    "this poster summarizes",
    "this poster explores",
    "this poster discusses",
    "this poster examines",
    "this poster introduces",
    "this poster outlines",
    "this poster highlights",
    "this poster provides",
)


def retag_auto_generated_descriptions(data: dict) -> int:
    """Retag auto-generated descriptions from Abstract to Other. Returns count changed."""
    changed = 0
    for desc in data.get("descriptions", []):
        if not isinstance(desc, dict):
            continue
        if desc.get("descriptionType") != "Abstract":
            continue
        text = desc.get("description", "").strip().lower()
        if any(text.startswith(p) for p in AUTO_GEN_PREFIXES):
            desc["descriptionType"] = "Other"
            changed += 1
    return changed


def classify_domain(data: dict, classifier, tokenizer_clf) -> str | None:
    """Classify poster domain using paper-to-field model."""
    title = ""
    titles = data.get("titles", [])
    if titles and isinstance(titles[0], dict):
        title = titles[0].get("title", "")

    description = ""
    for desc in data.get("descriptions", []):
        if isinstance(desc, dict) and desc.get("description"):
            description = desc["description"]
            if desc.get("descriptionType") == "Abstract":
                break

    if not title and not description:
        return None

    text = f"{title}. {description}" if description else title

    inputs = tokenizer_clf(
        text, truncation=True, max_length=384, return_tensors="pt"
    )
    inputs = {k: v.to(classifier.device) for k, v in inputs.items()}

    import torch
    with torch.no_grad():
        outputs = classifier(**inputs)

    logits = outputs.logits[0]
    pred_idx = logits.argmax().item()
    label = classifier.config.id2label[pred_idx]

    domain_map = {
        "Physical Sciences": "Physical Sciences",
        "Life Sciences": "Life Sciences",
        "Social Sciences": "Social Sciences",
        "Health Sciences": "Health Sciences",
    }
    return domain_map.get(label, label)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--extractions", required=True)
    parser.add_argument("--raw-text", required=True)
    parser.add_argument("--skip-orcid", action="store_true")
    parser.add_argument("--skip-domain", action="store_true")
    args = parser.parse_args()

    ext_dir = Path(args.extractions)
    raw_dir = Path(args.raw_text)

    ext_files = sorted(ext_dir.glob("*_extracted.json"))
    print(f"Found {len(ext_files)} extraction files")

    # Load poster2json postprocess
    from poster2json.extract import _postprocess_json

    # B1+B2: postprocess + ORCID
    orcid_client = None
    if not args.skip_orcid:
        from poster2json.orcid import OrcidClient
        client_id = os.environ.get("ORCID_CLIENT_ID")
        client_secret = os.environ.get("ORCID_CLIENT_SECRET")
        if client_id and client_secret:
            orcid_client = OrcidClient()
            print(f"ORCID enrichment enabled (client: {client_id[:10]}...)")
        else:
            print("ORCID credentials not set, skipping ORCID enrichment")

    # B4: domain classifier
    classifier = None
    tokenizer_clf = None
    if not args.skip_domain:
        try:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
            import torch
            print("Loading paper-to-field classifier...")
            model_name = "jimnoneill/paper-to-field"
            tokenizer_clf = AutoTokenizer.from_pretrained(model_name)
            classifier = AutoModelForSequenceClassification.from_pretrained(model_name)
            if torch.cuda.is_available():
                classifier = classifier.cuda()
            classifier.eval()
            print(f"Classifier loaded ({model_name})")
        except Exception as e:
            print(f"Could not load classifier: {e}")

    processed = 0
    skipped = 0
    errors = 0
    desc_retagged = 0
    domains_set = 0

    for ext_file in tqdm(ext_files, desc="Postprocessing", unit="poster"):
        try:
            data = json.loads(ext_file.read_text(encoding="utf-8"))
            if "error" in data:
                skipped += 1
                continue

            stem = ext_file.stem.replace("_extracted", "")
            raw_text = ""
            raw_cache = raw_dir / f"{stem}.json"
            if raw_cache.exists():
                try:
                    cached = json.loads(raw_cache.read_text())
                    raw_text = cached.get("text", "")
                except Exception:
                    pass

            # B1: Full postprocess (schema migration, enrichment, cleanup)
            data = _postprocess_json(data, raw_text=raw_text)

            # B3: Description retag
            desc_retagged += retag_auto_generated_descriptions(data)

            # B4: Domain classification
            if classifier is not None:
                domain = classify_domain(data, classifier, tokenizer_clf)
                if domain:
                    data["researchField"] = domain
                    domains_set += 1

            ext_file.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            processed += 1

        except Exception as e:
            errors += 1
            tqdm.write(f"Error on {ext_file.name}: {e}")

    print(f"\nDone: {processed} processed, {skipped} skipped (errors), {errors} new errors")
    print(f"Descriptions retagged: {desc_retagged}")
    print(f"Domains classified: {domains_set}")

    if orcid_client:
        orcid_client._save_cache()
        print(f"ORCID cache: {len(orcid_client._cache)} entries")


if __name__ == "__main__":
    main()
