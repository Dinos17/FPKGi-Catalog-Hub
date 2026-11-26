import ipaddress
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import requests

from catalog_names import validate_category_name
from release_sources import fetch_release_entries
from external_database import fetch_external_database_entries


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "sources.json"
EXCLUSIONS_PATH = Path(__file__).resolve().parent.parent / "config" / "exclusions.json"
OUTPUT_DIR = Path(__file__).resolve().parent.parent
def catalog_output_path(category, ps5=False):
    directory = OUTPUT_DIR / ("ps5" if ps5 else "ps4")
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{category}.json"

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
        try:
            validate_category_name(category)
        except ValueError as exc:
            raise ValueError(f"Invalid source configuration category {category!r}: {exc}") from exc
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


def load_exclusions():
    if not EXCLUSIONS_PATH.exists():
        return {}

    with EXCLUSIONS_PATH.open("r", encoding="utf-8") as file:
        exclusions = json.load(file)

    if not isinstance(exclusions, dict):
        raise ValueError("Exclusion configuration must be a JSON object")

    for category, urls in exclusions.items():
        try:
            validate_category_name(category)
        except ValueError as exc:
            raise ValueError(
                f"Invalid exclusion configuration category {category!r}: {exc}"
            ) from exc
        if not isinstance(urls, list):
            raise ValueError(
                f"Exclusion configuration category '{category}' must be a list"
            )
        for url in urls:
            if not isinstance(url, str) or not url.strip():
                raise ValueError(
                    f"Exclusion configuration category '{category}' "
                    "contains an invalid package URL"
                )

    return {category: set(urls) for category, urls in exclusions.items()}


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
MOCHA_FILE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
DEEPSTORAGE_HOST = "share.deepstorage.dev"


def validate_entry(pkg_url, metadata, allow_mocha=False, allow_deepstorage=False):
    errors = []
    warnings = []

    if not isinstance(pkg_url, str) or not pkg_url.strip():
        errors.append("package URL is missing or is not a string")
    elif isinstance(metadata, dict) and metadata.get("download_source") == "mocha":
        if not allow_mocha:
            errors.append("Mocha entries are not accepted from fetched sources")
        elif not MOCHA_FILE_ID_RE.fullmatch(pkg_url):
            errors.append("Mocha file ID is invalid")
    elif isinstance(metadata, dict) and metadata.get("download_source") == "deepstorage":
        if not allow_deepstorage:
            errors.append("DeepStorage entries are not accepted from fetched sources")
        else:
            try:
                parsed = urlsplit(pkg_url)
                valid_url = (
                    parsed.scheme.lower() == "https"
                    and parsed.hostname is not None
                    and parsed.hostname.lower().rstrip(".") == DEEPSTORAGE_HOST
                    and parsed.username is None
                    and parsed.password is None
                    and parsed.port in (None, 443)
                    and parsed.path.lower().endswith(".pkg")
                    and not parsed.query
                    and not parsed.fragment
                )
            except ValueError:
                valid_url = False
            if not valid_url:
                errors.append("DeepStorage package URL is invalid")
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


def is_ps5_entry(metadata):
    """Return True when metadata identifies a PS5 package."""
    title_id = metadata.get("title_id") if isinstance(metadata, dict) else None
    return isinstance(title_id, str) and title_id.upper().startswith("PPSA")


