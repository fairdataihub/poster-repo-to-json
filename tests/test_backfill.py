"""End-to-end check of backfill_curated_fields against a synthetic corpus.

Builds <root>/converted/zenodo and <root>/merged/zenodo with one record that
reflects the OLD merge (extraction creators/desc/funding won) plus downstream
enrichment (resolved ROR/ORCID/funderIdentifier). Runs the backfill and asserts
the deltas were applied while enrichment was preserved.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "post_processing" / "backfill_curated_fields.py"


def _write(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        rec = "12345"

        # Converted deposit metadata: curated names/order, deposit abstract,
        # NSF funder (no funderIdentifier yet), keyword subjects.
        _write(root / "converted" / "zenodo" / f"{rec}.json", {
            "creators": [
                {"name": "Doe, John", "nameType": "Personal",
                 "affiliation": [{"name": "Acme University"}]},
                {"name": "Smith, Jane", "nameType": "Personal",
                 "affiliation": [{"name": "Acme University"}]},
            ],
            "descriptions": [{"description": "Curated deposit abstract.",
                              "descriptionType": "Abstract"}],
            "fundingReferences": [{"funderName": "National Science Foundation",
                                   "awardNumber": "NSF-1"}],
            "subjects": [{"subject": "Proteomics"}],
        })

        # Merged + enriched (OLD behavior): extraction order (Smith first), name
        # in "Given Family" form, ORCID + ROR resolved, LLM summary as Abstract,
        # guessed funder WITH a resolved funderIdentifier, extracted subject.
        _write(root / "merged" / "zenodo" / f"{rec}_complete.json", {
            "titles": [{"title": "A poster"}],
            "content": {"sections": [{"sectionTitle": "x", "sectionContent": "y"}]},
            "creators": [
                {"name": "Jane Smith", "nameType": "Personal",
                 "nameIdentifiers": [{"nameIdentifier": "0000-0002-1111-2222",
                                      "nameIdentifierScheme": "ORCID"}],
                 "affiliation": [{"name": "Acme University",
                                  "affiliationIdentifier": "https://ror.org/05acme",
                                  "affiliationIdentifierScheme": "ROR"}]},
                {"name": "John Doe", "nameType": "Personal"},
                {"name": "Hallucinated, Author"},
            ],
            "descriptions": [{"description": "LLM generated summary text.",
                              "descriptionType": "Abstract"}],
            "fundingReferences": [{"funderName": "National Science Foundation",
                                   "funderIdentifier": "https://doi.org/10.13039/100000001",
                                   "funderIdentifierType": "Crossref Funder ID"}],
            "subjects": [{"subject": "Genomics"}],
        })

        r = subprocess.run([sys.executable, str(SCRIPT), "--root", str(root)],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert "changed      1" in (r.stdout + r.stderr), r.stdout + r.stderr

        out = json.loads((root / "merged" / "zenodo" / f"{rec}_complete.json").read_text())

        # creators: deposit order (Doe, Smith), hallucinated author gone
        names = [c["name"] for c in out["creators"]]
        assert names == ["Doe, John", "Smith, Jane"], names
        # ROR/ORCID enrichment carried over onto curated Smith despite name-order diff
        smith = out["creators"][1]
        assert smith["nameIdentifiers"][0]["nameIdentifier"] == "0000-0002-1111-2222", smith
        assert smith["affiliation"][0]["affiliationIdentifier"] == "https://ror.org/05acme", smith

        # descriptions: deposit abstract primary, LLM summary demoted to Other
        assert out["descriptions"][0]["description"] == "Curated deposit abstract."
        assert out["descriptions"][0]["descriptionType"] == "Abstract"
        assert out["descriptions"][1]["descriptionType"] == "Other"

        # funding: deposit funder wins, resolved funderIdentifier preserved
        fr = out["fundingReferences"]
        assert len(fr) == 1 and fr[0]["awardNumber"] == "NSF-1", fr
        assert fr[0]["funderIdentifier"] == "https://doi.org/10.13039/100000001", fr

        # subjects: union of Genomics + Proteomics
        subs = sorted(s["subject"].lower() for s in out["subjects"])
        assert subs == ["genomics", "proteomics"], subs

        # idempotent: second run reports no change
        r2 = subprocess.run([sys.executable, str(SCRIPT), "--root", str(root)],
                            capture_output=True, text=True)
        assert "changed      0" in (r2.stdout + r2.stderr), r2.stdout + r2.stderr

        print("OK backfill: deltas applied, enrichment preserved, idempotent")


if __name__ == "__main__":
    main()
