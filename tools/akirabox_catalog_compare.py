#!/usr/bin/env python3
"""Read-only comparison of an AkiraBox scan with the repository catalogs."""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

CATALOGS = {
    ("PS4", "games"): Path("ps4/games.json"),
    ("PS4", "dlc"): Path("ps4/dlc.json"),
    ("PS5", "games"): Path("ps5/ps5-games.json"),
    ("PS5", "dlc"): Path("ps5/ps5-dlc.json"),
}
import requests
from urllib.parse import urljoin, urlparse

ARCHIVE_HOST = "archive.org"
LINK_TIMEOUT_SECONDS = 15
MAX_LINK_REDIRECTS = 5


def _is_archive_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        # Backslashes can terminate the authority when urllib3 prepares a URL,
        # so reject them before checking the hostname suffix.
        if "\\" in parsed.netloc:
            return False
        host = (parsed.hostname or "").lower()
        return (
            parsed.scheme == "https"
            and (host == ARCHIVE_HOST or host.endswith("." + ARCHIVE_HOST))
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        # urlparse raises for malformed authorities (for example, bad IPv6).
        return False


def check_catalog_url(url: str, session: Any = None) -> dict[str, Any]:
    """Check an Internet Archive URL without downloading package contents."""
    client = session or requests.Session()
    if not _is_archive_url(url):
        return {"status": "inconclusive", "http_status": None,
                "reason": "Unsupported catalog URL host or scheme"}

    def request(method: str):
        current = url
        for _ in range(MAX_LINK_REDIRECTS + 1):
            if not _is_archive_url(current):
                return None, "Redirect left permitted Internet Archive hosts"
            try:
                # Prepare and send through the adapter directly. Session.send()
                # resolves redirect metadata even with allow_redirects=False;
                # that path can consume a redirect response body.
                prepared = client.prepare_request(
                    requests.Request(
                        method, current,
                        headers={"Range": "bytes=0-0"} if method == "GET" else {},
                    )
                )
                if not _is_archive_url(prepared.url):
                    return None, "Prepared request left permitted Internet Archive hosts"
                settings = client.merge_environment_settings(
                    prepared.url, {}, True, None, None
                )
                adapter = client.get_adapter(prepared.url)
                response = adapter.send(
                    prepared, timeout=LINK_TIMEOUT_SECONDS, **settings
                )
            except (requests.RequestException, ValueError) as exc:
                return None, f"Link check failed: {type(exc).__name__}"
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")
                response.close()
                if not location:
                    return None, "Redirect response had no Location header"
                try:
                    current = urljoin(current, location)
                except ValueError as exc:
                    return None, f"Malformed redirect URL: {type(exc).__name__}"
                if not _is_archive_url(current):
                    return None, "Redirect left permitted Internet Archive hosts"
                continue
            return response, None
        return None, "Too many redirects"

    response, error = request("HEAD")
    if error or response is None:
        return {"status": "inconclusive", "http_status": None,
                "reason": error or "No response"}
    status_code = response.status_code
    response.close()

    if status_code in {405, 501}:
        response, error = request("GET")
        if error or response is None:
            return {"status": "inconclusive", "http_status": None,
                    "reason": error or "No response to Range GET"}
        status_code = response.status_code
        response.close()

    if status_code in {404, 410}:
        return {"status": "broken", "http_status": status_code,
                "reason": f"Catalog URL returned HTTP {status_code}"}
    if 200 <= status_code < 400:
        return {"status": "reachable", "http_status": status_code,
                "reason": f"Catalog URL responded with HTTP {status_code}"}
    return {"status": "inconclusive", "http_status": status_code,
            "reason": f"Catalog URL check was inconclusive (HTTP {status_code})"}

REGIONS = {"US": "USA", "USA": "USA", "EU": "EUR", "EUR": "EUR",
           "JP": "JAP", "JAP": "JAP", "ASIA": "ASIA"}


def normalize_title(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = re.sub(r"\.(pkg|zip)$", "", value.strip(), flags=re.I)
    value = re.sub(r"\s*-?\s*\[[^]]+\]", " ", value)
    value = value.replace("™", " ").replace("®", " ").replace("©", " ")
    value = unicodedata.normalize("NFKD", value).casefold()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in value).split())


LANGUAGE_CODES = {
    "AR", "BG", "CS", "DA", "DE", "EL", "EN", "ES", "FI", "FR", "HE",
    "HR", "HU", "ID", "IT", "JA", "KO", "NL", "NO", "PL", "PT", "RO",
    "RU", "SK", "SV", "TH", "TR", "UK", "VI", "ZH",
}


