import json
import re
from pathlib import Path
from urllib.parse import quote, urlsplit

import requests

from pkg_metadata import extract_metadata


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "external_database.json"
TIMEOUT = 60
API_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "FPKGi-Catalog-Hub/1.0",
}


def load_database_url():
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        config = json.load(file)

    if not isinstance(config, dict):
        raise ValueError("External database configuration must be a JSON object")

    url = config.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ValueError('External database configuration must contain a non-empty "url"')

    url = url.strip()
    parsed = urlsplit(url)

    if parsed.scheme.lower() != "https":
        raise ValueError("External database URL must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("External database URL must not contain embedded credentials")
    if parsed.port is not None:
        raise ValueError("External database URL must not specify a custom port")
    if not parsed.hostname:
        raise ValueError("External database URL must contain a hostname")

    return url.rstrip("/")


def _hugging_face_dataset_parts(database_url):
    parsed = urlsplit(database_url)
    hostname = (parsed.hostname or "").lower().rstrip(".")

    if hostname != "huggingface.co":
        raise ValueError(
            f"Unsupported external database host: {hostname}. "
            "Currently supported: huggingface.co"
        )

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 3 or parts[0] != "datasets":
        raise ValueError(
            "Hugging Face external database URL must be "
            "https://huggingface.co/datasets/<owner>/<dataset>"
        )

    return parts[1], parts[2]


def fetch_database_files():
    database_url = load_database_url()
    owner, dataset = _hugging_face_dataset_parts(database_url)
    api_url = f"https://huggingface.co/api/datasets/{owner}/{dataset}/tree/main"

    response = requests.get(
        api_url,
        params={"recursive": "true", "expand": "true"},
        timeout=TIMEOUT,
        headers=API_HEADERS,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        raise ValueError("External database API did not return a file list")
    return data, database_url


def package_url(database_url, path):
    owner, dataset = _hugging_face_dataset_parts(database_url)
    resolve_url = f"https://huggingface.co/datasets/{owner}/{dataset}/resolve/main"
    return f"{resolve_url}/{quote(path, safe='')}?download=true"


def parse_title_id(name):
    match = re.search(
        r"(?<![A-Z0-9])((?:CUSA|PPSA)\d{5})(?!\d)",
        name,
        re.IGNORECASE,
    )
    return match.group(1).upper() if match else None


def fetch_dataset_entries():
    print("\nFetching external package database")
    files, database_url = fetch_database_files()

    entries = {}
    scanned = 0
    skipped = 0

    for item in files:
        path = item.get("path")
        size = item.get("size")

        if not isinstance(path, str) or not path.lower().endswith(".pkg"):
            continue
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            print(f"  WARNING: Skipping database file with invalid size: {path}")
            skipped += 1
            continue

        scanned += 1
        url = package_url(database_url, path)
        filename = path.rsplit("/", 1)[-1]

        metadata = {
            "title_id": parse_title_id(filename),
            "region": None,
            "name": filename,
            "version": None,
            "release": None,
            "size": size,
            "min_fw": None,
            "cover_url": None,
        }

        try:
            pkg_metadata = extract_metadata(url, size)
            metadata.update(pkg_metadata)
            print(
                f"  PKG metadata: {filename} | "
                f"{pkg_metadata.get('title_id', 'no-title-id')} | "
                f"{pkg_metadata.get('version', 'no-version')}"
            )
        except Exception as exc:
            print(f"  WARNING: Could not inspect {filename}: {exc}")

        entries[url] = metadata

    print(
        f"External package database: {scanned} PKG files scanned | "
        f"{len(entries)} entries | {skipped} skipped"
    )
    return entries
