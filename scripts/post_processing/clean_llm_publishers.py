#!/usr/bin/env python3
"""Apply the corpus's publisher rigor to the 2025 LLM-backfilled publishers.

The 2025 backfill filled publishers with a local LLM, whose output carries junk the basic
_clean_publisher gate misses: surrounding quotes, "The publisher is ..." preambles, hedge
non-answers ("not specified in the text", "the poster does not mention"), and generic
non-institutions ("a university", "research institute"). This normalizes each publisher the
same way the rest of the corpus was cleaned and drops anything that fails to the repository
fallback (Zenodo/Figshare by source).

Usage (pubverse env):
    ~/myenv/bin/python clean_llm_publishers.py --merged-dir /storage/poster-work/data2025/merged [--dry-run]
"""
import argparse
import json
import re
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if _SRC.exists():
    sys.path.insert(0, str(_SRC))
from poster_to_json.field_normalize import _clean_publisher, _is_placeholder  # noqa: E402

_PREAMBLE = re.compile(r"^(the\s+)?(publisher|publishing organization|organization|organisation|"
                       r"institution|host|hosting institution|host organization|affiliation)\s*"
                       r"(is|was|:|=|-)\s*", re.I)
_HEDGE = re.compile(r"(not specified|not provided|not mentioned|not clearly|not clear|not available|"
                    r"not found|not indicated|not stated|not listed|cannot be|could not be|can't be|"
                    r"unable to|no publisher|no explicit|no clear|based on the|appears to be|"
                    r"the poster\b|this poster\b|the authors\b|the text\b|the document\b|"
                    r"the image\b|the abstract\b|i cannot|i could not|unclear|unknown|would need|is not )", re.I)
_GENERIC = {
    "a university", "the university", "university", "a research institution", "research institution",
    "research institute", "institute", "research center", "research centre", "the institute",
    "conference", "poster", "academic institution", "the organization", "the organisation",
    "organization", "organisation", "company", "a company", "the company", "a research center",
    "various", "multiple", "n a", "the publisher", "publisher", "author", "the author", "the authors",
}
_CITATION = re.compile(r"\bet\s+al\b", re.I)        # "Haselman et al" is a citation, not a publisher


def _unwrap(s):
    """Strip surrounding quotes/asterisks/backticks ONLY when they wrap the whole string
    (balanced). Leaves an internal-quote name like 'University of Campania \"Luigi Vanvitelli\"'
    intact (it starts with a letter, ends with a quote -- not balanced)."""
    s = s.strip()
    prev = None
    while prev != s:
        prev = s
        for q in ('"', "'", "`", "*"):
            if len(s) >= 2 and s[0] == q and s[-1] == q:
                s = s[1:-1].strip()
    return s


def clean_publisher(name):
    """Return a cleaned publisher string, or None if it is junk / not a real publisher."""
    if not isinstance(name, str):
        return None
    s = _unwrap(name)                              # unwrap balanced wrappers, keep "Inc." intact
    s = _unwrap(_PREAMBLE.sub("", s))
    c = _clean_publisher(s)                        # NFKC, url/placeholder/len<=2/no-letter gate
    if not c:
        return None
    # generic check on the lowercased string WITHOUT stripping non-ASCII: a non-Latin name
    # ending in a Latin word ("Εθνική ... University") must not collapse to bare "university".
    low = re.sub(r"\s+", " ", c.lower()).strip(" .")
    if low in _GENERIC or _is_placeholder(c):
        return None
    if _HEDGE.search(c) or _CITATION.search(c):    # LLM non-answer prose / author citation
        return None
    if not _conservative and len(c.split()) > 15:  # a paragraph, not a name (abbreviations
        return None                                # like "U.S." keep real agency names safe).
    return c                                        # skipped in --conservative (extraction has
                                                    # legitimately long institution names)


_conservative = False


def _fallback(src):
    return "Zenodo" if src == "zenodo" else ("Figshare" if src == "figshare" else "Zenodo")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merged-dir", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--conservative", action="store_true",
                    help="skip the >15-word drop; for extraction publishers with long real names")
    ap.add_argument("--show", type=int, default=25)
    args = ap.parse_args()
    global _conservative
    _conservative = args.conservative

    st = {"scanned": 0, "cleaned": 0, "dropped_to_fallback": 0}
    samples = []
    for src_dir in sorted(Path(args.merged_dir).iterdir()):
        if not src_dir.is_dir():
            continue
        src = src_dir.name
        for f in sorted(src_dir.glob("*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            st["scanned"] += 1
            p = d.get("publisher")
            name = p.get("name") if isinstance(p, dict) else (p if isinstance(p, str) else None)
            if not isinstance(name, str) or name in ("Zenodo", "Figshare"):
                continue
            c = clean_publisher(name)
            new = c if c else _fallback(src)
            if new != name:
                if len(samples) < args.show:
                    samples.append(f"{name[:55]!r} -> {new!r}")
                if c:
                    st["cleaned"] += 1
                else:
                    st["dropped_to_fallback"] += 1
                if not args.dry_run:
                    d["publisher"] = {"name": new}
                    f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    for s in samples:
        print("  ", s)
    print(f"scanned={st['scanned']} cleaned={st['cleaned']} dropped_to_fallback={st['dropped_to_fallback']}"
          + (" (dry-run)" if args.dry_run else ""))


if __name__ == "__main__":
    main()
