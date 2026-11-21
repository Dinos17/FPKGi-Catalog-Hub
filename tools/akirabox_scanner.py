#!/usr/bin/env python3
"""Read-only AkiraBox public-folder metadata scanner.

Only explicitly configured, authorized source folders are scanned. The tool
never downloads package binaries, authenticates, resolves download URLs, or
modifies catalog files.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests


DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "akirabox_sources.json"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
ALLOWED_CATEGORIES = {"homebrew", "apps", "emulators", "themes", "demos", "catalog"}
AKIRA_HOSTS = {"akirabox.to", "akirabox.com"}
MAX_REDIRECTS = 5


class ScanError(RuntimeError):
    """Raised when a public folder cannot be read safely."""


@dataclass
class ScanResult:
    folders: list[dict[str, Any]]
    files: list[dict[str, Any]]
    errors: list[dict[str, str]]


def _validate_folder_url(url: str) -> str:
    if not isinstance(url, str):
        raise ScanError("Folder URL must be a string")
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in AKIRA_HOSTS:
        raise ScanError(f"Unsupported AkiraBox folder URL: {url}")
    if not re.fullmatch(r"/[A-Za-z0-9]+/folder/?", parsed.path):
        raise ScanError(f"Not a recognized public folder URL: {url}")
    return url.rstrip("/")


def _next_flight_text(html: str) -> str:
    """Decode Next.js Flight string chunks embedded in script tags."""
    chunks: list[str] = []
    pattern = re.compile(r'self\.__next_f\.push\(\[1,("(?:\\.|[^"\\])*")\]\)')
    for match in pattern.finditer(html):
        try:
            value = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(value, str):
            chunks.append(value)
    if not chunks:
        raise ScanError("Could not find embedded Next.js folder data")
    return "\n".join(chunks)


def _json_objects(text: str):
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value


def _extract_page_data(html: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    flight = _next_flight_text(html)
    folder = next(
        (obj for obj in _json_objects(flight)
         if isinstance(obj.get("id"), str)
         and isinstance(obj.get("name"), str)
         and "isPrivate" in obj),
        None,
    )
    listing = next(
        (obj for obj in _json_objects(flight)
         if isinstance(obj.get("items"), list)
         and isinstance(obj.get("folderId"), (str, int))),
        None,
    )
    pager = next(
        (obj for obj in _json_objects(flight)
         if {"total", "page", "perPage", "sort", "order"}.issubset(obj)
         and isinstance(obj.get("total"), int)),
        None,
    )
    if folder is None or listing is None or pager is None:
        raise ScanError("Folder metadata, item list, or pagination data is missing")
    if folder.get("isPrivate") is not False:
        raise ScanError(f"Folder is private or its public status is unknown: {folder.get('name')}")
    if (not isinstance(pager.get("page"), int)
            or not isinstance(pager.get("perPage"), int)
            or not isinstance(pager.get("total"), int)
            or pager["page"] < 1 or pager["perPage"] < 1
            or pager["total"] < 0 or pager["page"] > 100000):
        raise ScanError("Invalid pagination metadata")
    return folder, listing, pager


def _classify_catalog_path(path: list[str]) -> tuple[str | None, str | None]:
    """Classify paths such as PS4/Games or PS5/DLC, including nested folders."""
    normalized = [part.strip().casefold() for part in path]
    for index, part in enumerate(normalized):
        if part not in {"ps4", "ps5"}:
            continue
        for candidate in normalized[index + 1:]:
            if candidate in {"games", "dlc"}:
                return part.upper(), candidate
    return None, None


class AkiraBoxScanner:
    def __init__(self, session: requests.Session | None = None, timeout: int = 30,
                 max_pages: int = 100, max_folders: int = 500):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.timeout = timeout
        self.max_pages = max_pages
        self.max_folders = max_folders

    def _get_html(self, url: str) -> str:
        current_url = url
        for redirect_count in range(MAX_REDIRECTS + 1):
            try:
                response = self.session.get(
                    current_url, timeout=self.timeout, allow_redirects=False
                )
            except requests.RequestException as exc:
                raise ScanError(f"Request failed for {current_url}: {exc}") from exc

            status = getattr(response, "status_code", 200)
            if status in {301, 302, 303, 307, 308}:
                location = getattr(response, "headers", {}).get("Location")
                if not location:
                    raise ScanError(f"Redirect from {current_url} did not include a Location header")
                target = urljoin(current_url, location)
                try:
                    parsed = urlparse(target)
                    safe_target = (
                        parsed.scheme == "https"
                        and (parsed.hostname or "").lower() in AKIRA_HOSTS
                        and parsed.username is None
                        and parsed.password is None
                        and parsed.port in (None, 443)
                    )
                except ValueError:
                    safe_target = False
                if not safe_target:
                    raise ScanError(f"Refusing redirect to a non-allowlisted URL: {target}")
                if redirect_count >= MAX_REDIRECTS:
                    raise ScanError(f"Too many redirects while requesting {url}")
                current_url = target
                continue

            try:
                response.raise_for_status()
            except requests.RequestException as exc:
                raise ScanError(f"Request failed for {current_url}: {exc}") from exc
            return response.text

        raise ScanError(f"Too many redirects while requesting {url}")

    def _scan_folder(self, folder_url: str, category: str, path: list[str],
                     visited: set[str], result: ScanResult, platform: str | None = None,
                     inherited_catalog_category: str | None = None,
                     platform_root: bool = False) -> None:
        folder_url = _validate_folder_url(folder_url)
        folder_id = folder_url.split("/")[-2]
        if folder_id in visited:
            return
        if len(visited) >= self.max_folders:
            raise ScanError(f"Folder safety limit reached ({self.max_folders})")
        visited.add(folder_id)

        page = 1
        folder_name = path[-1] if path else folder_id
        folder_record: dict[str, Any] | None = None

        while True:
            if page > self.max_pages:
                raise ScanError(f"Pagination safety limit reached in folder {folder_id}")
            page_url = folder_url if page == 1 else f"{folder_url}?page={page}"
            html = self._get_html(page_url)
            folder, listing, pager = _extract_page_data(html)
            if folder["id"] != folder_id:
                raise ScanError(f"Folder ID mismatch while reading {folder_url}")
            if pager["page"] != page:
                raise ScanError(f"Requested page {page}, but AkiraBox returned page {pager['page']}")
            folder_name = folder["name"]
            current_path = path if platform_root else path + [folder_name]
            detected_platform, detected_category = _classify_catalog_path(current_path)
            current_platform = platform or detected_platform
            current_category = inherited_catalog_category or detected_category
            if folder_record is None:
                folder_record = {
                    "id": folder_id,
                    "name": folder_name,
                    "url": folder_url,
                    "path": current_path,
                    "category": (current_category or "unclassified") if category == "catalog" else category,
                    "platform": current_platform,
                    "catalog_category": current_category,
                }
                result.folders.append(folder_record)

            items = listing["items"]
            for item in items:
                if not isinstance(item, dict):
                    continue
                kind = item.get("kind")
                name = item.get("name")
                item_id = item.get("id")
                if not isinstance(name, str) or not isinstance(item_id, str):
                    continue
                if kind == "folder":
                    child_url = f"https://akirabox.to/{item_id}/folder"
                    child_catalog_category = current_category
                    if platform_root:
                        child_name = name.strip().casefold()
                        if child_name not in {"games", "dlc"}:
                            continue
                        child_catalog_category = child_name
                    self._scan_folder(
                        child_url, category, current_path, visited, result,
                        platform=current_platform,
                        inherited_catalog_category=child_catalog_category,
                    )
                elif kind == "file":
                    if item.get("visibility") != "public":
                        continue
                    if any(existing["id"] == item_id for existing in result.files):
                        continue
                    extension = item.get("extension")
                    file_path = current_path
                    detected_platform, detected_category = _classify_catalog_path(file_path)
                    file_platform = platform or detected_platform
                    file_catalog_category = inherited_catalog_category or detected_category
                    output_category = (
                        file_catalog_category or "unclassified"
                        if category == "catalog" else category
                    )
                    result.files.append({
                        "id": item_id,
                        "numeric_id": item.get("numericId"),
                        "name": name,
                        "extension": extension.lower() if isinstance(extension, str) else None,
                        "size_bytes": item.get("size") if isinstance(item.get("size"), int) else None,
                        "visibility": item.get("visibility"),
                        "created_at": item.get("createdAt"),
                        "parent_id": item.get("parentId"),
                        "share_url": f"https://akirabox.to/{item_id}/file",
                        "folder_path": file_path,
                        "category": output_category,
                        "platform": file_platform,
                        "catalog_category": file_catalog_category,
                    })

            total_pages = max(1, (pager["total"] + pager["perPage"] - 1) // pager["perPage"])
            if page >= total_pages:
                break
            page += 1

    def scan(self, sources: list[dict[str, str]]) -> ScanResult:
        result = ScanResult(folders=[], files=[], errors=[])
        visited: set[str] = set()
        for source in sources:
            if not isinstance(source, dict):
                result.errors.append({"source": "invalid source", "error": "Each source must be a JSON object"})
                continue
            label = source.get("label", "unnamed source")
            raw_category = source.get("category", "")
            category = raw_category.lower() if isinstance(raw_category, str) else ""
            try:
                url = _validate_folder_url(source["url"])
                raw_platform = source.get("platform")
                if raw_platform is not None:
                    platform = raw_platform.upper() if isinstance(raw_platform, str) else ""
                    if platform not in {"PS4", "PS5"}:
                        raise ScanError("Platform sources must specify 'PS4' or 'PS5'")
                    self._scan_folder(
                        url, "catalog", [platform], visited, result,
                        platform=platform, platform_root=True,
                    )
                else:
                    if category not in ALLOWED_CATEGORIES:
                        raise ScanError(
                            f"Category '{category}' is not enabled. Allowed: {', '.join(sorted(ALLOWED_CATEGORIES))}"
                        )
                    self._scan_folder(url, category, [], visited, result)
            except (KeyError, ScanError) as exc:
                result.errors.append({"source": label, "error": str(exc)})
        return result


def load_sources(config_path: Path) -> list[dict[str, str]]:
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScanError(f"Cannot read source configuration {config_path}: {exc}") from exc
    sources = config.get("sources") if isinstance(config, dict) else None
    if not isinstance(sources, list):
        raise ScanError("Configuration must contain a 'sources' array")
    return sources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, help="Write JSON report to this path (default: stdout)")
    args = parser.parse_args(argv)

    try:
        sources = load_sources(args.config)
        result = AkiraBoxScanner().scan(sources)
    except ScanError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    report = {
        "scanner": "akirabox",
        "read_only": True,
        "folder_count": len(result.folders),
        "file_count": len(result.files),
        "folders": result.folders,
        "files": result.files,
        "errors": result.errors,
    }
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
