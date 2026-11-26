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
    if filename.startswith("ps5-"):
        return root / PS5_DIR / filename
    return root / PS4_DIR / filename


def expected_catalogs(root):
    with (root / "config" / "sources.json").open("r", encoding="utf-8") as file:
        categories = json.load(file)

    if not isinstance(categories, dict) or not categories:
        raise ValueError("Source configuration must be a non-empty JSON object")

    files = []
    legacy_categories = {"ps1", "ps2", "psp"}
    for category in categories:
        validate_category_name(category)
        if category in legacy_categories:
            files.append(f"ps4/{category}.json")
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


def _preserve_current_provider_entries(current_catalog, artifact_catalog):
    from merge import validate_entry

    if not current_catalog.is_file():
        return
    if not artifact_catalog.is_file():
        raise FileNotFoundError(
            f"Expected generated artifact is missing: {artifact_catalog}"
        )

    current_data = json.loads(current_catalog.read_text(encoding="utf-8"))
    artifact_data = json.loads(artifact_catalog.read_text(encoding="utf-8"))
    current_entries = current_data.get("DATA") if isinstance(current_data, dict) else None
    artifact_entries = artifact_data.get("DATA") if isinstance(artifact_data, dict) else None
    if not isinstance(current_entries, dict) or not isinstance(artifact_entries, dict):
        raise ValueError("Cannot reconcile provider registrations without valid DATA objects")

    for key, metadata in current_entries.items():
        if not isinstance(metadata, dict):
            continue
        provider = metadata.get("download_source")
        if provider not in {"mocha", "deepstorage"}:
            continue
        errors, _ = validate_entry(
            key, metadata, allow_mocha=True, allow_deepstorage=True
        )
        if errors:
            raise ValueError(
                f"Refusing to carry forward invalid {provider} registration "
                f"{key}: {'; '.join(errors)}"
            )
        if key in artifact_entries and artifact_entries[key] != metadata:
            raise ValueError(
                f"Current {provider} registration conflicts with generated artifact: {key}"
            )
        artifact_entries[key] = metadata

    artifact_catalog.write_text(
        json.dumps(artifact_data, indent=2, ensure_ascii=False) + "\\n",
        encoding="utf-8",
    )


def apply_catalog_artifact(root, artifact):
    for filename in expected_catalogs(root):
        source = artifact / filename
        if not source.is_file():
            raise FileNotFoundError(f"Expected generated artifact is missing: {filename}")
        destination = catalog_path(root, filename)
        # The publish job checks out the latest main. Carry provider registrations
        # committed after the build snapshot into the artifact before applying it.
        _preserve_current_provider_entries(destination, source)
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
