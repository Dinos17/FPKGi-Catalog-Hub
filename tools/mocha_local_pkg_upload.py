#!/usr/bin/env python3
"""Safely inventory and optionally upload local PS4 PKGs to an existing Mocha folder.

Dry-run is the default. Transfers require --upload and an interactive confirmation
unless --yes is also supplied. Existing same-name/same-size files are skipped;
same-name/different-size files are reported as conflicts and never overwritten.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

import requests

from tools import mocha_remote_pkg_upload as remote

DEFAULT_DIRECTORY = Path(
    "/media/fallen_angel/0840CF6D40CF60542/PS4_Under_20GB_Downloads/Uploaded_Uploading"
)
DEFAULT_REMOTE_PATH = "/PS4/Games"
PKG_MAGIC = bytes.fromhex("7f434e54")
HEADER_SIZE = 0x100
CONFIG_KEY = Path.home() / ".config/mocha/api_key"


def read_api_key() -> str:
    key = os.environ.get("MOCHA_API_KEY", "").strip()
    if key:
        return key
    try:
        key = CONFIG_KEY.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise remote.UploadError(
            f"Mocha API key unavailable; configure MOCHA_API_KEY or {CONFIG_KEY}"
        ) from exc
    if not key:
        raise remote.UploadError("Saved Mocha API key is empty")
    return key


def validate_remote_path(path: str) -> str:
    if not path.startswith("/") or path == "/" or ".." in PurePosixPath(path).parts:
        raise remote.UploadError("Destination must be an existing absolute Mocha folder path")
    return path.rstrip("/")


def list_remote_pkgs(session: requests.Session, api_key: str,
                     remote_path: str) -> dict[str, list[dict[str, Any]]]:
    cursor = None
    seen_cursors: set[str] = set()
    by_name: dict[str, list[dict[str, Any]]] = {}
    normalized_path = remote_path.rstrip("/")

    for page in range(1, 101):
        params: dict[str, str] = {"path": remote_path, "limit": "100"}
        if cursor:
            params["cursor"] = cursor
        payload = remote._request_json(session, "GET", "/files", api_key, params=params)
        files = payload.get("files")
        if not isinstance(files, list):
            raise remote.UploadError("Mocha inventory response is malformed")

        for item in files:
            if not isinstance(item, dict):
                continue
            if str(item.get("path", "")).rstrip("/") != normalized_path:
                continue
            name = item.get("original_name") or item.get("file_name")
            size = item.get("size")
            if not isinstance(name, str) or not name.lower().endswith(".pkg"):
                continue
            if type(size) is not int or size < 0:
                raise remote.UploadError(f"Mocha returned an invalid size for {name}")
            record = {
                "id": item.get("id"),
                "name": name,
                "size": size,
                "path": item.get("path"),
            }
            by_name.setdefault(name, []).append(record)

        if payload.get("hasMore") is not True:
            return by_name
        next_cursor = payload.get("nextCursor")
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen_cursors:
            raise remote.UploadError("Mocha inventory pagination is inconsistent")
        seen_cursors.add(next_cursor)
        cursor = next_cursor

    raise remote.UploadError("Mocha inventory exceeded the 100-page safety limit")


def discover_local_pkgs(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise remote.UploadError(f"Local directory does not exist: {directory}")
    files = sorted(
        (p for p in directory.iterdir() if p.is_file() and p.suffix.lower() == ".pkg"),
        key=lambda p: p.name.casefold(),
    )
    if not files:
        raise remote.UploadError(f"No .pkg files found in {directory}")
    names = [p.name for p in files]
    if len(names) != len(set(names)):
        raise remote.UploadError("Local directory contains duplicate package filenames")
    return files


def classify(local_files: list[Path],
             remote_files: dict[str, list[dict[str, Any]]]
             ) -> tuple[list[tuple[Path, dict[str, Any]]],
                        list[Path],
                        list[tuple[Path, dict[str, Any]]]]:
    exact: list[tuple[Path, dict[str, Any]]] = []
    missing: list[Path] = []
    conflicts: list[tuple[Path, dict[str, Any]]] = []
    for path in local_files:
        records = remote_files.get(path.name, [])
        if not records:
            missing.append(path)
        elif len(records) != 1:
            conflicts.append((path, {"reason": "duplicate remote filename", "records": records}))
        elif records[0]["size"] == path.stat().st_size:
            exact.append((path, records[0]))
        else:
            conflicts.append((path, records[0]))
    return exact, missing, conflicts


def file_signature(file_obj: Any) -> tuple[int, int, int, int]:
    st = os.fstat(file_obj.fileno())
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns


def validate_local_pkg(path: Path) -> tuple[int, tuple[int, int, int, int]]:
    with path.open("rb") as handle:
        signature = file_signature(handle)
        header = handle.read(HEADER_SIZE)
        if len(header) != HEADER_SIZE or header[:4] != PKG_MAGIC:
            raise remote.UploadError(f"Not a valid PS4 PKG header: {path.name}")
        if signature[2] < HEADER_SIZE:
            raise remote.UploadError(f"PKG is smaller than its required header: {path.name}")
        if file_signature(handle) != signature:
            raise remote.UploadError(f"File changed during validation: {path.name}")
        return signature[2], signature


def upload_local_pkg(path: Path, *, api_key: str, remote_path: str,
                     expected_size: int, expected_signature: tuple[int, int, int, int],
                     session: requests.Session | None = None) -> dict[str, Any]:
    client = session or requests.Session()
    deadline = time.monotonic() + remote.UPLOAD_DEADLINE_SECONDS
    staging_name = f"fpkgi-upload-{uuid.uuid4().hex}.pkg"
    upload_id = object_key = node_id = None
    original_name = staging_name
    completion_started = False

    try:
        with path.open("rb") as source:
            if file_signature(source) != expected_signature:
                raise remote.UploadError(f"Local file changed before upload: {path.name}")
            header = source.read(HEADER_SIZE)
            if len(header) != HEADER_SIZE or header[:4] != PKG_MAGIC:
                raise remote.UploadError(f"Invalid PS4 PKG header: {path.name}")
            if file_signature(source) != expected_signature:
                raise remote.UploadError(f"Local file changed during header read: {path.name}")

            init = remote._request_json(client, "POST", "/files/multipart/init", api_key, {
                "originalName": staging_name,
                "size": expected_size,
                "mimeType": "application/octet-stream",
                "path": remote_path,
            }, deadline=deadline)
            upload_id = init.get("uploadId")
            object_key = init.get("key")
            node_id = init.get("nodeId")
            original_name = init.get("originalName") or staging_name
            if original_name != staging_name:
                raise remote.UploadError("Mocha changed the unique staging filename unexpectedly")
            part_size = init.get("partSizeBytes")
            if init.get("directPartUpload") is True and type(init.get("directPartSizeBytes")) is int:
                part_size = init["directPartSizeBytes"]
            if not all(isinstance(v, str) and v for v in (upload_id, object_key, node_id)):
                raise remote.UploadError("Mocha multipart init response is incomplete")
            if type(part_size) is not int or not remote.MIN_PART_BYTES <= part_size <= remote.MAX_PART_BYTES:
                raise remote.UploadError("Mocha returned an unsupported multipart part size")
            part_count = (expected_size + part_size - 1) // part_size
            if not 1 <= part_count <= remote.MAX_PART_COUNT:
                raise remote.UploadError("Mocha part size exceeds the supported part-count limit")

            parts: list[dict[str, Any]] = []
            source.seek(0)
            for part_number in range(1, part_count + 1):
                if time.monotonic() >= deadline:
                    raise remote.UploadError("Overall upload deadline exceeded")
                if file_signature(source) != expected_signature:
                    raise remote.UploadError(f"Local file changed during upload: {path.name}")
                offset = (part_number - 1) * part_size
                length = min(part_size, expected_size - offset)
                data = source.read(length)
                if len(data) != length:
                    raise remote.UploadError(f"Local file ended early while reading {path.name}")

                presigned = remote._request_json(client, "POST", "/files/multipart/presigned", api_key, {
                    "uploadId": upload_id, "key": object_key, "nodeId": node_id,
                    "originalName": original_name, "path": remote_path,
                    "partNumbers": [part_number],
                }, deadline=deadline)
                urls = presigned.get("urls")
                if not isinstance(urls, list) or len(urls) != 1 or not isinstance(urls[0], dict):
                    raise remote.UploadError(f"Mocha did not return one URL for part {part_number}")
                if type(urls[0].get("partNumber")) is not int or urls[0]["partNumber"] != part_number:
                    raise remote.UploadError(f"Mocha returned a mismatched part number for part {part_number}")
                part_url = urls[0].get("url")
                if not isinstance(part_url, str) or not part_url:
                    raise remote.UploadError(f"Mocha returned an invalid URL for part {part_number}")
                etag = remote._put_part(client, part_url, data, part_number, deadline)
                parts.append({"partNumber": part_number, "etag": etag})
                print(
                    f"  {path.name}: part {part_number}/{part_count} "
                    f"({offset + length:,}/{expected_size:,} bytes)",
                    file=sys.stderr, flush=True,
                )

            if file_signature(source) != expected_signature or source.tell() != expected_size:
                raise remote.UploadError(f"Local file changed or size mismatch after reading: {path.name}")

        completion_started = True
        try:
            completed = remote._request_json(client, "POST", "/files/multipart/complete", api_key, {
                "uploadId": upload_id, "key": object_key, "nodeId": node_id,
                "originalName": original_name, "path": remote_path,
                "mimeType": "application/octet-stream", "size": expected_size,
                "parts": parts,
            }, deadline=deadline)
        except Exception:
            file_id = remote._find_staged_file(
                client, api_key, remote_path, staging_name, expected_size, deadline=deadline
            )
        else:
            file_obj = completed.get("file")
            file_id = file_obj.get("id") if isinstance(file_obj, dict) else None
            if not isinstance(file_id, str) or not file_id:
                file_id = remote._find_staged_file(
                    client, api_key, remote_path, staging_name, expected_size, deadline=deadline
                )

        remote._rename_staged_file(
            client, api_key, file_id, path.name, remote_path, staging_name,
            expected_size, deadline=deadline,
        )
        return {
            "file_id": file_id,
            "filename": path.name,
            "size": expected_size,
            "remote_path": remote_path,
            "parts": part_count,
        }
    except Exception as original_error:
        if upload_id and object_key and node_id and not completion_started:
            try:
                remote._request_json(client, "POST", "/files/multipart/abort", api_key, {
                    "uploadId": upload_id, "key": object_key, "nodeId": node_id,
                    "originalName": original_name, "path": remote_path,
                })
            except Exception as cleanup_error:
                raise remote.UploadError(
                    f"{original_error}; additionally, Mocha multipart abort failed: {cleanup_error}"
                ) from original_error
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--remote-path", default=DEFAULT_REMOTE_PATH)
    parser.add_argument("--upload", action="store_true",
                        help="perform uploads; without this flag the command is read-only")
    parser.add_argument("--yes", action="store_true",
                        help="skip the interactive confirmation (requires --upload)")
    args = parser.parse_args(argv)

    if args.yes and not args.upload:
        parser.error("--yes requires --upload")

    try:
        remote_path = validate_remote_path(args.remote_path)
        api_key = read_api_key()
        local_files = discover_local_pkgs(args.directory)
        session = requests.Session()
        remote_files = list_remote_pkgs(session, api_key, remote_path)
        exact, missing, conflicts = classify(local_files, remote_files)

        print(f"Destination: {remote_path}")
        print(f"Local PKGs: {len(local_files)}")
        print(f"Remote PKGs: {sum(len(v) for v in remote_files.values())}")
        print(f"Already present (same name and size): {len(exact)}")
        print(f"Missing upload candidates: {len(missing)}")
        print(f"Conflicts (will NOT be overwritten): {len(conflicts)}")
        for path, record in conflicts:
            print(f"CONFLICT: {path.name} | local={path.stat().st_size:,} | remote={record}")

        if not args.upload:
            print("\nDRY RUN ONLY. No files uploaded or changed.")
            return 0

        # Validate every candidate before starting the first irreversible transfer.
        validated: list[tuple[Path, int, tuple[int, int, int, int]]] = []
        for path in missing:
            size, signature = validate_local_pkg(path)
            validated.append((path, size, signature))

        print(f"\nReady to upload {len(validated)} PKG(s).")
        if not args.yes:
            answer = input('Type UPLOAD to transfer these files: ').strip()
            if answer != "UPLOAD":
                print("Cancelled. No files uploaded.")
                return 0

        results = []
        for index, (path, size, signature) in enumerate(validated, 1):
            print(f"[{index}/{len(validated)}] Uploading {path.name} ({size:,} bytes)")
            result = upload_local_pkg(
                path, api_key=api_key, remote_path=remote_path,
                expected_size=size, expected_signature=signature, session=session,
            )
            results.append(result)
            print(f"  Uploaded: {result['file_id']}")

        print(json.dumps({"uploaded": len(results), "results": results}, ensure_ascii=False, indent=2))
        return 0
    except (remote.UploadError, requests.RequestException, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
