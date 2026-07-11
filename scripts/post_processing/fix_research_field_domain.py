#!/usr/bin/env python3
"""Make `researchField` (and its mirror `domain`) schema-conformant.

poster_schema.json requires `researchField` to be one of the four OpenAlex
top-level domains -- 'Health Sciences', 'Life Sciences', 'Physical Sciences',
'Social Sciences' -- or omitted. Extraction left 1,085 records carrying OpenAlex
*field*-level values (Computer Science, Engineering, Arts and Humanities, ...) or
foreign-language equivalents, which violate the schema. This maps each up to its
OpenAlex parent domain and mirrors the result into `domain` (the key the
auto-index ingestion reads). Unmappable values are omitted (schema allows null).

Usage (pubverse env):
    ~/myenv/bin/python fix_research_field_domain.py --merged-dir <m> [--dry-run]
"""
import argparse
import json
from collections import Counter
from pathlib import Path

ALLOWED = {"Health Sciences", "Life Sciences", "Physical Sciences", "Social Sciences"}

# OpenAlex field / common-variant -> top-level domain.
FIELD_TO_DOMAIN = {
    # --- Physical Sciences (Chemistry, CS, Earth/Planetary, Energy, Engineering,
    #     Environmental Science, Materials, Mathematics, Physics) ---
    "Computer Science": "Physical Sciences", "Computing": "Physical Sciences",
    "Computer Science and Engineering": "Physical Sciences",
    "Human-Computer Interaction (HCI)": "Physical Sciences",
    "Artificial Intelligence": "Physical Sciences", "Information Science": "Physical Sciences",
    "Information Sciences": "Physical Sciences", "Information Systems": "Physical Sciences",
    "Information Technology": "Physical Sciences", "Research Software Engineering": "Physical Sciences",
    "Physics and Astronomy": "Physical Sciences", "Physics": "Physical Sciences",
    "Engineering": "Physical Sciences", "Industrial Engineering": "Physical Sciences",
    "Chemical Engineering": "Physical Sciences", "Water Resources Engineering": "Physical Sciences",
    "Manufacturing": "Physical Sciences", "Engineering and Technology": "Physical Sciences",
    "Engineering & Mathematics": "Physical Sciences",
    "Earth Sciences": "Physical Sciences", "Earth Science": "Physical Sciences",
    "Earth sciences": "Physical Sciences", "Earth and Planetary Sciences": "Physical Sciences",
    "Earth System Sciences": "Physical Sciences", "Earth System Science": "Physical Sciences",
    "Geology": "Physical Sciences", "Geological Sciences": "Physical Sciences",
    "Geosciences": "Physical Sciences", "Geowissenschaften": "Physical Sciences",
    "Environmental Science": "Physical Sciences", "Environmental Sciences": "Physical Sciences",
    "Energy": "Physical Sciences", "Chemistry": "Physical Sciences",
    "Mathematics": "Physical Sciences", "Materials Science": "Physical Sciences",
    # --- Life Sciences ---
    "Agricultural and Biological Sciences": "Life Sciences", "Agriculture": "Life Sciences",
    "Agricultural Sciences": "Life Sciences", "Neuroscience": "Life Sciences",
    "Immunology and Microbiology": "Life Sciences", "Bioinformatics": "Life Sciences",
    "Biochemistry, Genetics and Molecular Biology": "Life Sciences",
    # --- Health Sciences ---
    "Health Professions": "Health Sciences", "Medicine": "Health Sciences",
    "Veterinary": "Health Sciences", "Dentistry": "Health Sciences",
    "Pharmacology, Toxicology and Pharmaceutics": "Health Sciences",
    "Epidemiologia": "Health Sciences", "Forensic Science": "Health Sciences",
    # --- Social Sciences (Arts & Humanities, Business, Decision Sciences,
    #     Economics, Psychology, Social Sciences, Library & Information Sciences) ---
    "Arts and Humanities": "Social Sciences", "Humanities": "Social Sciences",
    "Digital Humanities": "Social Sciences", "Geistes- und Kulturwissenschaften": "Social Sciences",
    "Education": "Social Sciences", "Musikpädagogik": "Social Sciences",
    "Psychology": "Social Sciences", "Business, Management and Accounting": "Social Sciences",
    "Business": "Social Sciences", "Business/Management": "Social Sciences",
    "Decision Sciences": "Social Sciences", "Economics, Econometrics and Finance": "Social Sciences",
    "Language and Linguistics": "Social Sciences", "Sprachwissenschaft": "Social Sciences",
    "Literaturwissenschaft": "Social Sciences", "Law": "Social Sciences",
    "International Law": "Social Sciences", "Political Science": "Social Sciences",
    "History": "Social Sciences", "Art History": "Social Sciences",
    "Archeologia e Storia dell'arte": "Social Sciences", "Arte y Arqueología": "Social Sciences",
    "Arquitetura e Urbanismo": "Social Sciences", "Philosophie": "Social Sciences",
    "Religious Studies": "Social Sciences", "Library Services": "Social Sciences",
    "Library": "Social Sciences", "Library and Information Science": "Social Sciences",
    "Geography": "Social Sciences", "Geografia": "Social Sciences",
}


def resolve(value):
    """Return the conformant domain for a researchField value, or None to omit."""
    if value in ALLOWED:
        return value
    return FIELD_TO_DOMAIN.get(value)  # None if unmapped -> omit


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merged-dir", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    st = {"scanned": 0, "remapped": 0, "omitted": 0, "already_ok": 0, "mirror_fixed": 0}
    remap_counts, omit_counts = Counter(), Counter()
    for f in Path(args.merged_dir).rglob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        st["scanned"] += 1
        R = d.get("researchField")
        changed = False
        if R is None:
            if "domain" in d:                       # stray domain without researchField
                d.pop("domain", None); changed = True
        elif R in ALLOWED:
            st["already_ok"] += 1
            if d.get("domain") != R:                # keep mirror in sync
                d["domain"] = R; changed = True; st["mirror_fixed"] += 1
        else:
            new = resolve(R)
            if new is None:
                d.pop("researchField", None); d.pop("domain", None)
                omit_counts[R] += 1; st["omitted"] += 1; changed = True
            else:
                d["researchField"] = new; d["domain"] = new
                remap_counts[R] += 1; st["remapped"] += 1; changed = True
        if changed and not args.dry_run:
            f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")

    for k in ("scanned", "already_ok", "remapped", "omitted", "mirror_fixed"):
        print(f"  {k:12s} {st[k]}")
    print("  -- omitted (unmapped) values:", dict(omit_counts) or "none")
    if args.dry_run:
        print("  (dry-run)")


if __name__ == "__main__":
    main()
