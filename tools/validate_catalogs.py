import json
from pathlib import Path

from merge import validate_entry, load_sources

ROOT = Path(__file__).resolve().parent.parent


def read_catalog(path):
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, dict) or not isinstance(data.get("DATA"), dict):
        raise ValueError(f"{path}: missing valid DATA object")

    return data["DATA"]


def validate_catalog(path):
    entries = read_catalog(path)

    rejected = 0
    for pkg_url, metadata in entries.items():
        errors, _ = validate_entry(pkg_url, metadata)
        if errors:
            rejected += 1
            print(f"ERROR: {path}: {pkg_url}: {'; '.join(errors)}")

    if rejected:
        raise ValueError(f"{path}: {rejected} invalid entries")

    print(f"OK: {path} ({len(entries)} entries)")
    return entries


def validate_ps5_catalogs(ps5_entries, ps5_category_entries):
    category_union = {}
    for category, entries in ps5_category_entries.items():
        for pkg_url, metadata in entries.items():
            if pkg_url in category_union and category_union[pkg_url] != metadata:
                raise ValueError(
                    f"PS5 catalog conflict for {pkg_url}: "
                    f"metadata differs between categories"
                )
            category_union[pkg_url] = metadata

            if "/PS4-" in pkg_url:
                raise ValueError(
                    f"PS5 catalog contains PS4 release asset: {pkg_url}"
                )

            title_id = metadata.get("title_id")
            if isinstance(title_id, str) and title_id and not title_id.upper().startswith("PPSA"):
                raise ValueError(
                    f"PS5 catalog entry has non-PS5 title ID {title_id}: {pkg_url}"
                )

    if ps5_entries != category_union:
        missing = sorted(set(category_union) - set(ps5_entries))
        extra = sorted(set(ps5_entries) - set(category_union))
        raise ValueError(
            "ps5.json does not equal the union of ps5-* catalogs: "
            f"missing={missing[:5]}, extra={extra[:5]}"
        )

    print(f"OK: PS5 semantic validation ({len(ps5_entries)} entries)")


def main():
    sources = load_sources()
    paths = [ROOT / "ps5.json"]
    category_paths = {}

    for category in sources:
        paths.append(ROOT / f"{category}.json")
        ps5_path = ROOT / f"ps5-{category}.json"
        paths.append(ps5_path)
        category_paths[category] = ps5_path

    missing = [path for path in paths if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Missing expected catalog files: " + ", ".join(map(str, missing))
        )

    for path in paths:
        validate_catalog(path)

    ps5_entries = read_catalog(ROOT / "ps5.json")
    ps5_category_entries = {
        category: read_catalog(path)
        for category, path in category_paths.items()
    }
    validate_ps5_catalogs(ps5_entries, ps5_category_entries)


if __name__ == "__main__":
    main()
