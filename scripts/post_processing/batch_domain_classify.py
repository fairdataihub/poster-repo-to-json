#!/usr/bin/env python3
"""
Batch domain classification for extracted poster JSONs.

Classifies posters into OpenAlex domains using jimnoneill/paper-to-field.
Supports sharding across multiple GPUs.

Usage:
    # Single GPU, all posters:
    CUDA_VISIBLE_DEVICES=0 python batch_domain_classify.py --extractions ./extractions

    # Sharded across 3 GPUs:
    CUDA_VISIBLE_DEVICES=0 python batch_domain_classify.py --extractions ./extractions --shard 0 --num-shards 3
    CUDA_VISIBLE_DEVICES=2 python batch_domain_classify.py --extractions ./extractions --shard 1 --num-shards 3
    CUDA_VISIBLE_DEVICES=3 python batch_domain_classify.py --extractions ./extractions --shard 2 --num-shards 3
"""

import argparse
import json
from pathlib import Path

import torch
from tqdm import tqdm
from transformers import AutoModelForSequenceClassification, AutoTokenizer

ASJC_TO_OPENALEX = {
    "Chemical Engineering": "Physical Sciences",
    "Chemistry": "Physical Sciences",
    "Computer Science": "Physical Sciences",
    "Earth and Planetary Sciences": "Physical Sciences",
    "Energy": "Physical Sciences",
    "Engineering": "Physical Sciences",
    "Environmental Science": "Physical Sciences",
    "Materials Science": "Physical Sciences",
    "Mathematics": "Physical Sciences",
    "Physics and Astronomy": "Physical Sciences",
    "Physical Sciences": "Physical Sciences",
    "Agricultural and Biological Sciences": "Life Sciences",
    "Biochemistry, Genetics and Molecular Biology": "Life Sciences",
    "Immunology and Microbiology": "Life Sciences",
    "Neuroscience": "Life Sciences",
    "Pharmacology, Toxicology and Pharmaceutics": "Life Sciences",
    "Life Sciences": "Life Sciences",
    "Arts and Humanities": "Social Sciences",
    "Business, Management and Accounting": "Social Sciences",
    "Decision Sciences": "Social Sciences",
    "Economics, Econometrics and Finance": "Social Sciences",
    "Psychology": "Social Sciences",
    "Social Sciences": "Social Sciences",
    "Dentistry": "Health Sciences",
    "Health Professions": "Health Sciences",
    "Medicine": "Health Sciences",
    "Nursing": "Health Sciences",
    "Veterinary": "Health Sciences",
    "Health Sciences": "Health Sciences",
    "Multidisciplinary": "Physical Sciences",
}


def classify_domain(data: dict, classifier, tokenizer) -> str | None:
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

    inputs = tokenizer(text, truncation=True, max_length=384, return_tensors="pt")
    inputs = {k: v.to(classifier.device) for k, v in inputs.items()}

    with torch.no_grad():
        outputs = classifier(**inputs)

    pred_idx = outputs.logits[0].argmax().item()
    label = classifier.config.id2label[pred_idx]
    return ASJC_TO_OPENALEX.get(label, label)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--extractions", required=True)
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--num-shards", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--force", action="store_true",
                        help="Reclassify all files, even those that already have researchField")
    args = parser.parse_args()

    ext_dir = Path(args.extractions)
    all_files = sorted(ext_dir.glob("*_extracted.json"))

    shard_files = [f for i, f in enumerate(all_files) if i % args.num_shards == args.shard]
    print(f"Shard {args.shard}/{args.num_shards}: {len(shard_files)} files (of {len(all_files)} total)")

    model_name = "jimnoneill/paper-to-field"
    print(f"Loading {model_name}...")
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    classifier = AutoModelForSequenceClassification.from_pretrained(model_name)
    if torch.cuda.is_available():
        classifier = classifier.cuda().half()
    classifier.eval()
    print(f"Model loaded on {classifier.device}")

    classified = 0
    skipped = 0
    already_has = 0
    errors = 0

    for f in tqdm(shard_files, desc=f"Shard {args.shard}", unit="poster"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if "error" in data:
                skipped += 1
                continue

            if data.get("researchField") and not args.force:
                already_has += 1
                continue

            domain = classify_domain(data, classifier, tokenizer)
            if domain:
                data["researchField"] = domain
                f.write_text(
                    json.dumps(data, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                classified += 1
            else:
                skipped += 1

        except Exception as e:
            errors += 1
            tqdm.write(f"Error on {f.name}: {e}")

    print(f"\nShard {args.shard} done: {classified} classified, {already_has} already had domain, {skipped} skipped, {errors} errors")


if __name__ == "__main__":
    main()
