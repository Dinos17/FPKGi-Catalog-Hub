import re
from urllib.parse import quote

import requests

from pkg_metadata import extract_metadata


HF_DATASET = "dinos17/FPKGi-Packages"
HF_API = f"https://huggingface.co/api/datasets/{HF_DATASET}/tree/main"
HF_RESOLVE = f"https://huggingface.co/datasets/{HF_DATASET}/resolve/main"
TIMEOUT = 60
API_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "FPKGi-Catalog-Hub/1.0",
}


def fetch_dataset_files():
    response = requests.get(
        HF_API,
        params={"recursive": "true", "expand": "true"},
        timeout=TIMEOUT,
        headers=API_HEADERS,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        raise ValueError("Hugging Face dataset API did not return a file list")
    return data


def package_url(path):
    return f"{HF_RESOLVE}/{quote(path, safe='')}?download=true"


def parse_title_id(name):
    match = re.search(
        r"(?<![A-Z0-9])((?:CUSA|PPSA)\d{5})(?!\d)",
        name,
        re.IGNORECASE,
    )
    return match.group(1).upper() if match else None


def fetch_dataset_entries():
    print("\nFetching Hugging Face package dataset")
    files = fetch_dataset_files()

    entries = {}
    scanned = 0
    skipped = 0

    for item in files:
        path = item.get("path")
        size = item.get("size")

        if not isinstance(path, str) or not path.lower().endswith(".pkg"):
            continue
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            print(f"  WARNING: Skipping dataset file with invalid size: {path}")
            skipped += 1
            continue

        scanned += 1
        url = package_url(path)
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
        f"Hugging Face dataset: {scanned} PKG files scanned | "
        f"{len(entries)} entries | {skipped} skipped"
    )
    return entries
