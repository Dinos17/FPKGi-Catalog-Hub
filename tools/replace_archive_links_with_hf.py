#!/usr/bin/env python3
"""HF-first PKG reconciliation for the FPKGi catalog."""
from __future__ import annotations
import argparse, json, os, re, unicodedata
from pathlib import Path
from urllib.parse import quote, unquote, urlparse
import requests
from pkg_metadata import extract_metadata

OWNER = "dinos17"
HF = "https://huggingface.co"
TIMEOUT = 60
CATALOGS = {
    "ps4/games.json": "PS-Games-Dataset",
    "ps4/apps.json": "PS-Applications",
    "ps4/dlc.json": "PS-Applications",
    "ps4/updates.json": "PS-Applications",
    "ps4/homebrew.json": "PS-Applications",
}
TITLE_ID_RE = re.compile(r"\b([A-Z]{4}\d{5})\b", re.I)
CONTENT_ID_RE = re.compile(r"\b[A-Z]{2}\d{4}-[A-Z0-9]{9}_[A-Z0-9]{2}-[A-Z0-9]{16}\b", re.I)

def hf_tree(repo):
    url = f"{HF}/api/datasets/{OWNER}/{repo}/tree/main"
    params = {"recursive":"true","expand":"false","limit":1000}
    out=[]; s=requests.Session()
    while True:
        r=s.get(url,params=params,timeout=TIMEOUT); r.raise_for_status()
        batch=r.json()
        if not isinstance(batch,list): raise RuntimeError(f"Unexpected HF tree response for {repo}")
        out += [x for x in batch if x.get("type")=="file" and str(x.get("path","")).lower().endswith(".pkg")]
        nxt=None
        for part in r.headers.get("Link","").split(","):
            if 'rel="next"' in part: nxt=part.strip().split(";",1)[0].strip("<> "); break
        if not nxt: return out
        url=nxt; params={}

def norm_name(v):
    if not isinstance(v,str): return ""
    v=unicodedata.normalize("NFKD",v).encode("ascii","ignore").decode().casefold()
    v=re.sub(r"\[[^\]]*\]"," ",v); v=TITLE_ID_RE.sub(" ",v)
    v=re.sub(r"\b(?:v|ver|version)?\s*\d+(?:\.\d+){1,3}\b"," ",v)
    v=re.sub(r"[_./\\-]+"," ",v); v=re.sub(r"[^a-z0-9]+"," ",v)
    return " ".join(v.split())

def norm_ver(v): return v.strip().casefold() if isinstance(v,str) else ""
def content_id(v):
    m=CONTENT_ID_RE.search(unquote(v)) if isinstance(v,str) else None
    return m.group(0).upper() if m else ""

def archive_pkg(v):
    if not isinstance(v,str): return False
    p=urlparse(v)
    return (p.hostname or "").lower().endswith("archive.org") and unquote(p.path).lower().endswith(".pkg")

def hf_url(repo,path):
    return f"{HF}/datasets/{OWNER}/{repo}/resolve/main/{quote(path,safe='/')}?download=true"

def load_records(root):
    out=[]
    for rel,repo in CATALOGS.items():
        p=root/rel
        data=json.loads(p.read_text(encoding="utf-8"))
        for url,rec in data.get("DATA",{}).items():
            if isinstance(url,str) and isinstance(rec,dict):
                out.append({"path":rel,"repo":repo,"url":url,"record":rec})
    return out

def indexes(records):
    idx={k:{} for k in ("content","tvs","ts","nvs")}
    def add(k,key,item):
        if key: idx[k].setdefault(key,[]).append(item)
    for x in records:
        r=x["record"]; tid=str(r.get("title_id") or "").strip().upper()
        ver=norm_ver(r.get("version")); size=r.get("size")
        cid=content_id(x["url"]) or str(r.get("content_id") or "").strip().upper()
        add("content",cid,x)
        if tid and ver and isinstance(size,int): add("tvs",(tid,ver,size),x)
        if tid and isinstance(size,int): add("ts",(tid,size),x)
        n=norm_name(r.get("name"))
        if n and ver and isinstance(size,int): add("nvs",(n,ver,size),x)
    return idx

def uniq(xs):
    seen=set(); out=[]
    for x in xs:
        k=(x["path"],x["url"])
        if k not in seen: seen.add(k); out.append(x)
    return out

