import json
from pathlib import Path

from merge import validate_entry, load_sources

ROOT = Path(__file__).resolve().parent.parent


def validate_catalog(path):
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, dict) or not isinstance(data.get("DATA"), dict):
        raise ValueError(f"{path}: missing valid DATA object")

    rejected = 0
    for pkg_url, metadata in data["DATA"].items():
        errors, _ = validate_entry(pkg_url, metadata)
        if errors:
            rejected += 1
            print(f"ERROR: {path}: {pkg_url}: {'; '.join(errors)}")

    if rejected:
        raise ValueError(f"{path}: {rejected} invalid entries")

    print(f"OK: {path} ({len(data['DATA'])} entries)")


def main():
    sources = load_sources()
    paths = []

    paths.append(ROOT / "ps5.json")

    for category in sources:
        paths.append(ROOT / f"{category}.json")
        paths.append(ROOT / f"ps5-{category}.json")

    missing = [path for path in paths if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Missing expected catalog files: " + ", ".join(map(str, missing))
        )

    for path in paths:
        validate_catalog(path)


if __name__ == "__main__":
    main()
