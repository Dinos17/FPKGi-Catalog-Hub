import json
from pathlib import Path

import requests


SOURCES = {
    "games": [
        "https://raw.githubusercontent.com/gop753811-netizen/fpkgi/main/GAMES-archive.org.json",
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/GAMES.json",
        "https://dn721605.ca.archive.org/0/items/ps4-fpkg-collection-english-fpkgi/GAMES.json",
    ],
    "dlc": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/DLC.json",
        "https://dn721605.ca.archive.org/0/items/ps4-fpkg-collection-english-fpkgi/DLC.json",
    ],
    "homebrew": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/HOMEBREW.json",
    ],
    "demos": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/DEMOS.json",
    ],
    "emulators": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/EMULATORS.json",
    ],
    "themes": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/THEMES.json",
    ],
    "ps1": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/PS1.json",
    ],
    "ps2": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/PS2.json",
    ],
    "psp": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/PSP.json",
    ],
    "apps": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/APPS.json",
    ],
}


OUTPUT_DIR = Path(__file__).resolve().parent.parent
TIMEOUT = 60


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


def merge_category(category, urls):
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

    for category, urls in SOURCES.items():
        merge_category(category, urls)

    print("\nMerge completed.")


if __name__ == "__main__":
    main()
