import ipaddress
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import requests

from release_sources import fetch_release_entries


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "sources.json"
OUTPUT_DIR = Path(__file__).resolve().parent.parent
TIMEOUT = 60
MIN_CATALOG_RETENTION_RATIO = 0.5
ALLOWED_SOURCE_HOSTS = {"raw.githubusercontent.com"}
ALLOWED_SOURCE_HOST_SUFFIXES = (".archive.org",)


def validate_source_url(url):
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Source URL must be a non-empty string")

    parsed = urlsplit(url.strip())
    if parsed.scheme.lower() != "https":
        raise ValueError("Source URL must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Source URL must not contain embedded credentials")
    if parsed.port is not None:
        raise ValueError("Source URL must not specify a custom port")

    hostname = (parsed.hostname or "").lower().rstrip(".")
    if not hostname:
        raise ValueError("Source URL must contain a hostname")

    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError("Source URL must use an approved public hostname")

    if hostname not in ALLOWED_SOURCE_HOSTS and not any(
        hostname.endswith(suffix) for suffix in ALLOWED_SOURCE_HOST_SUFFIXES
    ):
        raise ValueError(f"Source URL host is not approved: {hostname}")

    return url.strip()


def load_sources():
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        sources = json.load(file)

    if not isinstance(sources, dict) or not sources:
        raise ValueError("Source configuration must be a non-empty JSON object")

    for category, urls in sources.items():
        if not isinstance(category, str) or not category.strip():
            raise ValueError("Source configuration category names must be non-empty strings")
        if not isinstance(urls, list):
            raise ValueError(f"Source configuration category '{category}' must be a list")
        for url in urls:
            try:
                validate_source_url(url)
            except ValueError as exc:
                raise ValueError(
                    f"Source configuration category '{category}' contains an invalid source URL: {exc}"
                ) from exc

    return sources


def fetch_source(url):
    print(f"\nFetching: {url}")

    url = validate_source_url(url)

    response = requests.get(
        url,
        timeout=TIMEOUT,
        headers={"User-Agent": "FPKGi-Catalog-Hub/1.0"},
        allow_redirects=False,
    )

    if 300 <= response.status_code < 400:
        raise ValueError("Source URL returned a redirect; refusing to follow it")
    response.raise_for_status()

    data = response.json()

    if not isinstance(data, dict):
        raise ValueError("Top-level JSON must be an object")

    entries = data.get("DATA")

    if not isinstance(entries, dict):
        raise ValueError('JSON must contain a "DATA" object')

    return entries


