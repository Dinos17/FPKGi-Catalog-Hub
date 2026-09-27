import json
from pathlib import Path

from title_resolver import resolve_category


ROOT = Path(__file__).resolve().parent.parent
CATEGORY_MAP = {
    "game": "games", "games": "games",
    "application": "apps", "applications": "apps", "app": "apps", "apps": "apps",
    "media": "apps", "utility": "apps", "utilities": "apps",
    "dlc": "dlc", "addon": "dlc", "add-on": "dlc",
    "demo": "demos", "demos": "demos",
    "emulator": "emulators", "emulators": "emulators",
    "theme": "themes", "themes": "themes",
    "homebrew": "homebrew",
    "update": "updates", "updates": "updates",
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


def audit_catalogs():
    moved = []
    scanned = 0

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

            for url, record in list(entries.items()):
                scanned += 1
                if not isinstance(record, dict):
                    continue

                title_id = record.get("title_id")
                if not isinstance(title_id, str):
                    continue

                resolved = normalize(resolve_category(title_id))
                if resolved is None or resolved in {"ps1", "ps2", "psp"}:
                    continue

                current_category = category
                if current_category == resolved:
                    continue

                destination = expected_catalog(platform, resolved)
                destination_data = (
                    json.loads(destination.read_text(encoding="utf-8"))
                    if destination.exists()
                    else {"DATA": {}}
                )
                destination_entries = destination_data.setdefault("DATA", {})

                if url not in destination_entries:
                    destination_entries[url] = record
                del entries[url]

                moved.append((platform, title_id, current_category, resolved, record.get("name", url)))
                print(
                    f"Reclassified {platform} {title_id}: "
                    f"{current_category} -> {resolved} | {record.get('name', url)}"
                )

                destination.write_text(
                    json.dumps(destination_data, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )

            path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

    print(f"Catalog audit: scanned {scanned} records | reclassified {len(moved)}")
    return moved


if __name__ == "__main__":
    audit_catalogs()
