import json
import re
from pathlib import Path
from urllib.parse import quote, urlsplit

import requests

from pkg_metadata import extract_metadata
from title_resolver import resolve_category, resolve_title


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "external_database.json"
TIMEOUT = 60
API_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "FPKGi-Catalog-Hub/1.0",
}


def _validate_database_url(url):
    if not isinstance(url, str) or not url.strip():
        raise ValueError("External database URL must be a non-empty string")

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


def load_database_urls():
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        config = json.load(file)

    if not isinstance(config, dict):
        raise ValueError("External database configuration must be a JSON object")

    datasets = config.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        raise ValueError('External database configuration must contain a non-empty "datasets" list')

    result = []
    seen = set()

    for index, dataset in enumerate(datasets, start=1):
        if not isinstance(dataset, dict):
            raise ValueError(f"External database entry #{index} must be an object")

        url = _validate_database_url(dataset.get("url"))
        name = dataset.get("name")
        if not isinstance(name, str) or not name.strip():
            name = urlsplit(url).path.rstrip("/").split("/")[-1]

        if url in seen:
            raise ValueError(f"Duplicate external database URL: {url}")

        seen.add(url)
        result.append({"name": name.strip(), "url": url})

    return result


def load_database_url():
    """Backward-compatible single-database accessor for callers/tests."""
    databases = load_database_urls()
    return databases[0]["url"]


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


def fetch_database_files(database_url):
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
    return data


def package_url(database_url, path):
    owner, dataset = _hugging_face_dataset_parts(database_url)
    resolve_url = f"https://huggingface.co/datasets/{owner}/{dataset}/resolve/main"
    return f"{resolve_url}/{quote(path, safe='')}?download=true"


def parse_title_id(name):
    match = re.search(
        r"(?<![A-Z0-9])([A-Z]{4}\d{5})(?!\d)",
        name,
        re.IGNORECASE,
    )
    return match.group(1).upper() if match else None


PKG_CATEGORY_MAP = {
    # PS4 PARAM.SFO CATEGORY values. These are package-level classifications,
    # so they are more authoritative than a broad title-ID classification.
    "gd": "games",
    "gda": "apps",
    "gdc": "apps",
    "gdd": "apps",
    "gde": "apps",
    "gdg": "apps",
    "gdk": "apps",
    "gdl": "apps",
    "gdo": "games",
    "gdp": "games",
    "gds": "games",
    "gdt": "games",
    "gdu": "games",
    "gdx": "games",
    "gapp": "apps",
    "gtheme": "themes",
    "ac": "dlc",
    "gaddon": "dlc",
    "gp": "updates",
    "gpatch": "updates",
    "gup": "updates",
    "gpc": "updates",
    "gpd": "updates",
    "gpe": "updates",
    "gpk": "updates",
    "gpl": "updates",
}


def normalize_category(value):
    if not isinstance(value, str):
        return None
    return PKG_CATEGORY_MAP.get(value.lower(), value.lower())



EXTERNAL_CATEGORY_MAP = {
    "game": "games",
    "games": "games",
    "application": "apps",
    "applications": "apps",
    "app": "apps",
    "apps": "apps",
    "media": "apps",
    "utility": "apps",
    "utilities": "apps",
    "dlc": "dlc",
    "addon": "dlc",
    "add-on": "dlc",
    "demo": "demos",
    "demos": "demos",
    "emulator": "emulators",
    "emulators": "emulators",
    "theme": "themes",
    "themes": "themes",
    "homebrew": "homebrew",
    "update": "updates",
    "updates": "updates",
}


def normalize_external_category(value):
    if not isinstance(value, str):
        return None
    return EXTERNAL_CATEGORY_MAP.get(value.strip().lower())


def category_hint_from_path(path, database_name):
    parts = [part for part in path.split("/") if part]
    path_map = {
        "game": "games",
        "games": "games",
        "application": "apps",
        "applications": "apps",
        "utility": "apps",
        "utilities": "apps",
        "media": "apps",
        "emulator": "emulators",
        "emulators": "emulators",
        "homebrew": "homebrew",
        "update": "updates",
        "updates": "updates",
        "dlc": "dlc",
        "demo": "demos",
        "demos": "demos",
        "theme": "themes",
        "themes": "themes",
    }

    for part in parts:
        category = path_map.get(part.strip().lower())
        if category:
            return category

    if database_name.strip().lower() == "ps-games-dataset":
        return "games"

    return None


