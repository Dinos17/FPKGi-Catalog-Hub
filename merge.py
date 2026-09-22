import json
import requests

CATEGORIES = {
    "games": [
        "https://raw.githubusercontent.com/gop753811-netizen/fpkgi/main/GAMES-archive.org.json",
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/GAMES.json",
        "https://archive.org/download/ps4-fpkg-collection-english-fpkgi/GAMES.json",
    ],
    "updates": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/UPDATES.json",
        "https://archive.org/download/ps4-fpkg-collection-english-fpkgi/UPDATES.json",
    ],
    "dlc": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/DLC.json",
        "https://archive.org/download/ps4-fpkg-collection-english-fpkgi/DLC.json",
    ],
    "homebrew": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/HOMEBREW.json",
    ],
    "demos": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/DEMOS.json",
        "https://archive.org/download/ps4-fpkg-collection-english-fpkgi/DEMO.json",
    ],
    "emulators": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/EMULATORS.json",
    ],
    "themes": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/THEMES.json",
    ],
    "ps1": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/PS1.json",
        "https://archive.org/download/ps4-fpkg-collection-english-fpkgi/PS1.json",
    ],
    "ps2": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/PS2.json",
        "https://archive.org/download/ps4-fpkg-collection-english-fpkgi/PS2.json",
    ],
    "psp": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/PSP.json",
        "https://archive.org/download/ps4-fpkg-collection-english-fpkgi/PSP.json",
    ],
    "apps": [
        "https://raw.githubusercontent.com/ps4arab/fpkgi/main/APPS.json",
        "https://archive.org/download/ps4-fpkg-collection-english-fpkgi/APPS.json",
    ],
}

HEADERS = {
    "User-Agent": "fpkgi-merged/1.0"
}


def load_source(url):
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=60,
    )
    response.raise_for_status()

    data = response.json()

    if not isinstance(data, dict):
        raise ValueError("Top-level JSON is not an object")

    if "DATA" not in data:
        raise ValueError("Missing DATA object")

    entries = data["DATA"]

    if not isinstance(entries, dict):
        raise ValueError("DATA is not an object")

    return entries


def merge_category(category, urls):
    merged = {}

    print(f"\n=== {category.upper()} ===")

    for url in urls:
        try:
            entries = load_source(url)

            added = 0
            duplicates = 0

            for pkg_url, metadata in entries.items():
                if pkg_url in merged:
                    duplicates += 1
                    continue

                merged[pkg_url] = metadata
                added += 1

            print(
                f"OK: {url}\n"
                f"    Source entries: {len(entries)}\n"
                f"    Added: {added}\n"
                f"    Duplicates: {duplicates}"
            )

        except Exception as exc:
            print(f"FAILED: {url}")
            print(f"    Error: {exc}")

    output = {
        "DATA": merged
    }

    output_file = f"{category}.json"

    with open(output_file, "w", encoding="utf-8") as file:
        json.dump(
            output,
            file,
            indent=2,
            ensure_ascii=False,
        )
        file.write("\n")

    print(f"TOTAL {category.upper()}: {len(merged)}")
    print(f"OUTPUT: {output_file}")


def main():
    for category, urls in CATEGORIES.items():
        merge_category(category, urls)


if __name__ == "__main__":
    main()
