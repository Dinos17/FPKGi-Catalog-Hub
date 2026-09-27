#!/usr/bin/env python3
from pathlib import Path
import os
import re
import tempfile
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from huggingface_hub import HfApi

HF_REPO = "dinos17/PS-Applications"
BASE_URL = "https://pkg-zone.com"
CATEGORY = "Homebrew"
CATEGORY_SLUG = "hb"
CONSOLE = "ps4"

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

def fetch(url, stream=False, retries=10):
    delay = 2

    for attempt in range(1, retries + 1):
        try:
            response = SESSION.get(url, timeout=(15, 90), stream=stream)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            if attempt == retries:
                print(f"Request failed after {retries} attempts: {url} -> {exc}")
                return None
            print(f"Request failed (attempt {attempt}/{retries}, retrying in {delay}s): {url} -> {exc}")
            time.sleep(delay)
            delay = min(delay * 2, 30)
def extract_card(article):
    link = article.select_one('a[href*="/details/"]')
    if not link:
        return None
    match = re.search(r"/details/([^/?#]+)", link.get("href", ""))
    if not match:
        return None
    title_node = article.select_one(".title.font-bold")
    title = title_node.get_text(" ", strip=True) if title_node else match.group(1)
    return {
        "id": match.group(1),
        "name": title,
        "detail_url": urljoin(BASE_URL, link.get("href", "")),
    }

def extract_category(soup):
    text = soup.get_text(" ", strip=True)
    match = re.search(
        r"\bCategory\s+(.+?)(?=\s+Downloads\b|\s+Ratings\b|\s+Updated\b)",
        text,
        re.I,
    )
    return match.group(1).strip().lower() if match else ""

def extract_package_url(record):
    response = fetch(record["detail_url"])
    if response is None:
        return None
    soup = BeautifulSoup(response.text, "html.parser")

    candidates = [
        f"{BASE_URL}/download/{CONSOLE}/{record['id']}/latest",
    ]

    for anchor in soup.find_all("a", href=True):
        href = urljoin(BASE_URL, anchor["href"])
        text = anchor.get_text(" ", strip=True).lower()
        lower = href.lower()

        if ".pkg" in lower or "download" in text:
            if "login" in text or "login" in lower:
                continue
            candidates.append(href)

    for href in candidates:
        if ".pkg" in href.lower():
            return href

    for href in candidates:
        if "/download/" in href.lower():
            return href

    return None

def collect_records():
    records = {}
    page = 1

    while True:
        url = f"{BASE_URL}/?page={page}"
        response = fetch(url)
        if response is None:
            print(f"{CATEGORY}: page {page}: failed after retries; moving to next page.")
            page += 1
            continue

        soup = BeautifulSoup(response.text, "html.parser")
        articles = soup.select("article.pkg")

        if not articles:
            print(f"{CATEGORY}: page {page}: no package cards; reached end of catalog.")
            break

        page_ids = set()
        added = 0

        for article in articles:
            record = extract_card(article)
            if not record or record["id"] in records or record["id"] in page_ids:
                continue
            page_ids.add(record["id"])

            detail = fetch(record["detail_url"])
            if detail is None:
                print(f"RETRY LATER {record['id']}: detail page unavailable")
                continue

            soup_detail = BeautifulSoup(detail.text, "html.parser")
            category = extract_category(soup_detail)

            if not category.startswith(CATEGORY_SLUG):
                continue

            record["category"] = category
            records[record["id"]] = record
            added += 1

        print(f"{CATEGORY}: page {page}: {len(articles)} cards, {added} new")

        # A repeated page means pagination has ended even if the site still
        # returns HTML/cards instead of an empty page.
        if page > 1 and all(record["id"] in records for record in (
            extract_card(article) for article in articles if extract_card(article)
        )):
            print(f"{CATEGORY}: page {page}: no new package IDs; reached end of catalog.")
            break

        page += 1

    return list(records.values())

def download_package(record, destination):
    package_url = extract_package_url(record)
    if not package_url:
        print(f"SKIP {record['id']}: no public direct package download found")
        return None

    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", record["name"]).strip("._")
    filename = f"{safe_name or record['id']}_{record['id']}.pkg"
    path = destination / filename

    print(f"Downloading {record['id']}: {package_url}")
    response = fetch(package_url, stream=True)
    if response is None:
        print(f"SKIP {record['id']}: package download failed after retries")
        return None

    with path.open("wb") as output:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                output.write(chunk)

    if path.stat().st_size == 0:
        path.unlink(missing_ok=True)
        raise RuntimeError(f"Downloaded empty package for {record['id']}")

    print(f"Downloaded {filename}: {path.stat().st_size} bytes")
    return path

def remove_old_catalog_jsons(api):
    for category in (
        "Utility", "Emulator", "Game", "Homebrew", "Update",
        "Media", "DLC", "Retail PKG", "Fake PKG", "Dev Menu",
    ):
        path = f"{category}/catalog.json"
        try:
            api.delete_file(
                path_in_repo=path,
                repo_id=HF_REPO,
                repo_type="dataset",
                commit_message=f"Remove metadata catalog {path}",
            )
            print(f"Removed old metadata file: {path}")
        except Exception as exc:
            print(f"Metadata cleanup skipped for {path}: {exc}")

def upload_packages(files):
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError("HF_TOKEN is not set")

    api = HfApi(token=token)
    api.create_repo(
        repo_id=HF_REPO,
        repo_type="dataset",
        exist_ok=True,
        private=False,
    )
    remove_old_catalog_jsons(api)

    for path in files:
        api.upload_file(
            path_or_fileobj=str(path),
            path_in_repo=f"{CATEGORY}/{path.name}",
            repo_id=HF_REPO,
            repo_type="dataset",
            commit_message=f"Add homebrew package {path.name}",
        )
        print(f"Uploaded: {CATEGORY}/{path.name}")

def main():
    records = collect_records()
    if not records:
        raise RuntimeError("PKG-Zone Homebrew catalog produced 0 records.")

    with tempfile.TemporaryDirectory() as temp:
        package_dir = Path(temp)
        downloaded = []

        for record in records:
            try:
                path = download_package(record, package_dir)
            except Exception as exc:
                print(f"SKIP {record['id']}: download failed: {exc}")
                continue
            if path:
                downloaded.append(path)

        if not downloaded:
            raise RuntimeError("No publicly downloadable Homebrew PKG files were found.")

        upload_packages(downloaded)

    print(f"Uploaded {len(downloaded)} actual PKG files to {HF_REPO}/{CATEGORY}/")

if __name__ == "__main__":
    main()
