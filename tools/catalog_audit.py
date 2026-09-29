import json
from pathlib import Path

from external_database import fetch_external_database_entries, normalize_category
from pkg_metadata import extract_metadata


ROOT = Path(__file__).resolve().parent.parent

CATEGORY_MAP = {
    "game": "games", "games": "games",
    "application": "apps", "applications": "apps", "app": "apps",
    "apps": "apps", "media": "apps", "utility": "apps", "utilities": "apps",
    "dlc": "dlc", "addon": "dlc", "add-on": "dlc",
    "demo": "demos", "demos": "demos",
    "emulator": "emulators", "emulators": "emulators",
    "theme": "themes", "themes": "themes",
    "homebrew": "homebrew",
    "update": "updates", "updates": "updates", "patch": "updates",
    "gd": "games",
    "gda": "apps", "gdc": "apps", "gdd": "apps", "gde": "apps",
    "gdg": "apps", "gdk": "apps", "gdl": "apps",
    "gdo": "games", "gdp": "games", "gds": "games", "gdt": "games",
    "gdu": "games", "gdx": "games",
    "gapp": "apps", "gtheme": "themes",
    "ac": "dlc",
    "gpatch": "updates", "gup": "updates", "gp": "updates",
    "gpc": "updates", "gpd": "updates", "gpe": "updates",
    "gpk": "updates", "gpl": "updates", "gaddon": "dlc",
}


def normalize(value):
    if not isinstance(value, str):
        return None
    return CATEGORY_MAP.get(value.strip().lower())


def catalog_category(path):
    stem = path.stem.lower()
    if path.parent.name == "ps5" and stem.startswith("ps5-"):
        return stem[4:]
    return stem


def expected_catalog(platform, category):
    if platform == "PS5":
        return ROOT / "ps5" / f"ps5-{category}.json"
    return ROOT / "ps4" / f"{category}.json"


def resolve_external_categories():
    try:
        entries = fetch_external_database_entries()
    except Exception as exc:
        print(f"WARNING: External database category lookup failed: {exc}")
        return {}, {}

    url_categories = {}
    title_categories = {}

    for url, metadata in entries.items():
        category = normalize(metadata.get("category"))
        if not category:
            continue
        url_categories[url] = category
        title_id = metadata.get("title_id")
        if isinstance(title_id, str) and title_id.strip():
            title_categories.setdefault(
                title_id.strip().upper(), set()
            ).add(category)

    return (
        url_categories,
        {
            title_id: next(iter(values))
            for title_id, values in title_categories.items()
            if len(values) == 1
        },
    )


PKG_CACHE_PATH = ROOT / "config" / "pkg_category_cache.json"


def _load_pkg_cache():
    if not PKG_CACHE_PATH.exists():
        return {}
    try:
        data = json.loads(PKG_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"WARNING: Could not load PKG category cache: {exc}")
        return {}
    return data if isinstance(data, dict) else {}


def _save_pkg_cache(cache):
    PKG_CACHE_PATH.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _resolve_pkg_category(url, record, cache):
    if url in cache:
        return normalize(cache[url])

    size = record.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        return None

    try:
        metadata = extract_metadata(url, size)
    except Exception as exc:
        print(f"  PKG category lookup failed: {record.get('name', url)}: {exc}")
        return None

    category = normalize(metadata.get("category"))
    if category not in {
        "games", "apps", "dlc", "demos", "emulators",
        "homebrew", "themes", "updates",
    }:
        return None

    cache[url] = category
    print(
        f"  PKG category lookup: "
        f"{record.get('title_id', 'unknown')} -> {category} | "
        f"{record.get('name', url)}"
    )
    return category


def audit_catalogs():
    moved = []
    scanned = 0
    records = []

    valid_categories = {
        "games", "apps", "dlc", "demos", "emulators",
        "homebrew", "themes", "updates", "ps1", "ps2", "psp",
    }

    for platform_dir, platform in (
        (ROOT / "ps4", "PS4"),
        (ROOT / "ps5", "PS5"),
    ):
        for path in sorted(platform_dir.glob("*.json")):
            if path.name == "new-registrations.json":
                continue

            category = catalog_category(path)
            if category not in valid_categories:
                continue

            data = json.loads(path.read_text(encoding="utf-8"))
            entries = data.get("DATA", {})
            if not isinstance(entries, dict):
                raise ValueError(f"{path}: DATA must be an object")

            for url, record in entries.items():
                scanned += 1
                if not isinstance(record, dict):
                    continue

                title_id = record.get("title_id")
                if isinstance(title_id, str) and title_id.strip():
                    records.append((
                        platform, path, category, url, record,
                        title_id.strip().upper(),
                    ))

    title_ids = {item[5] for item in records}
    print(
        f"Catalog audit: scanning {scanned} records | "
        f"resolving {len(title_ids)} unique title IDs"
    )

    external_url_categories, external_categories = resolve_external_categories()
    pkg_cache = _load_pkg_cache()

    for platform, path, category, url, record, title_id in records:
        exact_external = external_url_categories.get(url)
        title_external = external_categories.get(title_id)

        # Package-level CATEGORY is authoritative whenever available.
        # It describes this exact PKG, unlike title-ID metadata which may
        # describe the base product or another package sharing the title ID.
        pkg_category = _resolve_pkg_category(url, record, pkg_cache)

        if pkg_category:
            resolved = pkg_category
        elif exact_external:
            resolved = exact_external
        elif title_external:
            resolved = title_external
        else:
            resolved = normalize(record.get("category"))

        if resolved is None or resolved in {"ps1", "ps2", "psp"}:
            continue

        if category == resolved:
            continue

        destination = expected_catalog(platform, resolved)
        destination_data = (
            json.loads(destination.read_text(encoding="utf-8"))
            if destination.exists()
            else {"DATA": {}}
        )
        destination_entries = destination_data.setdefault("DATA", {})

        record["category"] = resolved
        destination_entries.setdefault(url, record)

        source_data = json.loads(path.read_text(encoding="utf-8"))
        source_entries = source_data.setdefault("DATA", {})
        source_entries.pop(url, None)

        destination.write_text(
            json.dumps(destination_data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        path.write_text(
            json.dumps(source_data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        moved.append(
            (platform, title_id, category, resolved, record.get("name", url))
        )
        print(
            f"Reclassified {platform} {title_id}: "
            f"{category} -> {resolved} | {record.get('name', url)}"
        )

    _save_pkg_cache(pkg_cache)
    print(
        f"Catalog audit: scanned {scanned} records | "
        f"reclassified {len(moved)}"
    )
    return moved


if __name__ == "__main__":
    audit_catalogs()
