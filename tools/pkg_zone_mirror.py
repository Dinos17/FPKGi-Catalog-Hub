#!/usr/bin/env python3
from pathlib import Path
import json, os, sqlite3, tempfile
import requests
from huggingface_hub import HfApi

STORE_DB_URL="https://api.pkg-zone.com/store.db"
HF_REPO="dinos17/PS-Applications"
CATEGORIES=["Utility","Emulator","Game","Homebrew","Update","Media","DLC","Retail PKG","Fake PKG","Dev Menu"]
ALIASES={
"utility":"Utility","utilities":"Utility","emulator":"Emulator","emulators":"Emulator",
"game":"Game","games":"Game","homebrew":"Homebrew","homebrews":"Homebrew",
"update":"Update","updates":"Update","patch":"Update","patches":"Update",
"media":"Media","dlc":"DLC","retail pkg":"Retail PKG","retail":"Retail PKG",
"fake pkg":"Fake PKG","fake":"Fake PKG","dev menu":"Dev Menu","devmenu":"Dev Menu"}

def norm(v):
    if not v: return None
    return ALIASES.get(" ".join(v.strip().lower().replace("_"," ").split()))

def download(path):
    with requests.get(STORE_DB_URL,timeout=120,stream=True) as r:
        r.raise_for_status()
        with path.open("wb") as f:
            for chunk in r.iter_content(1024*1024):
                if chunk: f.write(chunk)

def load(db):
    con=sqlite3.connect(db); con.row_factory=sqlite3.Row
    try:
        cols={r[1] for r in con.execute("PRAGMA table_info(homebrews)")}
        required={"id","name","package","version","apptype"}
        missing=required-cols
        if missing: raise RuntimeError(f"homebrews missing columns: {sorted(missing)}")
        rows=con.execute("""SELECT id,name,package,version,apptype,desc,image,Size,Author,pv,releaseddate,
                            number_of_downloads,github,video,twitter,md5
                            FROM homebrews WHERE apptype IS NOT NULL
                            ORDER BY name COLLATE NOCASE, version""").fetchall()
        grouped={c:[] for c in CATEGORIES}; skipped=0
        for row in rows:
            category=norm(row["apptype"])
            if not category: skipped+=1; continue
            record={k:row[k] for k in row.keys() if row[k] not in (None,"")}
            record["category"]=category; record["source"]="PKG-Zone"
            grouped[category].append(record)
        print(f"DB: {len(rows)} rows, {sum(map(len,grouped.values()))} categorized, {skipped} skipped")
        for c in CATEGORIES: print(f"  {c}: {len(grouped[c])}")
        return grouped
    finally: con.close()

def write(grouped,root):
    for c in CATEGORIES:
        d=root/c; d.mkdir(parents=True,exist_ok=True)
        (d/"catalog.json").write_text(json.dumps({
            "source":"https://pkg-zone.com/","category":c,"records":grouped[c]
        },ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    (root/"README.md").write_text(
        "# PS-Applications\n\nCatalog metadata mirrored from PKG-Zone's public store database. "
        "Contains metadata and package URLs, not copied package binaries.\n",encoding="utf-8")

def upload(root):
    token=os.environ.get("HF_TOKEN")
    if not token: raise RuntimeError("HF_TOKEN is not set")
    api=HfApi(token=token)
    api.create_repo(repo_id=HF_REPO,repo_type="dataset",exist_ok=True,private=False)
    api.upload_folder(repo_id=HF_REPO,repo_type="dataset",folder_path=str(root),
                      path_in_repo=".",commit_message="Update PS-Applications catalog")

def main():
    with tempfile.TemporaryDirectory() as t:
        db=Path(t)/"store.db"; out=Path(t)/"dataset"; download(db)
        grouped=load(db); write(grouped,out); upload(out)
        print(f"Uploaded to {HF_REPO}")

if __name__=="__main__": main()
