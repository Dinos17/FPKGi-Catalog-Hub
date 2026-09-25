import re
from datetime import datetime

import requests

from pkg_metadata import extract_metadata


GITHUB_REPO = "Dinos17/FPKGi-Catalog-Hub"
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

TITLE_ID_RE = re.compile(r"(?<![A-Z0-9])((?:CUSA|PPSA)\d{5})(?!\d)", re.IGNORECASE)
VERSION_RE = re.compile(r"(?:^|[_-])v(\d+(?:\.\d+)+)(?:[_-]|\.)", re.IGNORECASE)

API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "FPKGi-Catalog-Hub/1.0",
}


def fetch_paginated_json(url):
    items = []
    page = 1

    while True:
        response = requests.get(
            url,
            params={"per_page": 100, "page": page},
            timeout=TIMEOUT,
            headers=API_HEADERS,
        )
        response.raise_for_status()

        data = response.json()
        if not isinstance(data, list):
            raise ValueError(f"GitHub API did not return a list for {url}")

        items.extend(data)

        if "next" not in response.links:
            break

        page += 1

    return items


def fetch_releases():
    print("\nFetching GitHub Releases")

    releases = fetch_paginated_json(GITHUB_API)

    print(f"Fetched {len(releases)} GitHub releases.")
    return releases


def fetch_release_assets(release):
    assets_url = release.get("assets_url")

    if not assets_url:
        raise ValueError(
            f"Release {release.get('tag_name', '<unknown>')} has no assets_url"
        )

    assets = fetch_paginated_json(assets_url)

    return assets


def parse_cover_urls(release):
    """Read optional cover URLs from a Markdown table in the release body."""
    body = release.get("body")
    if not isinstance(body, str) or not body.strip():
        return {}

    cover_urls = {}
    header = None
    separator = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")

    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or "|" not in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        normalized = [cell.lower() for cell in cells]
        if header is None and "package" in normalized and "cover url" in normalized:
            header = normalized
            continue
        if header is None or separator.match(line):
            continue
        if len(cells) != len(header):
            continue
        package_name = cells[header.index("package")].strip("`").strip()
        cover_url = cells[header.index("cover url")].strip().strip("<>")
        if package_name and cover_url.startswith(("http://", "https://")) and package_name.lower().endswith(".pkg"):
            cover_urls[package_name] = cover_url
    return cover_urls


def asset_to_entry(asset, release, category, cover_urls=None):
    name = asset.get("name")
    download_url = asset.get("browser_download_url")
    size = asset.get("size")
    cover_urls = cover_urls or {}

    if not isinstance(name, str) or not name.strip():
        return None
    if not isinstance(download_url, str) or not download_url.strip():
        return None
    if not name.lower().endswith(".pkg"):
        return None
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        return None

    name = name.strip()
    download_url = download_url.strip()

    cover_url = cover_urls.get(name)
    if cover_url is None:
        cover_url = next(
            (
                url
                for package_name, url in cover_urls.items()
                if isinstance(package_name, str)
                and package_name.lower() == name.lower()
            ),
            None,
        )

    metadata = {
        "title_id": None,
        "region": None,
        "name": name,
        "version": None,
        "release": None,
        "size": size,
        "min_fw": None,
        "cover_url": cover_url if isinstance(cover_url, str) else None,
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

    # Optional cover URLs come from the release body Markdown table.
    # APP_VER is already mapped to "version" by pkg_metadata.py.
    return download_url, metadata


def fetch_release_entries():
    entries_by_category = {
        category: {} for category in set(RELEASE_TO_CATEGORY.values())
    }
    ps5_entries_by_category = {
        category: {} for category in set(RELEASE_TO_CATEGORY.values())
    }

    for release in fetch_releases():
        tag = release.get("tag_name")

        if tag not in RELEASE_TO_CATEGORY:
            continue

        category = RELEASE_TO_CATEGORY[tag]

        try:
            assets = fetch_release_assets(release)
        except Exception as exc:
            print(f"ERROR: Could not fetch assets for {tag}: {exc}")
            print("Skipping this release.")
            continue

        cover_urls = parse_cover_urls(release)
        added = 0
        for asset in assets:
            try:
                result = asset_to_entry(asset, release, category, cover_urls)
            except Exception as exc:
                print(f"WARNING: Skipping malformed asset in {tag}: {exc}")
                continue

            if result is None:
                continue

            download_url, metadata = result

            if download_url in entries_by_category[category]:
                continue

            entries_by_category[category][download_url] = metadata

            if tag.startswith("PS5-"):
                ps5_entries_by_category[category][download_url] = metadata
            added += 1

        print(f"Release: {tag} | PKG assets: {added}")

    return entries_by_category, ps5_entries_by_category
