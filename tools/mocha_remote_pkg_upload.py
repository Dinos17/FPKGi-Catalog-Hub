#!/usr/bin/env python3
"""Stream a remote PKG into Mocha using its multipart upload API.

The package is never written to local disk. Source reads are bounded HTTP
Range requests; Mocha storage receives each part through a presigned PUT URL.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse, urljoin, unquote, quote

import requests

API_BASE = "https://api.mocha.my/api"
TIMEOUT = (15, 180)
MAX_REDIRECTS = 6
MAX_PART_BYTES = 128 * 1024 * 1024
MIN_PART_BYTES = 5 * 1024 * 1024
MAX_PART_COUNT = 10_000
RANGE_DEADLINE_SECONDS = 300
UPLOAD_DEADLINE_SECONDS = 6 * 60 * 60
MOCHA_STORAGE_HOSTS = {h.strip().lower() for h in os.environ.get("MOCHA_STORAGE_HOSTS", "stor11.mochaprox.li").split(",") if h.strip()}
SOURCE_HOSTS = {"archive.org", "huggingface.co", "hf.co"}


class UploadError(RuntimeError):
    pass


def _valid_https_url(url: str) -> bool:
    try:
        p = urlparse(url)
        return (p.scheme == "https" and bool(p.hostname)
                and not p.username and not p.password
                and p.port in (None, 443) and "\\" not in p.netloc)
    except ValueError:
        return False


def _source_host_allowed(url: str) -> bool:
    if not _valid_https_url(url):
        return False
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return any(host == root or host.endswith("." + root) for root in SOURCE_HOSTS)


def _mocha_storage_url_allowed(url: str) -> bool:
    if not _valid_https_url(url):
        return False
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return host in MOCHA_STORAGE_HOSTS


def _request_json(session: requests.Session, method: str, path: str,
                  api_key: str, body: dict[str, Any] | None = None,
                  params: dict[str, str] | None = None,
                  deadline: float | None = None) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    timeout = TIMEOUT
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise UploadError("Overall upload deadline exceeded")
        timeout = (min(TIMEOUT[0], remaining), min(TIMEOUT[1], remaining))
    response = session.request(method, API_BASE + path, headers=headers,
                               json=body, params=params, timeout=timeout,
                               allow_redirects=False)
    try:
        if response.status_code < 200 or response.status_code >= 300:
            detail = ""
            try:
                payload = response.json()
                detail = str(payload.get("error") or payload.get("message") or "")[:240]
            except (ValueError, AttributeError):
                pass
            raise UploadError(f"Mocha API {path} returned HTTP {response.status_code}"
                              + (f": {detail}" if detail else ""))
        if response.status_code == 204 or not response.content:
            return {}
        payload = response.json()
        if not isinstance(payload, dict):
            raise UploadError(f"Mocha API {path} returned a non-object response")
        return payload
    finally:
        response.close()


def _read_source_range(session: requests.Session, url: str, start: int,
                       length: int, *, expected_validator: tuple[str, str] | None = None,
                       overall_deadline: float | None = None
                       ) -> tuple[bytes, int, tuple[str, str]]:
    if not _source_host_allowed(url):
        raise UploadError("Source URL or redirect host is not allowlisted")
    end = start + length - 1
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        if not _source_host_allowed(current):
            raise UploadError("Source redirect host is not allowlisted")
        range_deadline = time.monotonic() + RANGE_DEADLINE_SECONDS
        if overall_deadline is not None:
            range_deadline = min(range_deadline, overall_deadline)
        remaining = range_deadline - time.monotonic()
        if remaining <= 0:
            raise UploadError("Upload deadline exceeded before source range request")
        headers = {
            "Range": f"bytes={start}-{end}",
            "Accept-Encoding": "identity",
            "User-Agent": "FPKGi-Mocha-PKG-Uploader/1.0",
        }
        if expected_validator is not None:
            headers["If-Range"] = expected_validator[1]
        response = session.get(current, headers=headers,
                               timeout=(min(15, remaining), min(30, remaining)),
                               allow_redirects=False, stream=True)
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise UploadError("Source redirect omitted Location")
            current = urljoin(current, location)
            continue
        try:
            if response.status_code != 206:
                raise UploadError(f"Source did not honor HTTP Range (status {response.status_code})")
            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", "").strip())
            if not match or (int(match.group(1)), int(match.group(2))) != (start, end):
                raise UploadError("Source returned an invalid Content-Range")
            total = int(match.group(3))
            if total <= end:
                raise UploadError("Source Content-Range total is inconsistent")
            etag = response.headers.get("ETag", "").strip()
            modified = response.headers.get("Last-Modified", "").strip()
            if etag and not etag.startswith("W/"):
                validator = ("ETag", etag)
            elif modified:
                validator = ("Last-Modified", modified)
            else:
                raise UploadError("Source has no strong ETag or Last-Modified validator")
            if expected_validator is not None and validator != expected_validator:
                raise UploadError("Source representation changed during transfer")
            data = bytearray()
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if time.monotonic() > range_deadline:
                    raise UploadError("Source range or overall upload deadline exceeded")
                if chunk:
                    data.extend(chunk)
                    if len(data) > length:
                        raise UploadError("Source returned more bytes than requested")
            if time.monotonic() > range_deadline:
                raise UploadError("Source range or overall upload deadline exceeded")
            if len(data) != length:
                raise UploadError("Source returned fewer bytes than requested")
            return bytes(data), total, validator
        finally:
            response.close()
    raise UploadError("Too many source redirects")

def _put_part(session: requests.Session, url: str, data: bytes, part_number: int,
              overall_deadline: float | None = None) -> str:
    if not _mocha_storage_url_allowed(url):
        raise UploadError("Mocha returned a presigned URL outside the storage host allowlist")
    timeout = TIMEOUT
    if overall_deadline is not None:
        remaining = overall_deadline - time.monotonic()
        if remaining <= 0:
            raise UploadError("Overall upload deadline exceeded")
        timeout = (min(TIMEOUT[0], remaining), min(TIMEOUT[1], remaining))
    response = session.put(url, data=data, headers={"Content-Type": "application/octet-stream"},
                           timeout=timeout, allow_redirects=False)
    try:
        if response.status_code < 200 or response.status_code >= 300:
            raise UploadError(f"Mocha storage rejected part {part_number} (HTTP {response.status_code})")
        etag = response.headers.get("ETag", "").strip().strip('"')
        if not etag:
            raise UploadError(f"Mocha storage omitted ETag for part {part_number}")
        return etag
    finally:
        response.close()


def _find_staged_file(session: requests.Session, api_key: str, remote_path: str,
                      staging_name: str, total_size: int,
                      deadline: float | None = None) -> str:
    """Find the uniquely named staged object after an ambiguous completion."""
    cursor = None
    matches: list[dict[str, Any]] = []
    for _ in range(100):
        params = {"path": remote_path, "limit": "100", "q": staging_name}
        if cursor:
            params["cursor"] = cursor
        payload = _request_json(session, "GET", "/files", api_key, params=params, deadline=deadline)
        files = payload.get("files")
        if not isinstance(files, list):
            raise UploadError("Mocha file inventory response is malformed")
        for item in files:
            if not isinstance(item, dict):
                continue
            names = (item.get("original_name"), item.get("file_name"))
            if (staging_name in names and str(item.get("path", "")).rstrip("/") == remote_path.rstrip("/")
                    and item.get("size") == total_size
                    and isinstance(item.get("id"), str)):
                matches.append(item)
        if payload.get("hasMore") is not True:
            break
        cursor = payload.get("nextCursor")
        if not isinstance(cursor, str) or not cursor:
            raise UploadError("Mocha inventory pagination is inconsistent")
    else:
        raise UploadError("Mocha inventory exceeded the pagination safety limit")
    if len(matches) != 1:
        raise UploadError(
            f"Could not uniquely identify staged upload in Mocha (found {len(matches)} matches)"
        )
    return matches[0]["id"]


def _rename_staged_file(session: requests.Session, api_key: str, file_id: str,
                        final_name: str, remote_path: str, staging_name: str,
                        total_size: int, deadline: float | None = None) -> None:
    """Rename by immutable file ID; resolve a lost PATCH response by inventory."""
    try:
        _request_json(session, "PATCH", "/files/" + quote(file_id, safe=""), api_key,
                      {"originalName": final_name}, deadline=deadline)
    except Exception as exc:
        try:
            payload = _request_json(session, "GET", "/files", api_key,
                                    params={"path": remote_path, "limit": "100", "q": final_name},
                                    deadline=deadline)
            files = payload.get("files")
            if not isinstance(files, list):
                raise UploadError("Mocha file inventory response is malformed")
            if any(isinstance(item, dict) and item.get("id") == file_id
                   and item.get("original_name") == final_name
                   and str(item.get("path", "")).rstrip("/") == remote_path.rstrip("/")
                   and item.get("size") == total_size
                   for item in files):
                return
        except Exception:
            pass
        raise UploadError(
            "Mocha file rename outcome is unknown; the staged object is retained. "
            "Do not upload another copy until its file ID is checked."
        ) from exc


def upload_remote_pkg(source_url: str, *, api_key: str, remote_path: str,
                      session: requests.Session | None = None) -> dict[str, Any]:
    if not _source_host_allowed(source_url):
        raise UploadError("Unsupported or unsafe source URL")
    if not api_key.strip():
        raise UploadError("MOCHA_API_KEY is not configured")
    if not remote_path.startswith("/") or ".." in PurePosixPath(remote_path).parts:
        raise UploadError("Remote destination must be an absolute Mocha folder path without '..'")

    client = session or requests.Session()
    filename = unquote(PurePosixPath(urlparse(source_url).path).name)
    if not filename or not filename.lower().endswith(".pkg") or len(filename) > 240:
        raise UploadError("Source URL does not identify a valid .pkg filename")

    overall_deadline = time.monotonic() + UPLOAD_DEADLINE_SECONDS
    # Require the full fixed-size PS4 PKG header, not merely its four-byte magic.
    header, total_size, validator = _read_source_range(
        client, source_url, 0, 0x100, overall_deadline=overall_deadline
    )
    if total_size < 0x100 or len(header) != 0x100 or header[:4] != bytes.fromhex("7f434e54"):
        raise UploadError("Source is not a complete PS4 PKG header")

    staging_name = f"fpkgi-upload-{uuid.uuid4().hex}.pkg"
    upload_id = key = node_id = None
    original_name = staging_name
    completion_started = False
    try:
        init = _request_json(client, "POST", "/files/multipart/init", api_key, {
            "originalName": staging_name,
            "size": total_size,
            "mimeType": "application/octet-stream",
            "path": remote_path,
        }, deadline=overall_deadline)
        upload_id, key, node_id = init.get("uploadId"), init.get("key"), init.get("nodeId")
        original_name = init.get("originalName") or staging_name
        if original_name != staging_name:
            raise UploadError("Mocha changed the unique staging filename unexpectedly")
        part_size = init.get("partSizeBytes")
        if init.get("directPartUpload") is True and type(init.get("directPartSizeBytes")) is int:
            part_size = init["directPartSizeBytes"]
        if not all(isinstance(v, str) and v for v in (upload_id, key, node_id)):
            raise UploadError("Mocha multipart init response is incomplete; session identifiers unavailable")
        if type(part_size) is not int or not MIN_PART_BYTES <= part_size <= MAX_PART_BYTES:
            raise UploadError("Mocha returned an unsupported part size")
        part_count = (total_size + part_size - 1) // part_size
        if not 1 <= part_count <= MAX_PART_COUNT:
            raise UploadError("Mocha part size would exceed the supported maximum part count")

        completed_parts: list[dict[str, Any]] = []
        for part_number in range(1, part_count + 1):
            offset = (part_number - 1) * part_size
            length = min(part_size, total_size - offset)
            data, observed_size, observed_validator = _read_source_range(
                client, source_url, offset, length, expected_validator=validator,
                overall_deadline=overall_deadline
            )
            if observed_size != total_size or observed_validator != validator:
                raise UploadError("Source package changed during transfer")

            urls_payload = _request_json(client, "POST", "/files/multipart/presigned", api_key, {
                "uploadId": upload_id, "key": key, "nodeId": node_id,
                "originalName": original_name, "path": remote_path,
                "partNumbers": [part_number],
            }, deadline=overall_deadline)
            urls = urls_payload.get("urls")
            if not isinstance(urls, list) or len(urls) != 1 or not isinstance(urls[0], dict):
                raise UploadError(f"Mocha did not return one presigned URL for part {part_number}")
            if type(urls[0].get("partNumber")) is not int or urls[0]["partNumber"] != part_number:
                raise UploadError(f"Mocha returned a mismatched presigned part number for part {part_number}")
            part_url = urls[0].get("url")
            if not isinstance(part_url, str) or not part_url:
                raise UploadError(f"Mocha returned an invalid presigned URL for part {part_number}")
            etag = _put_part(client, part_url, data, part_number, overall_deadline)
            completed_parts.append({"partNumber": part_number, "etag": etag})
            print(f"Uploaded part {part_number}/{part_count} ({offset + length:,}/{total_size:,} bytes)",
                  file=sys.stderr, flush=True)

        # Never blindly retry completion: a lost response may mean it committed.
        completion_started = True
        try:
            complete = _request_json(client, "POST", "/files/multipart/complete", api_key, {
                "uploadId": upload_id, "key": key, "nodeId": node_id,
                "originalName": original_name, "path": remote_path,
                "mimeType": "application/octet-stream", "size": total_size,
                "parts": completed_parts,
            }, deadline=overall_deadline)
        except Exception:
            # The unique staging name makes inventory reconciliation specific to this attempt.
            file_id = _find_staged_file(
                client, api_key, remote_path, staging_name, total_size, deadline=overall_deadline
            )
        else:
            file_obj = complete.get("file")
            file_id = file_obj.get("id") if isinstance(file_obj, dict) else None
            if not isinstance(file_id, str) or not file_id:
                file_id = _find_staged_file(
                    client, api_key, remote_path, staging_name, total_size, deadline=overall_deadline
                )

        _rename_staged_file(
            client, api_key, file_id, filename, remote_path, staging_name, total_size,
            deadline=overall_deadline
        )
        return {"file_id": file_id, "filename": filename, "size": total_size,
                "remote_path": remote_path, "parts": part_count}
    except Exception as original_error:
        if upload_id and key and node_id and not completion_started:
            try:
                _request_json(client, "POST", "/files/multipart/abort", api_key, {
                    "uploadId": upload_id, "key": key, "nodeId": node_id,
                    "originalName": original_name, "path": remote_path,
                })
            except Exception as cleanup_error:
                raise UploadError(
                    f"{original_error}; additionally, Mocha multipart abort failed: {cleanup_error}"
                ) from original_error
        raise

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--remote-path", required=True, help="Existing Mocha folder, e.g. /PS4/DLC")
    args = parser.parse_args()
    try:
        result = upload_remote_pkg(args.source_url, api_key=os.environ.get("MOCHA_API_KEY", ""),
                                   remote_path=args.remote_path)
    except (UploadError, requests.RequestException, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
