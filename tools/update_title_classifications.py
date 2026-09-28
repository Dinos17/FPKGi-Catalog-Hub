#!/usr/bin/env python3
"""Build the local PS4 Title ID classification database from GitHub-hosted JSON sources."""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

SOURCE_URLS = [
    ("applications", "https://raw.githubusercontent.com/ohhsodead/arisen-studio-database/main/PS4/applications.json"),
    ("homebrew", "https://raw.githubusercontent.com/ohhsodead/arisen-studio-database/main/PS4/homebrew.json"),
]


def fetch_json(url: str):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "FPKGi-Catalog-Hub/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def normalize_title_id(value):
    if not isinstance(value, str):
        return None
    value = value.strip().upper()
    if len(value) != 9 or not value[:4].isalpha() or not value[4:].isdigit():
        return None
    return value


def build_database(sources):
    database = {}

    # Load homebrew first; applications take precedence for overlapping IDs.
    for source_name, payload in sources:
        if not isinstance(payload, dict):
            continue

        for record in payload.get("Mods", []):
            if not isinstance(record, dict):
                continue

            title_id = normalize_title_id(record.get("TitleId"))
            if not title_id:
                continue

            category_id = str(record.get("CategoryId") or "").strip().lower()
            category = "apps" if source_name == "applications" else "homebrew"
            subcategory = None

            if source_name == "homebrew":
                if category_id in {"emu", "emulator"}:
                    category = "emulators"
                    subcategory = "emulator"
                elif category_id == "media":
                    category = "apps"
                    subcategory = "media"
                elif category_id in {"util", "utili", "utility"}:
                    subcategory = "utility"
                elif category_id:
                    subcategory = category_id

            database[title_id] = {
                "name": str(record.get("Name") or "").strip(),
                "type": "APPLICATION" if category == "apps" else category.upper(),
                "category": category,
                "subcategory": subcategory,
                "source": f"arisen-studio-database:{source_name}",
            }

    return dict(sorted(database.items()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    sources = []
    failures = []

    for source_name, source_url in SOURCE_URLS:
        try:
            sources.append((source_name, fetch_json(source_url)))
            print(f"Fetched {source_name} classification database")
        except Exception as exc:
            failures.append(f"{source_name}: {exc}")
            print(f"Source failed: {source_url}: {exc}")

    output_path = Path(__file__).resolve().parent.parent / "config" / "title_classifications.json"

    if not sources:
        if output_path.exists():
            print(f"Classification sources unavailable; keeping existing database: {output_path}")
        else:
            print("No classification source available and no existing database; continuing without one.")
        return 0

    database = build_database(sources)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps({
            "source": "arisen-studio-database",
            "source_urls": [url for _, url in SOURCE_URLS],
            "records": database,
        }, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(f"Parsed {len(database)} PS4 Title ID classifications.")
    for failure in failures:
        print(f"Classification source warning: {failure}")
    print(f"Output: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
