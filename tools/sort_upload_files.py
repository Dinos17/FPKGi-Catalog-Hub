#!/usr/bin/env python3
"""Classify every PS4 and PS5 catalog entry by its declared package size.

Reads catalog metadata only. It does not inspect local package files, resolve
links, download packages, or upload anything.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

DEFAULT_LIMIT_BYTES = 20_000_000_000
PLATFORMS = ("ps4", "ps5")


def discover_catalogs(repo_root: Path) -> list[tuple[str, str, Path]]:
    """Find every JSON catalog under the PS4 and PS5 catalog directories."""
    catalogs: list[tuple[str, str, Path]] = []
    for platform in PLATFORMS:
        directory = repo_root / platform
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.json")):
            relative = path.relative_to(repo_root)
            category = path.stem
            if platform == "ps5" and category.startswith("ps5-"):
                category = category[4:]
            catalogs.append((platform, category, relative))
    return catalogs


def parse_size(raw_size: Any) -> int | None:
    if isinstance(raw_size, bool) or not isinstance(raw_size, (int, float)):
        return None
    if raw_size < 0 or int(raw_size) != raw_size:
        return None
    return int(raw_size)


def load_entries(repo_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[str]]:
    entries: list[dict[str, Any]] = []
    problems: list[dict[str, str]] = []
    catalog_paths: list[str] = []

    for platform, category, relative_path in discover_catalogs(repo_root):
        catalog_name = relative_path.as_posix()
        catalog_paths.append(catalog_name)
        path = repo_root / relative_path
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            problems.append({"catalog": catalog_name, "error": str(exc)})
            continue

        data = payload.get("DATA") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            problems.append({"catalog": catalog_name, "error": "expected an object at DATA"})
            continue

        for source_url, metadata in data.items():
            if not isinstance(metadata, dict):
                problems.append({
                    "catalog": catalog_name,
                    "source_url": str(source_url),
                    "error": "entry metadata is not an object",
                })
                continue

            size = parse_size(metadata.get("size"))
            entries.append({
                "platform": platform,
                "category": category,
                "catalog": catalog_name,
                "source_url": source_url,
                "name": metadata.get("name"),
                "title_id": metadata.get("title_id"),
                "version": metadata.get("version"),
                "size_bytes": size,
                "size_gb": round(size / 1_000_000_000, 6) if size is not None else None,
            })

    return entries, problems, catalog_paths


def classify(
    entries: list[dict[str, Any]], limit_bytes: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    small: list[dict[str, Any]] = []
    big: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    for entry in entries:
        size = entry["size_bytes"]
        if size is None:
            unknown.append(entry)
        elif size <= limit_bytes:
            small.append(entry)
        else:
            big.append(entry)

    sort_key = lambda item: (
        item["platform"],
        item["category"],
        str(item.get("name") or "").casefold(),
        str(item.get("source_url") or ""),
    )
    return (
        sorted(small, key=sort_key),
        sorted(big, key=sort_key),
        sorted(unknown, key=sort_key),
    )


def write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--output-dir", type=Path, default=Path("file_sort_report"))
    parser.add_argument("--limit-bytes", type=int, default=DEFAULT_LIMIT_BYTES)
    args = parser.parse_args(argv)

    if args.limit_bytes < 0:
        parser.error("--limit-bytes must be zero or greater")

    root = args.repo_root.resolve()
    entries, problems, catalog_paths = load_entries(root)
    small, big, unknown = classify(entries, args.limit_bytes)
    output = args.output_dir.resolve()
    common = {
        "schema_version": 2,
        "limit_bytes": args.limit_bytes,
        "limit_gb": args.limit_bytes / 1_000_000_000,
        "catalogs_scanned": catalog_paths,
        "catalog_count": len(catalog_paths),
        "note": "Metadata-only classification; no package files were accessed or transferred.",
    }
    for filename, group in (
        ("small_file.json", small),
        ("big_file.json", big),
        ("unknown_size.json", unknown),
    ):
        write_json(output / filename, {**common, "count": len(group), "entries": group})

    write_json(output / "sort_errors.json", {
        **common,
        "count": len(problems),
        "errors": problems,
    })

    print(f"Catalog files scanned: {len(catalog_paths)}")
    print(f"Catalog entries read: {len(entries)}")
    print(f"Small (<= {args.limit_bytes:,} bytes): {len(small)}")
    print(f"Big (> {args.limit_bytes:,} bytes): {len(big)}")
    print(f"Unknown/invalid size: {len(unknown)}")
    print(f"Catalog problems: {len(problems)}")
    print(f"Reports: {output}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
