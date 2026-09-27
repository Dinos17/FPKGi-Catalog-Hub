#!/usr/bin/env python3
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import json
import os
import re
import tempfile

import requests
from bs4 import BeautifulSoup
from huggingface_hub import HfApi

HF_REPO = "dinos17/PS-Applications"
BASE_URL = "https://pkg-zone.com"
CATEGORIES = [
    "Utility", "Emulator", "Game", "Homebrew", "Update",
    "Media", "DLC", "Retail PKG", "Fake PKG", "Dev Menu",
]
ALIASES = {
    "utility": "Utility", "utilities": "Utility", "store": "Utility",
    "emulator": "Emulator", "emulators": "Emulator",
    "game": "Game", "games": "Game", "hb game": "Game", "homebrew game": "Game",
    "homebrew": "Homebrew", "homebrews": "Homebrew",
    "update": "Update", "updates": "Update", "patch": "Update", "patches": "Update",
    "media": "Media", "dlc": "DLC",
    "retail pkg": "Retail PKG", "retail": "Retail PKG",
    "fake pkg": "Fake PKG", "fake": "Fake PKG",
    "dev menu": "Dev Menu", "devmenu": "Dev Menu",
}

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/153.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
})

def norm(value):
    if not value:
        return None
    return ALIASES.get(" ".join(value.strip().lower().replace("_", " ").split()))

def fetch(url):
    response = SESSION.get(url, timeout=30)
    response.raise_for_status()
    return response.text

def extract_card(article):
    link = article.select_one('a[href*="/details/"]')
    if not link:
        return None
    match = re.search(r"/details/([^/?#]+)", link.get("href", ""))
    if not match:
        return None
    title_node = article.select_one(".title.font-bold")
    author_node = article.select_one(".text-gray-300")
    version_node = article.select_one(".number")
    title = title_node.get_text(" ", strip=True) if title_node else ""
    author = author_node.get_text(" ", strip=True) if author_node else ""
    version = version_node.get_text(" ", strip=True) if version_node else ""
    version = re.sub(r"\\s+", " ", version).strip()
    return {
        "id": match.group(1),
        "name": title,
        "author": author,
        "version": version.lstrip("vV").strip(),
        "details": f"{BASE_URL}/details/{match.group(1)}",
    }

def collect_listing():
    records = {}
    max_pages = int(os.environ.get("PKG_ZONE_MAX_PAGES", "20"))
    for page in range(1, max_pages + 1):
        url = f"{BASE_URL}/?page={page}"
        html = fetch(url)
        soup = BeautifulSoup(html, "html.parser")
        articles = soup.select("article.pkg")
        if not articles:
            print(f"Listing page {page}: no package cards; stopping.")
            break
        added = 0
        for article in articles:
            record = extract_card(article)
            if record and record["id"] not in records:
                records[record["id"]] = record
                added += 1
        print(f"Listing page {page}: {len(articles)} cards, {added} new")
        if added == 0:
            break
    if not records:
        raise RuntimeError("PKG-Zone listing returned no package cards.")
    print(f"Listing: {len(records)} unique packages")
    return list(records.values())

def enrich(record):
    try:
        html = fetch(record["details"])
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)
        match = re.search(r"\\bCategory\\s+([A-Za-z][A-Za-z ]{1,40}?)(?=\\s+(?:Downloads|Ratings|Updated|Download for))", text)
        category = norm(match.group(1)) if match else None
        if not category:
            match = re.search(r"\\bCategory\\s+([A-Za-z][A-Za-z ]{1,40})", text)
            category = norm(match.group(1)) if match else None
        if not category:
            return None, f"{record['id']}: category not recognized"
        record["category"] = category
        record["source"] = "PKG-Zone"
        record["package"] = record["details"]
        record.pop("details", None)
        return record, None
    except Exception as exc:
        return None, f"{record['id']}: {exc}"

def load():
    records = collect_listing()
    workers = int(os.environ.get("PKG_ZONE_WORKERS", "12"))
    grouped = {category: [] for category in CATEGORIES}
    skipped = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(enrich, record) for record in records]
        for future in as_completed(futures):
            record, error = future.result()
            if record:
                grouped[record["category"]].append(record)
            elif error:
                skipped.append(error)
    for category in CATEGORIES:
        grouped[category].sort(key=lambda item: (item.get("name", "").lower(), item.get("id", "")))
        print(f"  {category}: {len(grouped[category])}")
    if skipped:
        print(f"Skipped: {len(skipped)} records")
        for item in skipped[:20]:
            print(f"  {item}")
        if len(skipped) > 20:
            print(f"  ... {len(skipped) - 20} more")
    total = sum(len(items) for items in grouped.values())
    if total == 0:
        raise RuntimeError("PKG-Zone enrichment produced 0 categorized records.")
    print(f"Catalog: {total} categorized records")
    return grouped

def write(grouped, root):
    for category in CATEGORIES:
        directory = root / category
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "catalog.json").write_text(
            json.dumps(
                {
                    "source": f"{BASE_URL}/",
                    "category": category,
                    "records": grouped[category],
                },
                ensure_ascii=False,
                indent=2,
            ) + "\\n",
            encoding="utf-8",
        )
    (root / "README.md").write_text(
        "# PS-Applications\\n\\n"
        "Catalog metadata mirrored from PKG-Zone's public catalog pages. "
        "Contains metadata and package/detail URLs, not copied package binaries.\\n",
        encoding="utf-8",
    )

def upload(root):
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError("HF_TOKEN is not set")
    api = HfApi(token=token)
    api.create_repo(repo_id=HF_REPO, repo_type="dataset", exist_ok=True, private=False)
    api.upload_folder(
        repo_id=HF_REPO,
        repo_type="dataset",
        folder_path=str(root),
        path_in_repo=".",
        commit_message="Update PS-Applications catalog",
    )

def main():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "dataset"
        grouped = load()
        write(grouped, root)
        upload(root)
        print(f"Uploaded to {HF_REPO}")

if __name__ == "__main__":
    main()
