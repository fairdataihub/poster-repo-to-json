#!/usr/bin/env python3
"""Backfill 2025 publishers. The 2025 poster2json run left `publisher` null, so those
posters fell back to the bare Zenodo/Figshare repository. This fills them: for Figshare,
the deposit's custom "Publisher" field if present; otherwise the publisher extracted from
the poster content by a local LLM (Ollama chat). Only fills records still on the bare
repository fallback. Resumable via a progress file. Idempotent per record.

Usage (with the pubverse env):
    ~/myenv/bin/python backfill_publisher_2025_llm.py \
        --merged-dir /storage/poster-work/data2025/merged \
        --metadata-dir /storage/poster-work/data2025/metadata \
        --progress /storage/poster-work/pub2025.progress [--limit N]
"""
import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if _SRC.exists():
    sys.path.insert(0, str(_SRC))
from poster_to_json.field_normalize import _clean_publisher  # noqa: E402

LLM_URL = os.environ.get("PUBVERSE_LLM_URL", "http://100.115.159.103:11434")
MODEL = os.environ.get("PUB_LLM_MODEL", "llama3.2:3b")
_SYS = ("You identify the publisher or hosting institution/organization of a scientific "
        "poster (a university, research agency, company, or repository). Reply with ONLY "
        "that organization name on a single line, or the single word: unknown. No preamble.")
_BAD = {"unknown", "none", "n/a", "na", "not specified", "zenodo", "figshare", ""}


def llm_publisher(txt):
    body = json.dumps({"model": MODEL, "messages": [
        {"role": "system", "content": _SYS}, {"role": "user", "content": txt[:1800]}],
        "stream": False, "think": False,
        "options": {"temperature": 0, "num_predict": 30}}).encode()
    req = urllib.request.Request(LLM_URL + "/api/chat", body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        ans = json.load(r)["message"]["content"].strip()
    return ans.splitlines()[0].strip() if ans else ""


def content_text(d):
    parts = [(t.get("title") if isinstance(t, dict) else t) for t in (d.get("titles") or [])]
    for s in (d.get("content") or {}).get("sections") or []:
        if isinstance(s, dict):
            parts += [s.get("sectionTitle") or "", (s.get("sectionContent") or "")[:300]]
    return "\n".join(p for p in parts if isinstance(p, str))


def figshare_custom_publisher(rid, metadir):
    p = Path(metadir) / "figshare" / f"{rid}.json"
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    for c in raw.get("custom_fields") or []:
        if isinstance(c, dict) and c.get("name") == "Publisher":
            return c.get("value")
    return None


def run(merged_dir, metadir, progress, limit):
    done = set(open(progress).read().split()) if os.path.exists(progress) else set()
    pf = open(progress, "a")
    st = {"processed": 0, "figshare_custom": 0, "llm_filled": 0, "unknown": 0, "errors": 0}
    for src in ("zenodo", "figshare"):
        d0 = Path(merged_dir) / src
        if not d0.exists():
            continue
        for f in sorted(d0.glob("*_complete.json")):
            if limit and st["processed"] >= limit:
                break
            rid = f.stem.replace("_complete", "")
            key = f"{src}/{rid}"
            if key in done:
                continue
            st["processed"] += 1
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                st["errors"] += 1
                pf.write(key + "\n"); pf.flush()
                continue
            cur = d.get("publisher")
            cur = cur.get("name") if isinstance(cur, dict) else cur
            if cur not in ("Zenodo", "Figshare", None):     # already has a real publisher
                pf.write(key + "\n"); pf.flush()
                continue
            pub = None
            if src == "figshare":
                pub = _clean_publisher(figshare_custom_publisher(rid, metadir))
                if pub:
                    st["figshare_custom"] += 1
            if not pub:
                try:
                    ans = llm_publisher(content_text(d))
                    if ans and ans.strip().lower() not in _BAD:
                        pub = _clean_publisher(ans)
                        if pub:
                            st["llm_filled"] += 1
                except Exception:
                    st["errors"] += 1
            if pub and pub not in ("Zenodo", "Figshare"):
                d["publisher"] = {"name": pub}
                f.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
            else:
                st["unknown"] += 1
            pf.write(key + "\n"); pf.flush()
            if st["processed"] % 250 == 0:
                print(f"  ...{st['processed']} processed, {st['figshare_custom']+st['llm_filled']} filled", flush=True)
    return st


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--merged-dir", required=True)
    ap.add_argument("--metadata-dir", required=True)
    ap.add_argument("--progress", required=True)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    st = run(args.merged_dir, args.metadata_dir, args.progress, args.limit)
    for k in ("processed", "figshare_custom", "llm_filled", "unknown", "errors"):
        print(f"  {k:16s} {st[k]}")


if __name__ == "__main__":
    main()
