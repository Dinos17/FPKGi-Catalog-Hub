import json
from pathlib import Path

from merge import MOCHA_FILE_ID_RE, validate_entry, load_sources

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
    for key, metadata in entries.items():
        errors, _ = validate_entry(key, metadata, allow_mocha=True)
        if errors:
            rejected += 1
            print(f"ERROR: {path}: {key}: {'; '.join(errors)}")
    if rejected:
        raise ValueError(f"{path}: {rejected} invalid entries")
    print(f"OK: {path} ({len(entries)} entries)")
    return entries


def main():
    sources = load_sources()
    paths = []
    legacy_categories = {"ps1", "ps2", "psp"}
    for category in sources:
        paths.append(ROOT / "ps4" / f"{category}.json")
        if category not in legacy_categories:
            paths.append(ROOT / "ps5" / f"ps5-{category}.json")

    missing = [path for path in paths if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Missing expected catalog files: " + ", ".join(map(str, missing))
        )

    seen_mocha_ids = {}
    for path in paths:
        entries = validate_catalog(path)
        for file_id, metadata in entries.items():
            if not isinstance(metadata, dict) or metadata.get("download_source") != "mocha":
                continue
            if not MOCHA_FILE_ID_RE.fullmatch(file_id):
                # validate_catalog already reports this; keep the global pass safe.
                continue
            previous = seen_mocha_ids.get(file_id)
            if previous is not None:
                raise ValueError(
                    f"Duplicate Mocha file ID {file_id!r} appears in "
                    f"{previous} and {path}"
                )
            seen_mocha_ids[file_id] = path


if __name__ == "__main__":
    main()