def merge_category(category, urls, release_entries, excluded_urls=None):
    merged = {}
    excluded_urls = excluded_urls or set()
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
                if pkg_url in excluded_urls:
                    print(f"Excluded package registration: {pkg_url}")
                    continue
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
        if is_ps5_entry(metadata):
            continue
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

    output_path = catalog_output_path(category)
    fresh_entry_count = len(merged)

    # If every upstream source failed and no fresh records were obtained,
    # keep the entire existing catalog. Provider-only merged data must not
    # make a failed ingestion look like a successful refresh.
    if failed_sources and fresh_entry_count == 0 and output_path.exists():
        try:
            with output_path.open("r", encoding="utf-8") as file:
                existing_output = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Cannot safely preserve existing catalog {output_path}: {exc}"
            ) from exc
        existing_entries = (
            existing_output.get("DATA") if isinstance(existing_output, dict) else None
        )
        if not isinstance(existing_entries, dict):
            raise RuntimeError(
                f"Cannot safely preserve existing catalog {output_path}: "
                'missing valid "DATA" object'
            )
        print(
            f"WARNING: All source ingestion failed without fresh entries; "
            f"preserving the complete existing catalog ({len(existing_entries)} entries)."
        )
        return len(existing_entries)

    # Preserve provider-registered entries across ordinary rebuilds. These
    # entries may only originate from the guarded provider registration flows,
    # never from fetched external catalog sources.
    if output_path.exists():
        try:
            with output_path.open("r", encoding="utf-8") as file:
                existing_output = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Cannot safely preserve provider registrations in {output_path}: {exc}"
            ) from exc
        existing_entries = (
            existing_output.get("DATA") if isinstance(existing_output, dict) else None
        )
        if not isinstance(existing_entries, dict):
            raise RuntimeError(
                f"Cannot safely preserve provider registrations in {output_path}: "
                'missing valid "DATA" object'
            )
        for entry_key, metadata in existing_entries.items():
            if not isinstance(metadata, dict):
                continue
            provider = metadata.get("download_source")
            if provider not in {"mocha", "deepstorage"}:
                continue
            if provider == "deepstorage":
                errors, _ = validate_entry(
                    entry_key, metadata, allow_deepstorage=True
                )
                if errors:
                    raise RuntimeError(
                        f"Refusing to preserve invalid DeepStorage entry "
                        f"{entry_key}: {'; '.join(errors)}"
                    )
            if entry_key in merged and merged[entry_key] != metadata:
                raise RuntimeError(
                    f"{provider} catalog key conflicts with a fetched catalog key: "
                    f"{entry_key}"
                )
            merged[entry_key] = metadata

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
    exclusions = load_exclusions()
    release_entries, ps5_release_entries = fetch_release_entries()

    try:
        dataset_entries = fetch_external_database_entries()
    except Exception as exc:
        print(f"WARNING: External package database unavailable: {exc}")
        print("Continuing without external database entries.")
        dataset_entries = {}

    def external_entries_for_category(category):
        return {
            pkg_url: metadata
            for pkg_url, metadata in dataset_entries.items()
            if metadata.get("category") == category
            and metadata.get("download_source") != "deepstorage"
        }

    for category, urls in sources.items():
        merge_category(
            category,
            urls,
            {
                pkg_url: metadata
                for pkg_url, metadata in {
                    **release_entries.get(category, {}),
                    **external_entries_for_category(category),
                }.items()
                if not is_ps5_entry(metadata)
            },
            exclusions.get(category, set()),
        )

        ps5_entries = {
            pkg_url: metadata
            for pkg_url, metadata in {
                **ps5_release_entries.get(category, {}),
                **external_entries_for_category(category),
            }.items()
            if is_ps5_entry(metadata)
            and pkg_url not in exclusions.get(category, set())
        }
        legacy_category = category in {"ps1", "ps2", "psp"}
        ps5_catalog_name = category if legacy_category else f"ps5-{category}"
        ps5_output_path = catalog_output_path(ps5_catalog_name, ps5=True)

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

            for entry_key, metadata in existing_ps5_entries.items():
                if not isinstance(metadata, dict):
                    continue
                provider = metadata.get("download_source")
                if provider == "deepstorage":
                    errors, _ = validate_entry(
                        entry_key, metadata, allow_deepstorage=True
                    )
                    if errors:
                        raise RuntimeError(
                            f"Refusing to preserve invalid PS5 DeepStorage entry "
                            f"{entry_key}: {'; '.join(errors)}"
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

        # Preserve guarded provider registrations during PS5 catalog rebuilds.
        if ps5_output_path.exists():
            try:
                with ps5_output_path.open("r", encoding="utf-8") as file:
                    existing_ps5_output = json.load(file)
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"Cannot safely preserve PS5 provider registrations in "
                    f"{ps5_output_path}: {exc}"
                ) from exc
            existing_ps5_entries = (
                existing_ps5_output.get("DATA")
                if isinstance(existing_ps5_output, dict)
                else None
            )
            if not isinstance(existing_ps5_entries, dict):
                raise RuntimeError(
                    f"Cannot safely preserve PS5 provider registrations in "
                    f"{ps5_output_path}: missing valid DATA object"
                )
            for entry_key, metadata in existing_ps5_entries.items():
                if not isinstance(metadata, dict):
                    continue
                provider = metadata.get("download_source")
                if provider not in {"mocha", "deepstorage"}:
                    continue
                if provider == "deepstorage":
                    errors, _ = validate_entry(
                        entry_key, metadata, allow_deepstorage=True
                    )
                    if errors:
                        raise RuntimeError(
                            f"Refusing to preserve invalid PS5 DeepStorage entry "
                            f"{entry_key}: {'; '.join(errors)}"
                        )
                if entry_key in ps5_entries and ps5_entries[entry_key] != metadata:
                    raise RuntimeError(
                        f"PS5 {provider} catalog key conflicts with a fetched catalog "
                        f"key: {entry_key}"
                    )
                ps5_entries[entry_key] = metadata

        ps5_output = {"DATA": ps5_entries}
        with ps5_output_path.open("w", encoding="utf-8") as file:
            json.dump(ps5_output, file, indent=2, ensure_ascii=False)
            file.write("\n")
        print(
            f"PS5 catalog: {ps5_output_path} | "
            f"Entries: {len(ps5_output['DATA'])}"
        )

    print("\nMerge completed.")


if __name__ == "__main__":
    main()
