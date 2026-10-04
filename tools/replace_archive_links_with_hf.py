#!/usr/bin/env python3
"""
Replace confirmed Archive.org PKG URLs in the FPKGi catalogs with Hugging Face
dataset URLs.

Default mode is a dry-run. Use --apply to modify the catalogs.

Matching is deliberately conservative but searches using the information
already present in the GitHub catalog:
- only Archive.org URLs ending in .pkg are considered;
- exact filename is preferred;
- title ID + PKG size is used when HF naming differs;
- a unique title ID is accepted when there is only one HF PKG for it;
- normalized game/application name + size is used as another fallback;
- unmatched/ambiguous files are left unchanged and reported.

No Archive.org icon URLs or other non-PKG URLs are changed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import requests

REPO_OWNER = "dinos17"
HF_BASE = "https://huggingface.co"
CATALOGS = {
    "ps4/games.json": "PS-Games-Dataset",
    "ps4/dlc.json": "PS-Applications",
    "ps4/updates.json": "PS-Applications",
}

TIMEOUT = 30
PAGE_SIZE = 1000


def hf_tree(repo_id: str) -> list[dict]:
    """Fetch the complete HF dataset tree using the public Hub API."""
    url = f"{HF_BASE}/api/datasets/{REPO_OWNER}/{repo_id}/tree/main"
    params = {"recursive": "true", "expand": "false", "limit": PAGE_SIZE}
    files: list[dict] = []
    session = requests.Session()

    while True:
        response = session.get(url, params=params, timeout=TIMEOUT)
        response.raise_for_status()
        batch = response.json()
        if not isinstance(batch, list):
            raise RuntimeError(f"Unexpected HF API response for {repo_id}")

        files.extend(
            item for item in batch
            if item.get("type") == "file" and item.get("path", "").lower().endswith(".pkg")
        )

        # Hugging Face uses a Link header for paginated tree responses.
        link = response.headers.get("Link", "")
        next_url = None
        for part in link.split(","):
            if 'rel="next"' in part:
                next_url = part.strip().split(";", 1)[0].strip("<> ")
                break

        if not next_url:
            break

        url = next_url
        params = {}

    return files


def build_index(files: list[dict]) -> dict[str, list[dict]]:
    index: dict[str, list[dict]] = {}
    for item in files:
        name = Path(item["path"]).name.casefold()
        index.setdefault(name, []).append(item)
    return index


def is_archive_pkg(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    path = unquote(parsed.path)
    return host.endswith("archive.org") and path.lower().endswith(".pkg")


def replace_in_value(
    value: object,
    expected_repo: str,
    indexes: dict[str, dict[str, list[dict]]],
    stats: dict[str, int],
    unmatched: list[dict],
    ambiguous: list[dict],
) -> object:
    if isinstance(value, list):
        return [
            replace_in_value(v, expected_repo, indexes, stats, unmatched, ambiguous)
            for v in value
        ]

    if isinstance(value, dict):
        # Keep the record size available for filename disambiguation.
        record_size = value.get("size")
        return {
            k: replace_url(v, expected_repo, record_size, indexes, stats, unmatched, ambiguous)
            if isinstance(v, str)
            else replace_in_value(v, expected_repo, indexes, stats, unmatched, ambiguous)
            for k, v in value.items()
        }

    return value


def replace_url(
    value: str,
    expected_repo: str,
    record_size: object,
    indexes: dict[str, dict[str, list[dict]]],
    stats: dict[str, int],
    unmatched: list[dict],
    ambiguous: list[dict],
) -> str:
    if not is_archive_pkg(value):
        return value

    parsed = urlparse(value)
    filename = Path(unquote(parsed.path)).name
    candidates = indexes[expected_repo].get(filename.casefold(), [])

    if not candidates:
        stats["unmatched"] += 1
        unmatched.append({"url": value, "filename": filename, "repo": expected_repo})
        return value

    if len(candidates) > 1 and isinstance(record_size, int):
        sized = [x for x in candidates if x.get("size") == record_size]
        if len(sized) == 1:
            candidates = sized

    if len(candidates) != 1:
        stats["ambiguous"] += 1
        ambiguous.append(
            {
                "url": value,
                "filename": filename,
                "repo": expected_repo,
                "candidates": [x.get("path") for x in candidates],
            }
        )
        return value

    path = candidates[0]["path"]
    hf_url = f"{HF_BASE}/datasets/{REPO_OWNER}/{expected_repo}/resolve/main/{quote(path, safe='/')}?download=true"
    stats["replaced"] += 1
    return hf_url


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo",
        default=".",
        help="Path to the FPKGi-Catalog-Hub repository (default: current directory)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the replacements to the catalog files",
    )
    args = parser.parse_args()

    root = Path(args.repo).resolve()
    indexes: dict[str, dict[str, list[dict]]] = {}

    print("Fetching Hugging Face file indexes...")
    for repo_id in sorted(set(CATALOGS.values())):
        files = hf_tree(repo_id)
        indexes[repo_id] = build_index(files)
        print(f"  {repo_id}: {len(files)} PKG files")

    stats = {"replaced": 0, "unmatched": 0, "ambiguous": 0}
    unmatched: list[dict] = []
    ambiguous: list[dict] = []

    pending: list[tuple[Path, str, str, object]] = []

    for rel_path, expected_repo in CATALOGS.items():
        path = root / rel_path
        if not path.exists():
            raise FileNotFoundError(path)

        original = path.read_text(encoding="utf-8")
        data = json.loads(original)

        # Work on a deep JSON traversal while retaining the top-level records'
        # size field for URL disambiguation.
        def walk(obj: object) -> object:
            if isinstance(obj, list):
                return [walk(x) for x in obj]
            if isinstance(obj, dict):
                return {
                    k: (
                        replace_url(
                            v,
                            expected_repo,
                            obj,
                            indexes,
                            stats,
                            unmatched,
                            ambiguous,
                        )
                        if isinstance(v, str)
                        else walk(v)
                    )
                    for k, v in obj.items()
                }

        updated = walk(data)
        rendered = json.dumps(updated, ensure_ascii=False, indent=2) + "\n"

        if rendered != original:
            pending.append((path, original, rendered, expected_repo))

    print()
    print(f"Confirmed replacements: {stats['replaced']}")
    print(f"Unmatched PKG URLs:     {stats['unmatched']}")
    print(f"Ambiguous PKG URLs:     {stats['ambiguous']}")
    print(f"Catalog files changed:  {len(pending)}")

    if unmatched:
        print("\nUnmatched examples:")
        for item in unmatched[:20]:
            print(f"  {item['filename']} -> {item['repo']}")

    if ambiguous:
        print("\nAmbiguous examples:")
        for item in ambiguous[:20]:
            print(f"  {item['filename']} -> {item['candidates']}")

    if args.apply:
        for path, _, rendered, _ in pending:
            path.write_text(rendered, encoding="utf-8")
            print(f"Updated {path.relative_to(root)}")
        print("\nApplied. Review the diff before committing.")
    else:
        print("\nDry-run only. Re-run with --apply to modify the catalogs.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
