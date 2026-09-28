import json
import re
from html import unescape
from pathlib import Path
from urllib.parse import urlsplit
import hashlib
import hmac

import requests


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "title_database.json"
TIMEOUT = 30
SONY_TMDB_KEY = bytes.fromhex("F5DE66D2680E255B2DF79E74F890EBF349262F618BCAE2A9ACCDEE5156CE8DF2CDF2D48C71173CDC2594465B87405D197CF1AED3B7E9671EEB56CA6753C2E6B0")
HEADERS = {
    "Accept": "text/html,application/xhtml+xml",
    "User-Agent": "FPKGi-Catalog-Hub/1.0",
}


def _load_config():
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        config = json.load(file)
    if not isinstance(config, dict):
        raise ValueError("Title database configuration must be a JSON object")
    return config


def _valid_url(url):
    parsed = urlsplit(url)
    return (
        parsed.scheme.lower() == "https"
        and parsed.username is None
        and parsed.password is None
        and parsed.hostname
    )


def _extract_title(html):
    patterns = (
        r"<h1[^>]*>\s*(.*?)\s*</h1>",
        r"<title[^>]*>\s*(.*?)\s*</title>",
    )
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        title = re.sub(r"<[^>]+>", "", match.group(1))
        title = unescape(" ".join(title.split())).strip()
        if not title:
            continue
        title = re.sub(r"\s*[:|-]\s*(?:PROSPERO|ORBIS)PATCHES.*$", "", title, flags=re.I)
        if title:
            return title
    return None


def _sony_tmdb_url(title_id):
    seed = f"{title_id}_00".encode("utf-8")
    digest = hmac.new(SONY_TMDB_KEY, seed, hashlib.sha1).hexdigest().upper()
    return f"https://tmdb.np.dl.playstation.net/tmdb2/{title_id}_00_{digest}/{title_id}_00.json"


def _sony_tmdb(title_id):
    try:
        response = requests.get(
            _sony_tmdb_url(title_id),
            headers={"Accept": "application/json", "User-Agent": "FPKGi-Catalog-Hub/1.0"},
            timeout=TIMEOUT,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else None
    except requests.RequestException as exc:
        print(f"  WARNING: Sony TMDB lookup failed for {title_id}: {exc}")
        return None


def resolve_title(title_id):
    if not isinstance(title_id, str):
        return None

    title_id = title_id.strip().upper()
    if not re.fullmatch(r"(?:CUSA|PPSA)\d{5}", title_id):
        return None

    if title_id.startswith("CUSA"):
        data = _sony_tmdb(title_id)
        names = data.get("names") if data else None
        if isinstance(names, list):
            for item in names:
                if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"].strip():
                    return item["name"].strip()
        return None

    config = _load_config()
    key = "ps5_url"
    template = config.get(key)
    if not isinstance(template, str) or not template.strip():
        return None

    url = template.format(title_id=title_id)
    if not _valid_url(url):
        raise ValueError(f"Invalid title database URL: {url}")

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=TIMEOUT,
            allow_redirects=True,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return _extract_title(response.text)
    except requests.RequestException as exc:
        print(f"  WARNING: Title lookup failed for {title_id}: {exc}")
        return None


def _extract_category(html):
    match = re.search(
        r"Category\s*</[^>]+>\s*([^<]+)",
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    return unescape(" ".join(match.group(1).split())).strip() or None


def resolve_category(title_id):
    if not isinstance(title_id, str):
        return None

    title_id = title_id.strip().upper()
    if not re.fullmatch(r"(?:CUSA|PPSA)\d{5}", title_id):
        return None

    if title_id.startswith("CUSA"):
        data = _sony_tmdb(title_id)
        return data.get("category") if data else None

    return None