def parse_source_metadata(name: str) -> dict[str, Any]:
    """Parse identity plus language and minimum-firmware tags when filenames expose them."""
    stem = re.sub(r"\.(pkg|zip)$", "", name, flags=re.I).strip()
    # AkiraBox appends duplicate-copy counters after the final bracketed tag.
    stem = re.sub(r"(?<=\])\s*\(\d+\)$", "", stem)
    title_id_match = re.search(r"(?<![A-Z0-9])((?:CUSA|PPSA)\d{5})(?!\d)", stem, re.I)
    title_id = title_id_match.group(1).upper() if title_id_match else None

    tags = re.findall(r"\[\s*([^]]+?)\s*\]", stem)
    region_match = re.search(r"\[\s*(US|USA|EU|EUR|JP|JAP|ASIA)\s*\]", stem, re.I)
    region = REGIONS.get(region_match.group(1).upper()) if region_match else None
    languages = sorted({
        token.upper() for tag in tags
        for token in re.split(r"[+,/\s]+", tag.upper())
        if token in LANGUAGE_CODES
    })

    # An explicit v-prefixed version disambiguates a following numeric firmware tag.
    version_match = re.search(r"(?:^|[_\s-])v(\d+(?:\.\d+)+)(?=$|[_\s-])", stem, re.I)
    numeric_tags = [tag.strip() for tag in tags if re.fullmatch(r"\d+(?:\.\d+)+", tag.strip())]
    if version_match:
        version = version_match.group(1)
        firmware_tags = [tag for tag in numeric_tags if tag != version]
        min_fw = firmware_tags[-1] if firmware_tags else None
    else:
        fallback = re.search(r"\[\s*(\d+(?:\.\d+)+)\s*\]", stem)
        version = fallback.group(1) if fallback else None
        min_fw = None

    if title_id_match:
        title = stem[:title_id_match.start()]
        title = re.sub(r"^\s*\[[^]]+\]\s*[-_ ]*", "", title)
        title = title.strip(" -_")
    else:
        title = re.sub(r"\s*-?\s*\[[^]]+\]", " ", stem)

    return {
        "title": normalize_title(title),
        "region": region,
        "version": version,
        "title_id": title_id,
        "languages": languages,
        "min_fw": min_fw,
    }


def parse_source_details(name: str) -> tuple[str, str | None, str | None, str | None]:
    """Backward-compatible four-field identity parser."""
    parsed = parse_source_metadata(name)
    return parsed["title"], parsed["region"], parsed["version"], parsed["title_id"]


def _catalog_filename_metadata(url: str) -> dict[str, Any]:
    """Parse filename tags from the catalog URL without fetching the package."""
    from urllib.parse import unquote, urlparse

    filename = unquote(urlparse(url).path.rsplit("/", 1)[-1])
    return parse_source_metadata(filename)


def parse_source_identity(name: str) -> tuple[str, str | None, str | None]:
    """Backward-compatible three-field identity parser."""
    title, region, version, _ = parse_source_details(name)
    return title, region, version


def _catalog_identity(metadata: dict[str, Any]) -> tuple[str, str | None]:
    title = normalize_title(metadata.get("name"))
    raw_region = metadata.get("region")
    region = REGIONS.get(str(raw_region).upper()) if raw_region is not None else None
    return title, region