def match(meta,idx):
    cid=str(meta.get("content_id") or "").strip().upper()
    tid=str(meta.get("title_id") or "").strip().upper()
    ver=norm_ver(meta.get("version")); size=meta["_size"]; name=norm_name(meta.get("name"))
    checks=[
        ("content_id", idx["content"].get(cid,[]) if cid else []),
        ("title_id + version + size", idx["tvs"].get((tid,ver,size),[]) if tid and ver else []),
        ("title_id + size", idx["ts"].get((tid,size),[]) if tid else []),
        ("name + version + size", idx["nvs"].get((name,ver,size),[]) if name and ver else []),
    ]
    for method,c in checks:
        c=uniq(c)
        if len(c)==1: return c[0],method,[]
        if len(c)>1: return None,None,c
    return None,None,[]

def delete_hf(repo,paths,token):
    from huggingface_hub import HfApi
    api=HfApi(token=token)
    for i in range(0,len(paths),100):
        api.delete_files(repo_id=f"{OWNER}/{repo}",delete_patterns=paths[i:i+100],
                         repo_type="dataset",commit_message="Remove PKGs not needed by FPKGi catalog")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--repo",default="."); ap.add_argument("--apply",action="store_true")
    a=ap.parse_args(); root=Path(a.repo).resolve()
    records=load_records(root); idx=indexes(records)
    repos=sorted(set(CATALOGS.values())); stats={"scanned":0,"failed":0,"replaced":0,"delete":0,"ambiguous":0}
    methods={}; deletes={}; changes={}; report=[]
    for repo in repos:
        for item in hf_tree(repo):
            stats["scanned"]+=1; path=str(item.get("path","")); size=item.get("size")
            if not isinstance(size,int) or size<=0:
                stats["failed"]+=1; report.append(f"METADATA FAILED: {repo}/{path}: invalid size"); continue
            try:
                meta=extract_metadata(hf_url(repo,path),size); meta["_size"]=size
            except Exception as e:
                stats["failed"]+=1; report.append(f"METADATA FAILED: {repo}/{path}: {e}"); continue
            cand,method,amb=match(meta,idx)
            if amb:
                stats["ambiguous"]+=1
                report.append(f"AMBIGUOUS: {repo}/{path} -> "+", ".join(x["path"]+":"+str(x["record"].get("name")) for x in amb))
                continue
            if cand is None:
                deletes.setdefault(repo,[]).append(path); stats["delete"]+=1
                report.append(f"DELETE NO MATCH: {repo}/{path} ({meta.get('title_id')} {meta.get('version')} {size})"); continue
            methods[method]=methods.get(method,0)+1
            if not archive_pkg(cand["url"]):
                deletes.setdefault(repo,[]).append(path); stats["delete"]+=1
                report.append(f"DELETE ALREADY COVERED: {repo}/{path} -> {cand['path']}:{cand['record'].get('name')}"); continue
            cp=root/cand["path"]
            if cp not in changes: changes[cp]=json.loads(cp.read_text(encoding="utf-8"))
            data=changes[cp]; data["DATA"][hf_url(repo,path)]=data["DATA"].pop(cand["url"])
            stats["replaced"]+=1
            report.append(f"REPLACE: {cand['path']} {cand['url']} -> {hf_url(repo,path)} [{method}]")
    print(f"HF PKGs scanned: {stats['scanned']}")
    print(f"Metadata failures: {stats['failed']}")
    print(f"Archive URLs replaced: {stats['replaced']}")
    print(f"HF PKGs to delete: {stats['delete']}")
    print(f"Ambiguous (untouched): {stats['ambiguous']}")
    if methods:
        print("Match methods:"); [print(f"  {k}: {v}") for k,v in sorted(methods.items())]
    print("Planned actions:")
    for line in report[:100]: print("  "+line)
    if len(report)>100: print(f"  ... {len(report)-100} more")
    if not a.apply:
        print("Dry-run only. No changes made."); return 0
    if stats["failed"]:
        print("Refusing --apply because metadata extraction failed."); return 2
    token=os.environ.get("HF_TOKEN")
    for p,data in changes.items(): p.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    for repo,paths in deletes.items(): delete_hf(repo,paths,token)
    print("Applied successfully.")
    return 0

if __name__=="__main__": raise SystemExit(main())
