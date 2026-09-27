#!/usr/bin/env python3
from pathlib import Path
import json
import os
import re
import tempfile
import time

import requests
from bs4 import BeautifulSoup
from huggingface_hub import HfApi

HF_REPO = "dinos17/PS-Applications"
BASE_URL = "https://pkg-zone.com"
CATEGORIES = [
    "Utility", "Emulator", "Game", "Homebrew", "Update",
    "Media", "DLC", "Retail PKG", "Fake PKG", "Dev Menu",
]
CATEGORY_SLUGS = {
    "Utility": "utility",
    "Emulator": "emulator",
    "Game": "game",
    "Homebrew": "hb",
    "Update": "update",
    "Media": "media",
    "DLC": "dlc",
    "Retail PKG": "retail",
    "Fake PKG": "fake",
    "Dev Menu": "devmenu",
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

def fetch(url):
    last_error = None
    for attempt in range(3):
        try:
            response = SESSION.get(url, timeout=(15, 90))
            response.raise_for_status()
            return response.text
        except requests.RequestException as exc:
            last_error = exc
            print(f"Request failed ({attempt + 1}/3): {url} -> {exc}")
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Unable to fetch {url}: {last_error}")

def extract_card(article, category):
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
    return {
        "id": match.group(1),
        "name": title,
        "author": author,
        "version": re.sub(r"\s+", " ", version).strip().lstrip("vV").strip(),
        "category": category,
        "source": "PKG-Zone",
        "package": f"{BASE_URL}/details/{match.group(1)}",
    }

def collect_category(category):
    slug = CATEGORY_SLUGS[category]
    records = {}
    max_pages = int(os.environ.get("PKG_ZONE_MAX_PAGES", "20"))
    for page in range(1, max_pages + 1):
        url = f"{BASE_URL}/?category={slug}&page={page}"
        html = fetch(url)
        soup = BeautifulSoup(html, "html.parser")
        articles = soup.select("article.pkg")
        if not articles:
            print(f"{category}: page {page}: no package cards; stopping.")
            break
        added = 0
        for article in articles:
            record = extract_card(article, category)
            if record and record["id"] not in records:
                records[record["id"]] = record
                added += 1
        print(f"{category}: page {page}: {len(articles)} cards, {added} new")
        if added == 0:
            break
    return list(records.values())

def load():
    grouped = {category: [] for category in CATEGORIES}
    seen = set()
    for category in CATEGORIES:
        for record in collect_category(category):
            if record["id"] in seen:
                continue
            seen.add(record["id"])
            grouped[category].append(record)

    for category in CATEGORIES:
        grouped[category].sort(key=lambda item: (item["name"].lower(), item["id"]))
        print(f"  {category}: {len(grouped[category])}")

    total = sum(len(items) for items in grouped.values())
    if total == 0:
        raise RuntimeError("PKG-Zone catalog produced 0 categorized records.")
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
            ) + "\n",
            encoding="utf-8",
        )
    (root / "README.md").write_text(
        "# PS-Applications\n\n"
        "Catalog metadata mirrored from PKG-Zone's public catalog pages. "
        "Contains metadata and package/detail URLs, not copied package binaries.\n",
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
