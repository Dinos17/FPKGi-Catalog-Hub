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
CONSOLE = "ps4"

CATEGORY_FOLDERS = {
    "utility": "Utility",
    "emulator": "Emulator",
    "game": "Game",
    "homebrew": "Homebrew",
    "update": "Update",
    "media": "Media",
    "dlc": "DLC",
    "retail pkg": "Retail PKG",
    "fake pkg": "Fake PKG",
    "dev menu": "Dev Menu",
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

def fetch(url, stream=False, retries=30):
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
    if not match:
        return ""

    raw = match.group(1).strip().lower()
    # PKG-Zone may expose multiple labels such as "hb game".
    for category in sorted(CATEGORY_FOLDERS, key=len, reverse=True):
        if re.search(rf"(?<![a-z]){re.escape(category)}(?![a-z])", raw):
            return category
    return raw

def extract_playable_version(soup):
    """Return the latest PS4 release's Playable Version from the Releases table."""
    for table in soup.find_all("table"):
        headers = [cell.get_text(" ", strip=True).lower() for cell in table.find_all("th")]
        if not headers or "playable version" not in headers:
            continue

        console_idx = headers.index("console") if "console" in headers else None
        playable_idx = headers.index("playable version")

        for row in table.find_all("tr")[1:]:
            cells = row.find_all(["td", "th"])
            values = [cell.get_text(" ", strip=True) for cell in cells]
            if playable_idx >= len(values):
                continue
            if console_idx is not None and console_idx < len(values):
                if values[console_idx].strip().lower() != "ps4":
                    continue
            return values[playable_idx].strip()

    return ""

def is_plus_playable(playable_version):
    """Accept only PKG-Zone Playable Version values that explicitly contain +."""
    return "+" in playable_version

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
    failed_pages = []
    failed_details = []
    page = 1
    consecutive_page_failures = 0

    while True:
        url = f"{BASE_URL}/?page={page}"
        response = fetch(url)

        if response is None:
            failed_pages.append(page)
            consecutive_page_failures += 1
            print(
                f"PKG-Zone: page {page}: unavailable after retries "
                f"(consecutive failures: {consecutive_page_failures}); continuing."
            )
            if consecutive_page_failures >= 3:
                print(f"PKG-Zone: three consecutive page failures; ending initial scan.")
                break
            page += 1
            continue

        consecutive_page_failures = 0
        soup = BeautifulSoup(response.text, "html.parser")
        articles = soup.select("article.pkg")

        if not articles:
            print(f"PKG-Zone: page {page}: no package cards; reached end of catalog.")
            break

        added = 0
        page_ids = set()

        for article in articles:
            record = extract_card(article)
            if not record or record["id"] in records or record["id"] in page_ids:
                continue
            page_ids.add(record["id"])

            detail = fetch(record["detail_url"])
            if detail is None:
                failed_details.append(record)
                print(f"RETRY LATER {record['id']}: detail page unavailable")
                continue

            soup_detail = BeautifulSoup(detail.text, "html.parser")
            category = extract_category(soup_detail)
            playable_version = extract_playable_version(soup_detail)

            if not is_plus_playable(playable_version):
                print(
                    f"SKIP {record['id']}: PS4 Playable Version is "
                    f"{playable_version or 'missing'}, not X+"
                )
                continue

            folder = CATEGORY_FOLDERS.get(category)
            if not folder:
                print(f"SKIP {record['id']}: unsupported PKG-Zone category {category or 'unknown'}")
                continue

            record["category"] = category
            record["folder"] = folder
            record["playable_version"] = playable_version
            records[record["id"]] = record
            added += 1

        print(f"PKG-Zone: page {page}: {len(articles)} cards, {added} new")
        page += 1

    # Retry pages that failed during the main scan once more before finishing.
    for retry_page in list(dict.fromkeys(failed_pages)):
        response = fetch(f"{BASE_URL}/?page={retry_page}", retries=30)
        if response is None:
            print(f"FINAL SKIP: page {retry_page} still unavailable.")
            continue

        soup = BeautifulSoup(response.text, "html.parser")
        for article in soup.select("article.pkg"):
            record = extract_card(article)
            if not record or record["id"] in records:
                continue

            detail = fetch(record["detail_url"], retries=30)
            if detail is None:
                failed_details.append(record)
                continue

            soup_detail = BeautifulSoup(detail.text, "html.parser")
            category = extract_category(soup_detail)
            playable_version = extract_playable_version(soup_detail)
            folder = CATEGORY_FOLDERS.get(category)
            if folder and is_plus_playable(playable_version):
                record["category"] = category
                record["folder"] = folder
                record["playable_version"] = playable_version
                records[record["id"]] = record

    # Retry each temporarily unavailable detail page once, without another
    # 10-attempt retry loop. A dead detail page must not stall the whole scan.
    for record in list(dict.fromkeys(item["id"] for item in failed_details)):
        original = next(item for item in failed_details if item["id"] == record)
        detail = fetch(original["detail_url"], retries=1)
        if detail is None:
            print(f"FINAL SKIP: {record} detail page still unavailable.")
            continue

        category = extract_category(BeautifulSoup(detail.text, "html.parser"))
        folder = CATEGORY_FOLDERS.get(category)
        if folder:
            original["category"] = category
            original["folder"] = folder
            records[record] = original

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

    for path, folder in files:
        api.upload_file(
            path_or_fileobj=str(path),
            path_in_repo=f"{folder}/{path.name}",
            repo_id=HF_REPO,
            repo_type="dataset",
            commit_message=f"Add public package {path.name}",
        )
        print(f"Uploaded: {folder}/{path.name}")

def main():
    records = collect_records()
    if not records:
        raise RuntimeError("PKG-Zone catalog produced 0 supported records.")

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
                downloaded.append((path, record["folder"]))

        if not downloaded:
            raise RuntimeError("No publicly downloadable PKG files were found.")

        upload_packages(downloaded)

    print(f"Uploaded {len(downloaded)} actual public PKG files to {HF_REPO}/")

if __name__ == "__main__":
    main()
