import re
from datetime import datetime

import requests

from pkg_metadata import extract_metadata


GITHUB_REPO = "Dinos17/fpkgi-merged"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
TIMEOUT = 60

RELEASE_TO_CATEGORY = {
    "PS4-games": "games",
    "PS4-apps": "apps",
    "PS4-updates": "updates",
    "PS4-DLC": "dlc",
    "PS4-demos": "demos",
    "PS4-homebrew": "homebrew",
    "PS4-emulators": "emulators",
    "PS4-themes": "themes",
    "PS5-games": "games",
    "PS5-apps": "apps",
    "PS5-updates": "updates",
    "PS5-DLC": "dlc",
    "PS5-demos": "demos",
    "PS5-homebrew": "homebrew",
    "PS5-emulators": "emulators",
    "PS5-themes": "themes",
    "PS1": "ps1",
    "PS2": "ps2",
    "PSP": "psp",
}

TITLE_ID_RE = re.compile(r"(?<![A-Z0-9])(CUSA\d{5})(?!\d)", re.IGNORECASE)
VERSION_RE = re.compile(r"(?:^|[_-])v(\d+(?:\.\d+)+)(?:[_-]|\.)", re.IGNORECASE)


def fetch_releases():
    print("\nFetching GitHub Releases")

    response = requests.get(
        GITHUB_API,
        params={"per_page": 100},
        timeout=TIMEOUT,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "fpkgi-merged/1.0",
        },
    )
    response.raise_for_status()

    releases = response.json()

    if not isinstance(releases, list):
        raise ValueError("GitHub Releases API did not return a list")

    return releases


def asset_to_entry(asset, release, category):
    name = asset.get("name")
    download_url = asset.get("browser_download_url")
    size = asset.get("size")

    if not name or not download_url or not name.lower().endswith(".pkg"):
        return None

    if not isinstance(size, int) or size <= 0:
        raise ValueError(f"Invalid GitHub asset size for {name}")

    metadata = {
        "name": name,
        "size": size,
    }

    try:
        pkg_metadata = extract_metadata(download_url, size)
        metadata.update(pkg_metadata)
        print(
            f"  PKG metadata: {name} | "
            f"{pkg_metadata.get('title_id', 'no-title-id')} | "
            f"{pkg_metadata.get('version', 'no-version')}"
        )
    except Exception as exc:
        print(f"  WARNING: Could not inspect {name}: {exc}")

        title_match = TITLE_ID_RE.search(name)
        if title_match:
            metadata["title_id"] = title_match.group(1).upper()

        version_match = VERSION_RE.search(name)
        if version_match:
            metadata["version"] = version_match.group(1)

    published_at = release.get("published_at")
    if published_at:
        try:
            release_date = datetime.fromisoformat(
                published_at.replace("Z", "+00:00")
            )
            metadata["release"] = release_date.strftime("%d-%m-%Y")
        except ValueError:
            pass

    # APP_VER is the useful version field for update packages.
    if category == "updates" and metadata.get("app_ver"):
        metadata["version"] = metadata["app_ver"]

    metadata.pop("app_ver", None)
    return download_url, metadata


def fetch_release_entries():
    entries_by_category = {
        category: {} for category in set(RELEASE_TO_CATEGORY.values())
    }

    for release in fetch_releases():
        tag = release.get("tag_name")

        if tag not in RELEASE_TO_CATEGORY:
            continue

        category = RELEASE_TO_CATEGORY[tag]
        assets = release.get("assets") or []

        added = 0
        for asset in assets:
            result = asset_to_entry(asset, release, category)
            if result is None:
                continue

            download_url, metadata = result

            if download_url in entries_by_category[category]:
                continue

            entries_by_category[category][download_url] = metadata
            added += 1

        print(f"Release: {tag} | PKG assets: {added}")

    return entries_by_category
