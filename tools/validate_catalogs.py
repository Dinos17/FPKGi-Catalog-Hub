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


def main():
    sources = load_sources()
    paths = []
    legacy_categories = {"ps1", "ps2", "psp"}

    for category in sources:
        if category in legacy_categories:
            paths.append(ROOT / "ps4" / f"{category}.json")
            continue
        paths.append(ROOT / "ps4" / f"{category}.json")
        ps5_path = ROOT / "ps5" / f"ps5-{category}.json"
        paths.append(ps5_path)

    missing = [path for path in paths if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Missing expected catalog files: " + ", ".join(map(str, missing))
        )

    for path in paths:
        validate_catalog(path)


if __name__ == "__main__":
    main()
