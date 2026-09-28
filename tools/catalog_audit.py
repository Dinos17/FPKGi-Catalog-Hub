import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from external_database import fetch_external_database_entries, normalize_category
from pkg_metadata import extract_metadata
from title_resolver import resolve_category


ROOT = Path(__file__).resolve().parent.parent
FALLBACK_WORKERS = 2

CATEGORY_MAP = {
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
    "patch": "updates",
    "gd": "games",
    "gda": "games",
    "gdc": "dlc",
    "gdd": "games",
    "gdl": "games",
    "gdp": "games",
    "gds": "games",
    "gdt": "games",
    "gdu": "games",
    "gdx": "games",
    "gapp": "apps",
    "gtheme": "themes",
    "gpatch": "updates",
    "gup": "updates",
    "gaddon": "dlc",
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
    """Build exact-URL and title-ID category hints from external databases."""
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
            title_id = title_id.strip().upper()
            title_categories.setdefault(title_id, set()).add(category)

    unique_title_categories = {
        title_id: next(iter(values))
        for title_id, values in title_categories.items()
        if len(values) == 1
    }
    return url_categories, unique_title_categories


CACHE_PATH = ROOT / "config" / "title_api_cache.json"
PKG_CACHE_PATH = ROOT / "config" / "pkg_category_cache.json"
CLASSIFICATION_PATH = ROOT / "config" / "title_classifications.json"


def _load_classifications():
    if not CLASSIFICATION_PATH.exists():
        return {}
    try:
        data = json.loads(CLASSIFICATION_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"WARNING: Could not load title classifications: {exc}")
        return {}
    records = data.get("records") if isinstance(data, dict) else None
    return records if isinstance(records, dict) else {}


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
        cache[url] = None
        print(f"  PKG category lookup failed: {record.get('name', url)}: {exc}")
        return None

    category = normalize(metadata.get("category"))
    cache[url] = category
    if category:
        print(f"  PKG category lookup: {record.get('title_id', 'unknown')} -> {category} | {record.get('name', url)}")
    return category

def _load_title_cache():
    if not CACHE_PATH.exists():
        return {}
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"WARNING: Could not load title API cache: {exc}")
        return {}
    return data if isinstance(data, dict) else {}


def _save_title_cache(cache):
    CACHE_PATH.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def resolve_categories(title_ids):
    if not title_ids:
        return {}

    classifications = _load_classifications()
    cache = _load_title_cache()
    results = {}
    for title_id in title_ids:
        classification = classifications.get(title_id)
        if isinstance(classification, dict):
            type_code = str(classification.get("type", "")).upper()
            if type_code == "GAME":
                results[title_id] = "games"
            elif type_code == "APPLICATION":
                results[title_id] = "apps"
    results.update({
        title_id: cache[title_id]
        for title_id in title_ids
        if title_id not in results and title_id in cache
    })
    pending = sorted(set(title_ids) - set(results))

    # Unknown IDs stay unresolved for human classification. Never guess games
    # and never fall back to unreliable per-title network lookups.
    print(
        f"Catalog audit: classified {len(results)} / {len(title_ids)} unique title IDs; "
        f"{len(pending)} need human classification"
    )
    if cache:
        _save_title_cache(cache)

    print(
        f"Catalog audit: resolved {len(results)} / {len(title_ids)} unique title IDs"
    )
    return results


def audit_catalogs():
    moved = []
    scanned = 0
    records = []

    paths = []
    for platform_dir, platform in ((ROOT / "ps4", "PS4"), (ROOT / "ps5", "PS5")):
        paths.extend((platform, path) for path in sorted(platform_dir.glob("*.json")))

    for platform, path in paths:
        if path.name == "new-registrations.json":
            continue

        category = catalog_category(path)
        if category not in {
            "games", "apps", "dlc", "demos", "emulators",
            "homebrew", "themes", "updates", "ps1", "ps2", "psp"
        }:
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
            if not isinstance(title_id, str):
                continue

            title_id = title_id.strip().upper()
            if title_id:
                records.append((platform, path, category, url, record, title_id))

    title_ids = {item[5] for item in records}
    print(
        f"Catalog audit: scanning {scanned} records | "
        f"resolving {len(title_ids)} unique title IDs"
    )

    external_url_categories, external_categories = resolve_external_categories()
    resolved_categories = resolve_categories(title_ids)
    pkg_cache = _load_pkg_cache()

    for platform, path, category, url, record, title_id in records:
        # Preserve an explicit package/catalog category. Title-ID resolution is
        # only a fallback for records that do not already carry one. A DLC,
        # update, demo, or application can share a title ID with its base game.
        # Existing catalogs may predate the current external database layout.
        # Use the configured databases as the strongest fallback so packages
        # already misplaced in games.json can be moved to their real category.
        record_category = normalize(record.get("category"))
        # The catalog file itself is authoritative for specific categories.
        # This prevents a broad title-ID classification from moving DLC,
        # updates, demos, themes, or homebrew into games/apps.
        if category != "games":
            record_category = category
        external_category = external_categories.get(title_id)
        exact_external_category = external_url_categories.get(url)
        resolved_category = resolved_categories.get(title_id)

        # A generic games classification is not authoritative. External
        # databases are used to correct packages such as applications that
        # were previously registered in games.json. Specific classifications
        # such as DLC, updates, demos, themes, and homebrew remain authoritative
        # because those packages can legitimately share a title ID with a base
        # game.
        if record_category and record_category != "games":
            resolved = record_category
        else:
            # Exact package-URL classification is strongest. If it is only
            # generic "games", a title lookup may still correct a known
            # application such as YouTube or Netflix.
            resolved = (
                resolved_category
                if exact_external_category == "games" and resolved_category
                else exact_external_category or resolved_category or external_category or record_category
            )
        if resolved is None and category == "games":
            # Fall back to the actual package PARAM.SFO. CATEGORY is package-specific.
            resolved = _resolve_pkg_category(url, record, pkg_cache)

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
        if url not in destination_entries:
            destination_entries[url] = record

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

        moved.append((platform, title_id, category, resolved, record.get("name", url)))
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
