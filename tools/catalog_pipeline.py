import json
import shutil
from pathlib import Path


def expected_catalogs(root):
    with (root / "config" / "sources.json").open("r", encoding="utf-8") as file:
        categories = json.load(file)

    files = ["ps5.json"]
    for category in categories:
        files.extend((f"{category}.json", f"ps5-{category}.json"))
    return files


def prepare_catalog_artifact(root, output):
    output.mkdir(parents=True, exist_ok=True)

    for filename in expected_catalogs(root):
        source = root / filename
        if not source.is_file():
            raise FileNotFoundError(f"Expected generated catalog is missing: {filename}")
        shutil.copy2(source, output / filename)


def apply_catalog_artifact(root, artifact):
    for filename in expected_catalogs(root):
        source = artifact / filename
        if not source.is_file():
            raise FileNotFoundError(f"Expected generated artifact is missing: {filename}")
        shutil.copy2(source, root / filename)


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
