import json
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
        headers={"User-Agent": "fpkgi-merged/1.0"},
    )
    response.raise_for_status()

    data = response.json()

    if not isinstance(data, dict):
        raise ValueError("Top-level JSON must be an object")

    entries = data.get("DATA")

    if not isinstance(entries, dict):
        raise ValueError('JSON must contain a "DATA" object')

    return entries


def merge_category(category, urls, release_entries):
    merged = {}
    total_source_entries = 0

    print(f"\n{'=' * 60}")
    print(f"{category.upper()}")
    print(f"{'=' * 60}")

    for url in urls:
        try:
            entries = fetch_source(url)
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
    release_entries = fetch_release_entries()

    for category, urls in sources.items():
        merge_category(
            category,
            urls,
            release_entries.get(category, {}),
        )

    print("\nMerge completed.")


if __name__ == "__main__":
    main()
