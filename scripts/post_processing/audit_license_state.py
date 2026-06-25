#!/usr/bin/env python3
"""
Read-only: report the license/content state of a merged corpus so we can plan
the content step after a license backfill.

For each merged record, classify its (now-corrected) rightsList and check
whether its extracted content was previously stripped (_license_blocked).

Key buckets:
  need_strip    -- license is blocked/empty, content still present (must strip)
  need_restore  -- license is allowed, but content was stripped (_license_blocked)
                   => regathered an allowed license; content should be re-merged
  ok_allowed    -- allowed + content present
  ok_blocked    -- blocked/empty + already stripped

Usage:
    python audit_license_state.py --merged-dir /home/james/corpus_output/merged
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from license_policy import classify_license  # noqa: E402


def _has_content(d):
    c = d.get("content")
    return isinstance(c, dict) and bool(c.get("sections"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--merged-dir", default="/home/james/corpus_output/merged")
    ap.add_argument("--sample-restore", type=int, default=5,
                    help="print this many record ids that need restore")
    args = ap.parse_args()

    merged_dir = Path(args.merged_dir)
    files = sorted(merged_dir.rglob("*.json"))

    b = Counter()
    verdicts = Counter()
    restore_ids = []

    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            b["errors"] += 1
            continue
        if not isinstance(d, dict):
            b["errors"] += 1
            continue

        verdict = classify_license(d.get("rightsList"))
        verdicts[verdict] += 1
        blocked_flag = bool(d.get("_license_blocked"))
        content = _has_content(d)

        allowed = (verdict == "allowed")
        if allowed and blocked_flag:
            b["need_restore"] += 1
            if len(restore_ids) < args.sample_restore:
                restore_ids.append(f.stem.replace("_complete", ""))
        elif allowed and content:
            b["ok_allowed"] += 1
        elif allowed and not content:
            b["allowed_no_content_no_flag"] += 1
        elif not allowed and content and not blocked_flag:
            b["need_strip"] += 1
        else:  # not allowed, already stripped or no content
            b["ok_blocked"] += 1

    print(f"total: {len(files)}")
    print("verdicts:", dict(verdicts))
    print("buckets:")
    for k in ("ok_allowed", "ok_blocked", "need_strip", "need_restore",
              "allowed_no_content_no_flag", "errors"):
        if b.get(k):
            print(f"  {k:28s} {b[k]}")
    if restore_ids:
        print("sample need_restore ids:", ", ".join(restore_ids))


if __name__ == "__main__":
    main()
