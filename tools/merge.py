import json
import re
from pathlib import Path

import requests

from release_sources import fetch_release_entries


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "sources.json"
OUTPUT_DIR = Path(__file__).resolve().parent.parent
TIMEOUT = 60


def load_sources():
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        sources = json.load(file)

    if not isinstance(sources, dict):
        raise ValueError("Source configuration must be a JSON object")

    return sources


def fetch_source(url):
    print(f"\nFetching: {url}")

    response = requests.get(
        url,
        timeout=TIMEOUT,
        headers={"User-Agent": "FPKGi-Catalog-Hub/1.0"},
    )
    response.raise_for_status()

    data = response.json()

    if not isinstance(data, dict):
        raise ValueError("Top-level JSON must be an object")

    entries = data.get("DATA")

    if not isinstance(entries, dict):
        raise ValueError('JSON must contain a "DATA" object')

    return entries



ALLOWED_METADATA_TYPES = (str, int, float, bool, type(None))
TITLE_ID_RE = re.compile(r"^[A-Z]{4}\\d{5}$", re.IGNORECASE)
VERSION_RE = re.compile(r"^\\d+(?:\\.\\d+)*$")


def validate_entry(pkg_url, metadata):
    errors = []
    warnings = []

    if not isinstance(pkg_url, str) or not pkg_url.strip():
        errors.append("package URL is missing or is not a string")
    elif not pkg_url.lower().startswith(("http://", "https://")):
        errors.append("package URL is not HTTP(S)")

    if not isinstance(metadata, dict):
        errors.append("metadata is not an object")
        return errors, warnings

    for key, value in metadata.items():
        if not isinstance(value, ALLOWED_METADATA_TYPES):
            errors.append(f"metadata field '{key}' has an unsupported type")

    name = metadata.get("name")
    if not isinstance(name, str) or not name.strip():
        warnings.append("missing name")

    title_id = metadata.get("title_id")
    if title_id not in (None, ""):
        if not isinstance(title_id, str) or not TITLE_ID_RE.fullmatch(title_id.strip()):
            warnings.append(f"invalid title_id: {title_id!r}")

    size = metadata.get("size")
    if size is not None:
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            warnings.append(f"invalid size: {size!r}")

    version = metadata.get("version")
    if version not in (None, ""):
        if not isinstance(version, str) or not VERSION_RE.fullmatch(version.strip()):
            warnings.append(f"invalid version: {version!r}")

    return errors, warnings


def validate_entries(entries, source_name):
    if not isinstance(entries, dict):
        raise ValueError(f"{source_name}: DATA must be an object")

    valid = {}
    rejected = 0
    warning_count = 0

    for pkg_url, metadata in entries.items():
        errors, warnings = validate_entry(pkg_url, metadata)

        if errors:
            rejected += 1
            print(
                f"WARNING: Rejected entry from {source_name}: "
                f"{'; '.join(errors)}"
            )
            continue

        if warnings:
            warning_count += len(warnings)
            print(
                f"WARNING: Entry from {source_name}: "
                f"{pkg_url} | {'; '.join(warnings)}"
            )

        valid[pkg_url] = metadata

    if rejected or warning_count:
        print(
            f"Validation: {len(valid)} accepted | "
            f"{rejected} rejected | {warning_count} warnings"
        )

    return valid

def merge_category(category, urls, release_entries):
    merged = {}
    total_source_entries = 0

    print(f"\n{'=' * 60}")
    print(f"{category.upper()}")
    print(f"{'=' * 60}")

    for url in urls:
        try:
            entries = fetch_source(url)
            entries = validate_entries(entries, url)
            total_source_entries += len(entries)

            added = 0
            duplicates = 0

            for pkg_url, metadata in entries.items():
                if pkg_url in merged:
                    duplicates += 1
                    continue

                merged[pkg_url] = metadata
                added += 1

            print(
                f"Source: {len(entries)} entries | "
                f"Added: {added} | Duplicates: {duplicates}"
            )

        except Exception as exc:
            print(f"ERROR: {exc}")
            print("Skipping this source.")

    release_entries = validate_entries(release_entries, "GitHub Releases")

    release_added = 0
    release_duplicates = 0

    for pkg_url, metadata in release_entries.items():
        if pkg_url in merged:
            release_duplicates += 1
            continue

        merged[pkg_url] = metadata
        release_added += 1

    if release_entries:
        print(
            f"GitHub Releases: {len(release_entries)} assets | "
            f"Added: {release_added} | Duplicates: {release_duplicates}"
        )

    output = {
        "DATA": merged
    }

    output_path = OUTPUT_DIR / f"{category}.json"

    with output_path.open("w", encoding="utf-8") as file:
        json.dump(output, file, indent=2, ensure_ascii=False)
        file.write("\n")

    print(f"Total source entries: {total_source_entries}")
    print(f"Final unique entries: {len(merged)}")
    print(f"Output: {output_path}")

    return len(merged)


def main():
    print("FPKGi JSON Merger")
    print("=================")

    sources = load_sources()
    release_entries, ps5_release_entries = fetch_release_entries()

    for category, urls in sources.items():
        merge_category(
            category,
            urls,
            release_entries.get(category, {}),
        )

        ps5_output = {
            "DATA": ps5_release_entries.get(category, {})
        }
        ps5_output_path = OUTPUT_DIR / f"ps5-{category}.json"
        with ps5_output_path.open("w", encoding="utf-8") as file:
            json.dump(ps5_output, file, indent=2, ensure_ascii=False)
            file.write("\n")
        print(
            f"PS5 catalog: {ps5_output_path} | "
            f"Entries: {len(ps5_output['DATA'])}"
        )

    print("\nMerge completed.")


if __name__ == "__main__":
    main()
