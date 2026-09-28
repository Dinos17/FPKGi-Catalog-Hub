import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from external_database import fetch_external_database_entries
from title_resolver import resolve_category


ROOT = Path(__file__).resolve().parent.parent
FALLBACK_WORKERS = 8

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


def resolve_categories(title_ids):
    results = {}
    if not title_ids:
        return results

    print(
        f"Catalog audit: resolving {len(title_ids)} unique title IDs "
        f"using direct title/category lookup with {FALLBACK_WORKERS} workers"
    )

    def lookup(title_id):
        try:
            return title_id, normalize(resolve_category(title_id))
        except Exception as exc:
            print(f"WARNING: Category lookup failed for {title_id}: {exc}")
            return title_id, None

    with ThreadPoolExecutor(max_workers=FALLBACK_WORKERS) as executor:
        futures = [executor.submit(lookup, title_id) for title_id in sorted(title_ids)]
        for future in as_completed(futures):
            title_id, category = future.result()
            results[title_id] = category

    print(
        f"Catalog audit: resolved {sum(value is not None for value in results.values())} "
        f"/ {len(title_ids)} unique title IDs"
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

    for platform, path, category, url, record, title_id in records:
        # Preserve an explicit package/catalog category. Title-ID resolution is
        # only a fallback for records that do not already carry one. A DLC,
        # update, demo, or application can share a title ID with its base game.
        # Existing catalogs may predate the current external database layout.
        # Use the configured databases as the strongest fallback so packages
        # already misplaced in games.json can be moved to their real category.
        record_category = normalize(record.get("category"))
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

    print(
        f"Catalog audit: scanned {scanned} records | "
        f"reclassified {len(moved)}"
    )
    return moved


if __name__ == "__main__":
    audit_catalogs()