ALLOWED_METADATA_TYPES = (str, int, float, bool, type(None))
TITLE_ID_RE = re.compile(r"^[A-Z]{4}\d{5}$", re.IGNORECASE)
VERSION_RE = re.compile(r"^\d+(?:\.\d+)*$")


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
        if not errors:
            warnings.append("missing name")

    title_id = metadata.get("title_id")
    if title_id not in (None, ""):
        if not isinstance(title_id, str) or not TITLE_ID_RE.fullmatch(title_id.strip()):
            errors.append(f"invalid title_id: {title_id!r}")

    size = metadata.get("size")
    if size is not None:
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            errors.append(f"invalid size: {size!r}")

    version = metadata.get("version")
    if version not in (None, ""):
        if not isinstance(version, str) or not VERSION_RE.fullmatch(version.strip()):
            errors.append(f"invalid version: {version!r}")

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
    failed_sources = 0

    print(f"\n{'=' * 60}")
    print(f"{category.upper()}")
    print(f"{'=' * 60}")

    for url in urls:
        try:
            entries = fetch_source(url)
            raw_entry_count = len(entries)
            entries = validate_entries(entries, url)

            if raw_entry_count and not entries:
                raise ValueError(
                    f"{url}: all {raw_entry_count} source entries failed validation"
                )

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
            failed_sources += 1
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

    output_path = OUTPUT_DIR / f"{category}.json"

    # If an upstream source failed and the resulting catalog shrank sharply,
    # preserve the published catalog instead of publishing a partial merge.
    if failed_sources and output_path.exists() and merged:
        try:
            with output_path.open("r", encoding="utf-8") as file:
                existing_output = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Cannot safely inspect existing catalog {output_path}: {exc}"
            ) from exc

        existing_entries = (
            existing_output.get("DATA")
            if isinstance(existing_output, dict)
            else None
        )
        if not isinstance(existing_entries, dict):
            raise RuntimeError(
                f"Cannot safely inspect existing catalog {output_path}: "
                'missing valid "DATA" object'
            )

        if (
            existing_entries
            and len(merged) < len(existing_entries) * MIN_CATALOG_RETENTION_RATIO
        ):
            print(
                f"WARNING: Refusing to publish sharp catalog shrink after "
                f"{failed_sources} source failure(s): "
                f"{len(existing_entries)} -> {len(merged)} entries"
            )
            print(f"Preserved existing catalog: {output_path}")
            return len(existing_entries)

    # If every configured source completed successfully and produced no
    # entries, an empty catalog is intentional and must be publishable.
    # Preserve a non-empty catalog only when at least one source failed.
    if not merged and failed_sources and output_path.exists():
        try:
            with output_path.open("r", encoding="utf-8") as file:
                existing_output = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Cannot safely preserve existing catalog {output_path}: {exc}"
            ) from exc

        existing_entries = (
            existing_output.get("DATA")
            if isinstance(existing_output, dict)
            else None
        )
        if not isinstance(existing_entries, dict):
            raise RuntimeError(
                f"Cannot safely preserve existing catalog {output_path}: "
                'missing valid "DATA" object'
            )

        if existing_entries:
            print(
                f"WARNING: Refusing to replace non-empty catalog with 0 entries: "
                f"{output_path}"
            )
            print(f"Preserved existing entries: {len(existing_entries)}")
            return len(existing_entries)

    output = {
        "DATA": merged
    }

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

        ps5_entries = ps5_release_entries.get(category, {})
        ps5_output_path = OUTPUT_DIR / f"ps5-{category}.json"

        # Never replace an existing non-empty PS5 catalog with an empty
        # release result. This protects published PS5 data from transient
        # GitHub API failures or missing release assets.
        if not ps5_entries and ps5_output_path.exists():
            try:
                with ps5_output_path.open("r", encoding="utf-8") as file:
                    existing_ps5_output = json.load(file)
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"Cannot safely preserve existing PS5 catalog "
                    f"{ps5_output_path}: {exc}"
                ) from exc

            existing_ps5_entries = (
                existing_ps5_output.get("DATA")
                if isinstance(existing_ps5_output, dict)
                else None
            )
            if not isinstance(existing_ps5_entries, dict):
                raise RuntimeError(
                    f"Cannot safely preserve existing PS5 catalog "
                    f"{ps5_output_path}: missing valid \"DATA\" object"
                )

            if existing_ps5_entries:
                print(
                    f"WARNING: Refusing to replace non-empty PS5 catalog "
                    f"with 0 entries: {ps5_output_path}"
                )
                print(
                    f"Preserved existing PS5 entries: "
                    f"{len(existing_ps5_entries)}"
                )
                continue

        ps5_output = {"DATA": ps5_entries}
        with ps5_output_path.open("w", encoding="utf-8") as file:
            json.dump(ps5_output, file, indent=2, ensure_ascii=False)
            file.write("\n")
        print(
            f"PS5 catalog: {ps5_output_path} | "
            f"Entries: {len(ps5_output['DATA'])}"
        )

    ps5_output_path = OUTPUT_DIR / "ps5.json"
    ps5_entries = {}
    for category_entries in ps5_release_entries.values():
        for pkg_url, metadata in category_entries.items():
            ps5_entries.setdefault(pkg_url, metadata)

    if not ps5_entries and ps5_output_path.exists():
        try:
            with ps5_output_path.open("r", encoding="utf-8") as file:
                existing_ps5_output = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Cannot safely preserve existing PS5 catalog "
                f"{ps5_output_path}: {exc}"
            ) from exc
        existing_ps5_entries = (
            existing_ps5_output.get("DATA")
            if isinstance(existing_ps5_output, dict)
            else None
        )
        if not isinstance(existing_ps5_entries, dict):
            raise RuntimeError(
                f"Cannot safely preserve existing PS5 catalog "
                f"{ps5_output_path}: missing valid \"DATA\" object"
            )
        if existing_ps5_entries:
            print(
                f"WARNING: Refusing to replace non-empty unified PS5 catalog "
                f"with 0 entries: {ps5_output_path}"
            )
            print(f"Preserved existing PS5 entries: {len(existing_ps5_entries)}")
            print("\nMerge completed.")
            return

    with ps5_output_path.open("w", encoding="utf-8") as file:
        json.dump({"DATA": ps5_entries}, file, indent=2, ensure_ascii=False)
        file.write("\n")
    print(f"Unified PS5 catalog: {ps5_output_path} | Entries: {len(ps5_entries)}")

    print("\nMerge completed.")


if __name__ == "__main__":
    main()