def _scan_database(database):
    database_name = database["name"]
    database_url = database["url"]
    files = fetch_database_files(database_url)

    entries = {}
    scanned = 0
    skipped = 0

    for item in files:
        path = item.get("path")
        size = item.get("size")

        if not isinstance(path, str) or not path.lower().endswith(".pkg"):
            continue
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            print(f"  WARNING: Skipping {database_name} file with invalid size: {path}")
            skipped += 1
            continue

        scanned += 1
        url = package_url(database_url, path)
        filename = path.rsplit("/", 1)[-1]
        path_category = category_hint_from_path(path, database_name)

        filename_title_id = parse_title_id(filename)
        metadata = {
            "title_id": filename_title_id,
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
            metadata["category"] = normalize_category(metadata.get("category"))
            print(
                f"  PKG metadata: {database_name} | {filename} | "
                f"{pkg_metadata.get('title_id', 'no-title-id')} | "
                f"{pkg_metadata.get('version', 'no-version')}"
            )
        except Exception as exc:
            print(f"  WARNING: Could not inspect {database_name}/{filename}: {exc}")

        metadata_category = normalize_external_category(metadata.get("category"))
        if metadata_category:
            metadata["category"] = metadata_category
        elif path_category:
            metadata["category"] = path_category

        # Prefer the title ID encoded in the filename. External PKG metadata can
        # be generic or stale (for example, Store metadata embedded in a
        # homebrew package), while the dataset filename identifies the
        # registration being scanned.
        title_id = filename_title_id or metadata.get("title_id")
        if title_id:
            metadata["title_id"] = title_id

        if metadata.get("name") == filename and title_id:
            resolved_name = resolve_title(title_id)
            if resolved_name:
                metadata["name"] = resolved_name
                print(f"  Title lookup: {title_id} -> {resolved_name}")

        if title_id:
            sony_category = resolve_category(title_id)
            resolved_category = normalize_external_category(sony_category)
            if not resolved_category:
                resolved_category = normalize_category(sony_category)
            if resolved_category:
                current_category = normalize_external_category(metadata.get("category"))
                if not current_category or current_category == "games":
                    metadata["category"] = resolved_category
                    print(f"  Category lookup: {title_id} -> {resolved_category}")


        if not metadata.get("category"):
            print(
                f"  WARNING: Could not determine category for "
                f"{database_name}/{filename}; skipping"
            )
            skipped += 1
            continue

        entries[url] = metadata

    return entries, scanned, skipped


def fetch_external_database_entries():
    print("\nFetching external package databases")

    databases = load_database_urls()
    entries = {}
    total_scanned = 0
    total_skipped = 0
    failed = 0

    for database in databases:
        print(f"\nDatabase: {database['name']} -> {database['url']}")
        try:
            database_entries, scanned, skipped = _scan_database(database)
        except Exception as exc:
            failed += 1
            print(f"  WARNING: Database unavailable: {exc}")
            print("  Continuing with the remaining external databases.")
            continue

        total_scanned += scanned
        total_skipped += skipped

        for url, metadata in database_entries.items():
            if url in entries:
                existing_category = normalize_external_category(
                    entries[url].get("category")
                )
                new_category = normalize_external_category(metadata.get("category"))

                # The applications database is authoritative when the same
                # package URL also appears in the games database. This prevents
                # an application duplicated across the two datasets from being
                # locked into games simply because PS-Games-Dataset was scanned
                # first.
                if (
                    database["name"].strip().lower() == "ps-applications"
                    and new_category == "apps"
                    and existing_category == "games"
                ):
                    entries[url] = metadata
                    print(
                        f"  INFO: Application database overrides game category: {url}"
                    )
                else:
                    print(f"  WARNING: Duplicate package URL across databases: {url}")
                continue
            entries[url] = metadata

        print(
            f"  Database result: {scanned} PKG files scanned | "
            f"{len(database_entries)} entries | {skipped} skipped"
        )

    print(
        f"External package databases: {total_scanned} PKG files scanned | "
        f"{len(entries)} entries | {total_skipped} skipped | "
        f"{failed} database failures"
    )
    return entries
