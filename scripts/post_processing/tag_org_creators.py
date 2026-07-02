#!/usr/bin/env python3
"""
Tag organisational / collective authors with nameType "Organizational".

A creator is tagged Organizational when it carries a clear org keyword
(_ORG_RE), or when it looks like a collaboration/committee ("the X Team",
"consortium", ...) AND probablepeople independently classifies it as a
Corporation. The probablepeople dependency is OPTIONAL: without it, only the
keyword rule runs (probablepeople mislabels ~20% of person names as companies,
so it is used only to *confirm* an admin keyword, never on its own).

Usage:
    python tag_org_creators.py --merged-dir <m> [--dry-run]
"""
import argparse
import json
import logging
import re
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

_SRC = Path(__file__).resolve().parents[2] / "src"
if _SRC.exists():
    sys.path.insert(0, str(_SRC))
from poster_to_json.field_normalize import _ORG_RE  # noqa: E402

_ADMIN_RE = re.compile(
    r"\b(committee|comit|equipe|équipe|team|working group|task ?force|consortium|"
    r"network|secretariat|panel|board|collaboration|initiative|programme|project|"
    r"platform|partnership|alliance|federation|group members|members)\b", re.I)

try:
    import probablepeople as _pp

    def _is_corp(n):
        try:
            return _pp.tag(str(n))[1] == "Corporation"
        except Exception:
            return False
    _HAS_PP = True
except ImportError:
    def _is_corp(n):
        return False
    _HAS_PP = False


def _is_org(name):
    if _ORG_RE.search(name):
        return True
    if _ADMIN_RE.search(name) and _is_corp(name):
        return True
    return False


def run(merged_dir, dry_run, limit):
    stats = {"scanned": 0, "tagged": 0, "errors": 0}
    for src in ("zenodo", "figshare"):
        md = Path(merged_dir) / src
        if not md.exists():
            continue
        for f in sorted(md.glob("*_complete.json")):
            if limit and stats["scanned"] >= limit:
                break
            stats["scanned"] += 1
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                stats["errors"] += 1
                continue
            if not isinstance(d, dict):
                continue
            changed = False
            for c in (d.get("creators") or []):
                if not isinstance(c, dict) or not c.get("name"):
                    continue
                if c.get("nameType") == "Organizational":
                    continue
                if _is_org(c["name"]):
                    c["nameType"] = "Organizational"
                    changed = True
                    stats["tagged"] += 1
            if changed and not dry_run:
                try:
                    f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
                except Exception as e:
                    logger.error(f"  write {f.name}: {e}")
                    stats["errors"] += 1
    return stats


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merged-dir", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    mode = "DRY-RUN" if args.dry_run else "LIVE"
    logger.info(f"[{mode}] tag org creators  probablepeople={'yes' if _HAS_PP else 'no (keyword-only)'}")
    stats = run(args.merged_dir, args.dry_run, args.limit)
    logger.info(f"Done: scanned={stats['scanned']} tagged={stats['tagged']} errors={stats['errors']}")


if __name__ == "__main__":
    main()
