import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

CONTENT_ID_RE = re.compile(
    r"(?<![A-Z0-9])([A-Z]{2}\d{4}-[A-Z]{4}\d{5}_\d{2}-[A-Z0-9_]{8,})(?![A-Z0-9])",
    re.IGNORECASE,
)
TITLE_ID_RE = re.compile(r"(?<![A-Z0-9])([A-Z]{4}\d{5})(?!\d)", re.IGNORECASE)


def extract_content_id(value):
    if not isinstance(value, str):
        return None
    match = CONTENT_ID_RE.search(unquote(value))
    return match.group(1).upper() if match else None


def extract_title_id(value):
    if not isinstance(value, str):
        return None
    match = TITLE_ID_RE.search(unquote(value))
    return match.group(1).upper() if match else None


def identity_keys(url, record, category):
    title_id = str(record.get("title_id") or "").strip().upper() or extract_title_id(url)
    content_id = (
        str(record.get("content_id") or "").strip().upper()
        or extract_content_id(url)
    )
    version = str(record.get("version") or "").strip()
    category = category.strip().lower()

    keys = []
    if content_id:
        keys.append(("content", content_id))

    if title_id and version:
        keys.append(("title-version", category, title_id, version))

    # Base games/apps/homebrew normally have one catalog entry per Title ID.
    # Updates and DLC can legitimately have many packages for the same Title ID.
    if title_id and category in {"games", "apps", "homebrew", "demos", "emulators", "themes"}:
        keys.append(("title-category", category, title_id))

    return keys


def load_catalog_identity_index(root, catalog_output_path, catalog_category):
    index = {}
    for platform_dir, platform in ((root / "ps4", "PS4"), (root / "ps5", "PS5")):
        if not platform_dir.exists():
            continue
        for path in sorted(platform_dir.glob("*.json")):
            category = catalog_category(path)
            if not category:
                continue
            try:
                data = __import__("json").loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            entries = data.get("DATA", {}) if isinstance(data, dict) else {}
            if not isinstance(entries, dict):
                continue
            for url, record in entries.items():
                if not isinstance(record, dict):
                    continue
                for key in identity_keys(url, record, category):
                    index.setdefault(key, []).append((path, url, record))
    return index


def find_existing_identity(index, url, record, category):
    matches = {}
    for key in identity_keys(url, record, category):
        for path, old_url, old_record in index.get(key, []):
            matches[(str(path), old_url)] = (path, old_url, old_record)
    return list(matches.values())
