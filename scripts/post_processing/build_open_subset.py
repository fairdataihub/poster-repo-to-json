#!/usr/bin/env python3
"""
Build the open (allowed-license) delivery subset from a merged corpus.

The public delivery (posters-2025/merged) ships only openly-licensed posters.
This copies merged records whose license classifies as 'allowed' into <out-dir>,
preserving zenodo/figshare subdirs; restricted / blocked / no-license records
are excluded. Mirrors the original data_2025/merged_open selection, but built
from the corrected merged so license + date fixes are reflected and the open
set is up to date (records whose license we normalized into a recognized open
license are now included; newly-blocked ones drop out).

Usage:
    python build_open_subset.py --merged-dir <merged> --out-dir <merged_open> --clean
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from license_policy import classify_license  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--merged-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--clean", action="store_true",
                    help="empty out-dir first (so removed records don't linger)")
    args = ap.parse_args()

    merged = Path(args.merged_dir)
    out = Path(args.out_dir)
    if args.clean and out.exists():
        shutil.rmtree(out)

    stats = {"scanned": 0, "open": 0, "excluded": 0, "errors": 0}
    for src in ("zenodo", "figshare"):
        srcd = merged / src
        if not srcd.exists():
            continue
        (out / src).mkdir(parents=True, exist_ok=True)
        for f in srcd.glob("*_complete.json"):
            stats["scanned"] += 1
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                stats["errors"] += 1
                continue
            if isinstance(d, dict) and classify_license(d.get("rightsList")) == "allowed":
                shutil.copy2(f, out / src / f.name)
                stats["open"] += 1
            else:
                stats["excluded"] += 1

    print(f"open-subset build: {stats}")
    print(f"  -> {out}")


if __name__ == "__main__":
    main()
