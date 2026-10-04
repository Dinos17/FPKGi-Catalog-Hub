#!/usr/bin/env python3
"""
Replace confirmed Archive.org PKG URLs in the FPKGi catalogs with Hugging Face
dataset URLs.

Default mode is a dry-run. Use --apply to modify the catalogs.

Matching is conservative and uses the information already present in the
GitHub catalog record:
1. exact filename
2. title ID + PKG size
3. unique title ID
4. normalized game/application name + PKG size
5. otherwise leave the Archive.org URL unchanged

Only Archive.org .pkg URLs are considered. Icon URLs and every other URL are
left untouched.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
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
TITLE_ID_RE = re.compile(r"\b([A-Z]{4}\d{5})\b", re.IGNORECASE)


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
            item
            for item in batch
            if item.get("type") == "file"
            and item.get("path", "").lower().endswith(".pkg")
        )

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


def normalize_name(value: object) -> str:
    """Normalize a catalog/HF name for conservative name comparison."""
    if not isinstance(value, str):
        return ""

    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii").casefold()

    # Remove common filename metadata such as [US], [EU], [EN], versions,
    # CUSA IDs, and separators. This is intentionally not fuzzy matching.
    value = re.sub(r"\[[^\]]*\]", " ", value)
    value = TITLE_ID_RE.sub(" ", value)
    value = re.sub(r"\b(?:v|ver|version)?\s*\d+(?:\.\d+){1,3}\b", " ", value)
    value = re.sub(r"[_./\\-]+", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def extract_title_ids(value: object) -> set[str]:
    if not isinstance(value, str):
        return set()
    return {match.upper() for match in TITLE_ID_RE.findall(unquote(value))}


def candidate_name(item: dict) -> str:
    return normalize_name(Path(unquote(item.get("path", ""))).stem)


def build_index(files: list[dict]) -> dict[str, dict]:
    """
    Build indexes used by the five conservative matching levels.

    HF tree metadata normally provides path + size. Title IDs are extracted
    from the HF filename/path because the Hub file itself is not inspected.
    """
    exact: dict[str, list[dict]] = {}
    title_id: dict[str, list[dict]] = {}
    name_size: dict[tuple[str, int], list[dict]] = {}

    for item in files:
        path = item.get("path", "")
        filename = Path(path).name.casefold()
        exact.setdefault(filename, []).append(item)

        ids = extract_title_ids(path)
        for tid in ids:
            title_id.setdefault(tid, []).append(item)

        size = item.get("size")
        name = candidate_name(item)
        if isinstance(size, int) and name:
            name_size.setdefault((name, size), []).append(item)

    return {
        "exact": exact,
        "title_id": title_id,
        "name_size": name_size,
    }


def is_archive_pkg(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    path = unquote(parsed.path)
    return host.endswith("archive.org") and path.lower().endswith(".pkg")


def make_hf_url(repo_id: str, path: str) -> str:
    return (
        f"{HF_BASE}/datasets/{REPO_OWNER}/{repo_id}/resolve/main/"
        f"{quote(path, safe='/')}?download=true"
    )


def choose_candidate(
    value: str,
    record: dict,
    repo_id: str,
    indexes: dict[str, dict],
) -> tuple[dict | None, str | None, list[dict]]:
    """Return (candidate, match_method, ambiguous_candidates)."""
    parsed = urlparse(value)
    filename = Path(unquote(parsed.path)).name.casefold()
    index = indexes[repo_id]

    # 1. Exact filename.
    candidates = index["exact"].get(filename, [])
    if len(candidates) == 1:
        return candidates[0], "exact filename", []
    if len(candidates) > 1:
        sized = [
            item for item in candidates
            if item.get("size") == record.get("size")
        ]
        if len(sized) == 1:
            return sized[0], "exact filename + size", []
        return None, None, candidates

    record_size = record.get("size")
    record_ids = extract_title_ids(record.get("title_id"))

    # 2. Title ID + PKG size.
    if record_ids and isinstance(record_size, int):
        sized: list[dict] = []
        for tid in record_ids:
            sized.extend(
                item
                for item in index["title_id"].get(tid, [])
                if item.get("size") == record_size
            )
        unique = {item.get("path"): item for item in sized}
        if len(unique) == 1:
            return next(iter(unique.values())), "title ID + size", []
        if len(unique) > 1:
            return None, None, list(unique.values())

    # 3. Unique title ID.
    if record_ids:
        by_path: dict[str, dict] = {}
        for tid in record_ids:
            for item in index["title_id"].get(tid, []):
                by_path[item.get("path")] = item
        if len(by_path) == 1:
            return next(iter(by_path.values())), "unique title ID", []
        if len(by_path) > 1:
            # If multiple versions exist, do not guess.
            return None, None, list(by_path.values())

    # 4. Normalized catalog name + size.
    if isinstance(record_size, int):
        name = normalize_name(record.get("name"))
        candidates = index["name_size"].get((name, record_size), [])
        if len(candidates) == 1:
            return candidates[0], "normalized name + size", []
        if len(candidates) > 1:
            return None, None, candidates

    return None, None, []


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
    indexes: dict[str, dict] = {}

    print("Fetching Hugging Face file indexes...")
    for repo_id in sorted(set(CATALOGS.values())):
        files = hf_tree(repo_id)
        indexes[repo_id] = build_index(files)
        print(f"  {repo_id}: {len(files)} PKG files")

    stats = {
        "replaced": 0,
        "unmatched": 0,
        "ambiguous": 0,
    }
    methods: dict[str, int] = {}
    unmatched: list[dict] = []
    ambiguous: list[dict] = []
    pending: list[tuple[Path, str]] = []

    for rel_path, expected_repo in CATALOGS.items():
        path = root / rel_path
        if not path.exists():
            raise FileNotFoundError(path)

        original = path.read_text(encoding="utf-8")
        data = json.loads(original)

        def walk(obj: object) -> object:
            if isinstance(obj, list):
                return [walk(x) for x in obj]

            if isinstance(obj, dict):
                # The catalogs are keyed by URL under DATA. Pass the associated
                # record dict to the matcher so title_id/name/size are available.
                if "DATA" in obj and isinstance(obj["DATA"], dict):
                    new_data = {}
                    for url, record in obj["DATA"].items():
                        if isinstance(url, str) and is_archive_pkg(url) and isinstance(record, dict):
                            candidate, method, ambiguous_candidates = choose_candidate(
                                url, record, expected_repo, indexes
                            )
                            if candidate is not None:
                                new_url = make_hf_url(expected_repo, candidate["path"])
                                new_data[new_url] = record
                                stats["replaced"] += 1
                                methods[method] = methods.get(method, 0) + 1
                            else:
                                new_data[url] = record
                                if ambiguous_candidates:
                                    stats["ambiguous"] += 1
                                    ambiguous.append({
                                        "url": url,
                                        "name": record.get("name"),
                                        "title_id": record.get("title_id"),
                                        "repo": expected_repo,
                                        "candidates": [
                                            item.get("path")
                                            for item in ambiguous_candidates
                                        ],
                                    })
                                else:
                                    stats["unmatched"] += 1
                                    unmatched.append({
                                        "url": url,
                                        "name": record.get("name"),
                                        "title_id": record.get("title_id"),
                                        "repo": expected_repo,
                                    })
                        else:
                            new_data[url] = walk(record)
                    return {**obj, "DATA": new_data}

                return {k: walk(v) for k, v in obj.items()}

            return obj

        updated = walk(data)

        # Compare parsed JSON, not rendered text. This avoids reporting
        # line-ending/serialization differences as catalog changes.
        if updated != data:
            rendered = json.dumps(updated, ensure_ascii=False, indent=2) + "\n"
            pending.append((path, rendered))

    print()
    print(f"Confirmed replacements: {stats['replaced']}")
    print(f"Unmatched PKG URLs:     {stats['unmatched']}")
    print(f"Ambiguous PKG URLs:     {stats['ambiguous']}")
    print(f"Catalog files changed:  {len(pending)}")

    if methods:
        print("\nMatch methods:")
        for method, count in methods.items():
            print(f"  {method}: {count}")

    if unmatched:
        print("\nUnmatched examples:")
        for item in unmatched[:20]:
            print(
                f"  {item['name']!r} ({item['title_id']}) -> {item['repo']}"
            )

    if ambiguous:
        print("\nAmbiguous examples:")
        for item in ambiguous[:20]:
            print(
                f"  {item['name']!r} ({item['title_id']}) -> "
                f"{item['candidates']}"
            )

    if args.apply:
        for path, rendered in pending:
            path.write_text(rendered, encoding="utf-8")
            print(f"Updated {path.relative_to(root)}")
        print("\nApplied. Review the diff before committing.")
    else:
        print("\nDry-run only. Re-run with --apply to modify the catalogs.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