def _title_similarity(left: str, right: str) -> float:
    """Compare package-name tokens, preserving meaningful pack/volume numbers."""
    left_tokens, right_tokens = set(left.split()), set(right.split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _load_catalog(root: Path, relative: Path) -> dict[str, dict[str, Any]]:
    path = root / relative
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load catalog {path}: {exc}") from exc
    data = payload.get("DATA") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise ValueError(f"Catalog {path} must contain a DATA object")
    return data


def compare_scan(scan: dict[str, Any], catalog_root: Path, link_checker=None) -> dict[str, Any]:
    catalogs = {key: _load_catalog(catalog_root, path) for key, path in CATALOGS.items()}
    indexes: dict[tuple[str, str], dict[tuple[str, str | None], list[tuple[str, dict[str, Any]]]]] = {}
    title_id_indexes: dict[tuple[str, str], dict[str, list[tuple[str, dict[str, Any]]]]] = {}
    for (platform, category), entries in catalogs.items():
        index: dict[tuple[str, str | None], list[tuple[str, dict[str, Any]]]] = {}
        title_id_index: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for url, metadata in entries.items():
            if isinstance(metadata, dict):
                identity = _catalog_identity(metadata)
                if identity[0]:
                    index.setdefault(identity, []).append((url, metadata))
                title_id = metadata.get("title_id")
                if isinstance(title_id, str) and title_id.strip():
                    title_id_index.setdefault(title_id.strip().upper(), []).append((url, metadata))
        indexes[(platform, category)] = index
        title_id_indexes[(platform, category)] = title_id_index

    results = []
    source_seen: dict[tuple[Any, ...], str] = {}
    source_catalog_urls: dict[tuple[str, str], set[str]] = {}
    counts = {"new": 0, "matched": 0, "outdated": 0, "duplicate": 0, "unclassified": 0}
    for item in scan.get("files", []):
        platform, category = item.get("platform"), item.get("catalog_category")
        if (platform, category) not in indexes or not isinstance(item.get("name"), str):
            counts["unclassified"] += 1
            results.append({"status": "unclassified", "file_id": item.get("id"),
                            "name": item.get("name"), "reason": "Missing supported platform/category"})
            continue

        source_metadata = parse_source_metadata(item["name"])
        title = source_metadata["title"]
        region = source_metadata["region"]
        source_version = source_metadata["version"]
        source_title_id = source_metadata["title_id"]
        if not title:
            counts["unclassified"] += 1
            results.append({"status": "unclassified", "file_id": item.get("id"),
                            "name": item.get("name"), "reason": "Could not normalize filename"})
            continue
        index = indexes[(platform, category)]
        title_id_index = title_id_indexes[(platform, category)]
        # Different title IDs are different products even when their visible
        # names, regions, and versions happen to be identical.
        source_size = item.get("size_bytes")
        source_size = source_size if isinstance(source_size, int) else None
        identity_key = (
            platform, category, title, region, source_version, source_title_id,
            tuple(source_metadata["languages"]), source_metadata["min_fw"], source_size,
        )
        if identity_key in source_seen:
            counts["duplicate"] += 1
            results.append({"status": "duplicate", "file_id": item.get("id"), "name": item.get("name"),
                            "duplicate_of_file_id": source_seen[identity_key],
                            "reason": "Duplicate AkiraBox title/region/version already seen in this scan"})
            continue
        source_seen[identity_key] = str(item.get("id"))
        id_candidates = title_id_index.get(source_title_id, []) if source_title_id else []
        exact = index.get((title, region), [])
        # A title ID identifies a base application, not necessarily one DLC
        # package. Always verify the package name for DLC, even when the catalog
        # currently contains only one entry for that title ID.
        ambiguous_title_id = False
        if category == "dlc" and id_candidates:
            scored = []
            for entry in id_candidates:
                _, metadata = entry
                scored.append((_title_similarity(title, normalize_title(metadata.get("name"))), entry))
            scored.sort(key=lambda pair: pair[0], reverse=True)
            if scored and scored[0][0] >= 0.80 and (
                len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.08
            ):
                candidates = [scored[0][1]]
            else:
                candidates = []
                ambiguous_title_id = True
        elif len(id_candidates) > 1:
            scored = []
            for entry in id_candidates:
                _, metadata = entry
                scored.append((_title_similarity(title, normalize_title(metadata.get("name"))), entry))
            scored.sort(key=lambda pair: pair[0], reverse=True)
            if scored and scored[0][0] >= 0.80 and (
                len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.08
            ):
                candidates = [scored[0][1]]
            else:
                candidates = []
                ambiguous_title_id = True
        elif id_candidates:
            candidates = id_candidates
        else:
            # If the source filename has no region, only accept a unique title match.
            candidates = exact if region else [
                entry for identity, entries in index.items() if identity[0] == title for entry in entries
            ]
        if len(candidates) == 1:
            source_catalog_urls.setdefault((platform, category), set()).add(candidates[0][0])
        proposed_replacement = None
        if ambiguous_title_id:
            status, reason = "unclassified", "Title ID match could not be verified against a unique package name"
        elif len(candidates) > 1:
            status, reason = "duplicate", "Multiple catalog entries match the available title identity"
        elif not candidates:
            status, reason = "new", "No unique matching title ID or title/region in the target catalog"
        else:
            catalog_url, metadata = candidates[0]
            catalog_filename = _catalog_filename_metadata(catalog_url)
            old_version = str(metadata.get("version") or "").lstrip("0")
            new_version = str(source_version or "").lstrip("0")
            old_size, new_size = metadata.get("size"), item.get("size_bytes")
            differences = []
            metadata_differences = []

            def add_metadata_difference(field: str, source_value: Any, catalog_value: Any) -> None:
                if source_value is None or catalog_value is None:
                    return
                if field == "version":
                    differs = str(source_value).lstrip("0") != str(catalog_value).lstrip("0")
                elif field == "title_id":
                    differs = str(source_value).upper() != str(catalog_value).upper()
                else:
                    differs = source_value != catalog_value
                if differs:
                    metadata_differences.append({
                        "field": field, "source_value": source_value, "catalog_value": catalog_value,
                    })

            if source_version and old_version and new_version != old_version:
                differences.append("version differs")
            if isinstance(new_size, int) and isinstance(old_size, int) and new_size != old_size:
                differences.append("file size differs")
            add_metadata_difference("version", source_version, metadata.get("version"))
            add_metadata_difference("region", region, REGIONS.get(str(metadata.get("region") or "").upper()))
            add_metadata_difference("title_id", source_title_id, metadata.get("title_id"))
            add_metadata_difference("language", source_metadata["languages"] or None,
                                    catalog_filename["languages"] or None)
            add_metadata_difference("min_fw", source_metadata["min_fw"], metadata.get("min_fw"))
            add_metadata_difference("size_bytes", new_size, old_size)

            # Archive.org is the source being replaced, not a URL whose
            # availability needs to be probed. A unique identity match is
            # enough to propose the AkiraBox share URL in this read-only report.
            archive_catalog_url = _is_archive_url(catalog_url)
            share_url = item.get("share_url")
            valid_share_url = False
            if isinstance(share_url, str):
                try:
                    parsed_share = urlparse(share_url)
                    valid_share_url = (
                        parsed_share.scheme == "https"
                        and (parsed_share.hostname or "").lower() in {"akirabox.to", "akirabox.com"}
                    )
                except ValueError:
                    valid_share_url = False
            # Only flag an entry when a concrete AkiraBox destination exists.
            archive_replacement = archive_catalog_url and valid_share_url
            if archive_replacement:
                proposed_replacement = {
                    "from": catalog_url,
                    "to": share_url,
                    "status": "pending_review",
                }
            if differences or archive_replacement:
                status = "outdated"
                reasons = list(differences)
                if archive_replacement:
                    reasons.append("Archive.org catalog URL should be replaced with the AkiraBox share URL")
                reason = ", ".join(reasons)
            elif not source_version or not old_version or not isinstance(new_size, int) or not isinstance(old_size, int):
                status, reason = "unclassified", "Matched catalog entry, but version or file-size metadata is missing"
            else:
                status, reason = "matched", "Metadata agrees; catalog URL does not require Archive.org replacement"
        counts[status] += 1
        results.append({
            "status": status, "file_id": item.get("id"), "name": item.get("name"),
            "platform": platform, "category": category, "region": region,
            "version": source_version, "title_id": source_title_id,
            "metadata_differences": metadata_differences if candidates and len(candidates) == 1 else [],
            "source_metadata": {
                **source_metadata,
                "size_bytes": source_size,
                "file_name": item.get("name"),
                "platform": platform,
                "category": category,
            },
            "catalog_metadata": ({
                **metadata,
                "size_bytes": metadata.get("size"),
                "parsed_filename_metadata": catalog_filename,
            } if candidates and len(candidates) == 1 else None),
            "catalog_url": catalog_url if candidates and len(candidates) == 1 else None,
            "proposed_replacement": proposed_replacement,
            "share_url": item.get("share_url"), "reason": reason,
        })

    expected = {(platform, category) for platform in ("PS4", "PS5") for category in ("games", "dlc")}
    discovered = set()
    for folder in scan.get("folders", []):
        path = folder.get("path", [])
        if len(path) >= 2 and str(path[1]).casefold() in {"games", "dlc"}:
            discovered.add((str(path[0]).upper(), str(path[1]).casefold()))
    complete = not scan.get("errors") and expected.issubset(discovered)
    missing_candidates = []
    if complete:
        for (platform, category), entries in catalogs.items():
            seen_urls = source_catalog_urls.get((platform, category), set())
            for url, metadata in entries.items():
                if not isinstance(metadata, dict):
                    continue
                if url not in seen_urls:
                    missing_candidates.append({"platform": platform, "category": category,
                                               "name": metadata.get("name"), "region": metadata.get("region"),
                                               "catalog_url": url, "status": "not_seen_in_source"})
    return {
        "scanner": "akirabox_catalog_comparison",
        "read_only": True,
        "catalog_write_performed": False,
        "scan_complete_for_all_categories": complete,
        "missing_check_deferred": not complete,
        "counts": counts,
        "replacement_candidate_count": sum(1 for result in results if result.get("proposed_replacement")),
        "results": results,
        "missing_candidate_count": len(missing_candidates),
        "missing_catalog_entries": missing_candidates,
        "note": ("Missing candidates are only flagged for review; nothing is deleted."
                 if complete else
                 "At least one of PS4/Games, PS4/DLC, PS5/Games, PS5/DLC is not confirmed. "
                 "Missing catalog entries are intentionally not inferred from this partial scan."),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan-report", type=Path, required=True)
    parser.add_argument("--catalog-root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        scan = json.loads(args.scan_report.read_text(encoding="utf-8"))
        report = compare_scan(scan, args.catalog_root)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
