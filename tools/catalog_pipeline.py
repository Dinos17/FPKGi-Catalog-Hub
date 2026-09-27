import json
import shutil
from pathlib import Path

from catalog_names import validate_category_name


MANIFEST_PATH = "config/generated_catalogs.json"
PS4_DIR = "ps4"
PS5_DIR = "ps5"


def catalog_path(root, filename):
    path = Path(filename)
    if len(path.parts) > 1:
        return root / path
    if filename.startswith("ps5-") or filename == "ps5.json" or filename in {"ps1.json", "ps2.json", "psp.json"}:
        return root / PS5_DIR / filename
    return root / PS4_DIR / filename



def expected_catalogs(root):
    with (root / "config" / "sources.json").open("r", encoding="utf-8") as file:
        categories = json.load(file)

    if not isinstance(categories, dict) or not categories:
        raise ValueError("Source configuration must be a non-empty JSON object")

    files = ["ps5/ps5.json"]
    legacy_categories = {"ps1", "ps2", "psp"}
    for category in categories:
        validate_category_name(category)
        if category in legacy_categories:
            files.append(f"ps5/{category}.json")
        else:
            files.extend((f"ps4/{category}.json", f"ps5/ps5-{category}.json"))
    return files


def prepare_catalog_artifact(root, output):
    output.mkdir(parents=True, exist_ok=True)

    for filename in expected_catalogs(root):
        source = catalog_path(root, filename)
        if not source.is_file():
            raise FileNotFoundError(f"Expected generated catalog is missing: {filename}")
        destination = output / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _load_manifest(root):
    manifest_path = root / MANIFEST_PATH
    if not manifest_path.is_file():
        return set()

    with manifest_path.open("r", encoding="utf-8") as file:
        manifest = json.load(file)

    if not isinstance(manifest, list) or not all(isinstance(item, str) for item in manifest):
        raise ValueError("Generated catalog manifest must be a JSON array of filenames")

    return set(manifest)


def _write_manifest(root, expected):
    manifest_path = root / MANIFEST_PATH
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8") as file:
        json.dump(sorted(expected), file, indent=2)
        file.write("\n")


def reconcile_catalog_files(root):
    expected = set(expected_catalogs(root))
    previous = _load_manifest(root)
    removed = []

    for filename in previous - expected:
        path = catalog_path(root, filename)
        if path.is_file():
            path.unlink()
            removed.append(filename)

    _write_manifest(root, expected)
    return sorted(removed)


def apply_catalog_artifact(root, artifact):
    for filename in expected_catalogs(root):
        source = artifact / filename
        if not source.is_file():
            raise FileNotFoundError(f"Expected generated artifact is missing: {filename}")
        destination = catalog_path(root, filename)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    return reconcile_catalog_files(root)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "apply"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--artifact", type=Path, required=True)
    args = parser.parse_args()

    if args.action == "prepare":
        prepare_catalog_artifact(args.root, args.artifact)
    else:
        apply_catalog_artifact(args.root, args.artifact)
