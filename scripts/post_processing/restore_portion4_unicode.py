"""Restore non-ASCII values wrongly dropped by v0.33.0 buggy ASCII checks.
conferenceName <- deposit meeting.title OR extraction; lost captions <- extraction.
Only restores real multi-char names the FIXED normalizer keeps. Idempotent."""
import argparse, glob, json, re, sys
from pathlib import Path
sys.path.insert(0, "/storage/poster-work/repo/src")
from poster_to_json.field_normalize import drop_junk_captions
def has_letter(s): return any(ch.isalpha() for ch in s)
def ascii_alnum(s): return sum(1 for ch in s if ch.isalnum() and ch.isascii())
def uni_alnum(s): return sum(1 for ch in s if ch.isalnum())
def recoverable(name):
    return isinstance(name,str) and has_letter(name) and ascii_alnum(name)<=2 and uni_alnum(name)>2
def build_idx(corpus):
    idx={}
    for f in glob.glob("/storage/poster-work/"+corpus+"/extractions/*.json"):
        m=re.match(r"(zenodo|figshare)_(\d+)_", Path(f).name)
        if m: idx.setdefault((m.group(1),m.group(2)), f)
    return idx
def run(dry):
    st={"scanned":0,"conf":0,"cap":0}
    for corpus in ["pre2025","data2025"]:
        idx=build_idx(corpus)
        for src in ["zenodo","figshare"]:
            for mf in glob.glob("/storage/poster-work/"+corpus+"/merged/"+src+"/*_complete.json"):
                st["scanned"]+=1
                try: d=json.load(open(mf,encoding="utf-8"))
                except: continue
                rid=Path(mf).stem.replace("_complete","")
                ep=idx.get((src,rid)); e={}
                if ep:
                    try: e=json.load(open(ep,encoding="utf-8"))
                    except: e={}
                changed=False
                conf=d.get("conference")
                if isinstance(conf,dict) and not conf.get("conferenceName"):
                    name=None
                    mp=Path("/storage/poster-work/"+corpus+"/metadata/"+src+"/"+rid+".json")
                    if mp.exists():
                        try: meta=json.load(open(mp,encoding="utf-8"))
                        except: meta={}
                        md=meta.get("metadata") if src=="zenodo" else meta
                        mt=(md or {}).get("meeting",{}).get("title") if isinstance((md or {}).get("meeting"),dict) else None
                        if recoverable(mt): name=mt
                    if not name:
                        ec=e.get("conference"); ecn=ec.get("conferenceName") if isinstance(ec,dict) else None
                        if recoverable(ecn): name=ecn
                    if name:
                        conf["conferenceName"]=name.strip(); changed=True; st["conf"]+=1
                if not d.get("_license_blocked") and e:
                    for k in ("imageCaptions","tableCaptions"):
                        ecaps=e.get(k) or []
                        cur=d.get(k) or []
                        curset={(c.get("caption") if isinstance(c,dict) else c) for c in cur}
                        add=[c for c in ecaps if isinstance(c,dict) and isinstance(c.get("caption"),str)
                             and has_letter(c["caption"]) and not c["caption"].strip().isascii()
                             and len(c["caption"].strip())<=2 and c["caption"] not in curset]
                        if add:
                            d[k]=cur+add; drop_junk_captions(d); st["cap"]+=len(add); changed=True
                if changed and not dry:
                    open(mf,"w",encoding="utf-8").write(json.dumps(d,indent=2,ensure_ascii=False))
    return st
ap=argparse.ArgumentParser(); ap.add_argument("--dry-run",action="store_true"); a=ap.parse_args()
st=run(a.dry_run)
print("MODE","DRY" if a.dry_run else "LIVE","| scanned",st["scanned"],"| conf restored",st["conf"],"| caption entries restored",st["cap"])
