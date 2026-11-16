#!/usr/bin/env python3
"""Sort local files into AkiraBox upload-size groups without uploading them.

The default registered-free-account threshold is 20 GiB (21,474,836,480 bytes).
Files at or below the threshold go to small_file.json; larger files go to
big_file.json. Symlinks are skipped so the scan cannot escape the chosen tree.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_LIMIT_BYTES = 20 * 1024**3


def scan_files(root: Path, limit_bytes: int, excluded: set[Path] | None = None
               ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    root = root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"Input path is not a directory: {root}")
    excluded = {p.resolve() for p in (excluded or set())}
    small: list[dict[str, Any]] = []
    big: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    def onerror(exc: OSError) -> None:
        errors.append({"path": str(getattr(exc, "filename", "") or root),
                       "error": str(exc)})

    for current, dirs, filenames in os.walk(root, topdown=True, followlinks=False,
                                              onerror=onerror):
        current_path = Path(current)
        kept_dirs: list[str] = []
        for dirname in dirs:
            candidate = current_path / dirname
            if candidate.is_symlink():
                continue
            try:
                if candidate.resolve() not in excluded:
                    kept_dirs.append(dirname)
            except OSError as exc:
                errors.append({"path": str(candidate), "error": str(exc)})
        dirs[:] = kept_dirs

        for filename in filenames:
            path = current_path / filename
            if path.is_symlink() or not path.is_file():
                continue
            try:
                resolved = path.resolve(strict=True)
                if resolved in excluded:
                    continue
                size = path.stat().st_size
            except OSError as exc:
                errors.append({"path": str(path), "error": str(exc)})
                continue
            record = {
                "name": path.name,
                "path": path.relative_to(root).as_posix(),
                "extension": path.suffix.lower(),
                "size_bytes": size,
                "size_gib": round(size / (1024**3), 6),
            }
            (small if size <= limit_bytes else big).append(record)

    key = lambda item: item["path"].casefold()
    small.sort(key=key)
    big.sort(key=key)
    return small, big, errors


def write_report(path: Path, limit_bytes: int, files: list[dict[str, Any]]) -> None:
    report = {
        "schema_version": 1,
        "limit_bytes": limit_bytes,
        "comparison": "less_than_or_equal_is_small",
        "count": len(files),
        "files": files,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Directory tree to scan")
    parser.add_argument("--limit-bytes", type=int, default=DEFAULT_LIMIT_BYTES,
                        help=f"Maximum size for small files (default: {DEFAULT_LIMIT_BYTES})")
    parser.add_argument("--output-dir", type=Path, default=Path("file_sort_report"),
                        help="Directory for small_file.json and big_file.json")
    args = parser.parse_args(argv)

    if args.limit_bytes < 0:
        parser.error("--limit-bytes must be zero or greater")

    try:
        root = args.directory.expanduser().resolve(strict=True)
        output_dir = args.output_dir.expanduser().resolve()
        small, big, errors = scan_files(root, args.limit_bytes, {output_dir})
        write_report(output_dir / "small_file.json", args.limit_bytes, small)
        write_report(output_dir / "big_file.json", args.limit_bytes, big)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"Scanned: {root}")
    print(f"Limit: {args.limit_bytes:,} bytes ({args.limit_bytes / 1024**3:.3f} GiB)")
    print(f"Small files: {len(small)} -> {output_dir / 'small_file.json'}")
    print(f"Big files:   {len(big)} -> {output_dir / 'big_file.json'}")
    print(f"Scan errors: {len(errors)}")
    if errors:
        for error in errors:
            print(f"WARNING: {error['path']}: {error['error']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
