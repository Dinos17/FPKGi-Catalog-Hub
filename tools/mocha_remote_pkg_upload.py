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
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse, urljoin, unquote

import requests

API_BASE = "https://api.mocha.my/api"
TIMEOUT = (15, 180)
MAX_REDIRECTS = 6
MAX_PART_BYTES = 128 * 1024 * 1024
MIN_PART_BYTES = 5 * 1024 * 1024
MAX_PART_COUNT = 10_000
RANGE_DEADLINE_SECONDS = 300
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
                  api_key: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    response = session.request(method, API_BASE + path, headers=headers,
                               json=body, timeout=TIMEOUT, allow_redirects=False)
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
                       length: int, *, expected_validator: tuple[str, str] | None = None
                       ) -> tuple[bytes, int, tuple[str, str]]:
    if not _source_host_allowed(url):
        raise UploadError("Source URL or redirect host is not allowlisted")
    end = start + length - 1
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        if not _source_host_allowed(current):
            raise UploadError("Source redirect host is not allowlisted")
        deadline = time.monotonic() + RANGE_DEADLINE_SECONDS
        headers = {
            "Range": f"bytes={start}-{end}",
            "Accept-Encoding": "identity",
            "User-Agent": "FPKGi-Mocha-PKG-Uploader/1.0",
        }
        if expected_validator is not None:
            headers["If-Range"] = expected_validator[1]
        response = session.get(current, headers=headers, timeout=(15, 30),
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
                if time.monotonic() > deadline:
                    raise UploadError("Source range exceeded its transfer deadline")
                if chunk:
                    data.extend(chunk)
                    if len(data) > length:
                        raise UploadError("Source returned more bytes than requested")
            if time.monotonic() > deadline:
                raise UploadError("Source range exceeded its transfer deadline")
            if len(data) != length:
                raise UploadError("Source returned fewer bytes than requested")
            return bytes(data), total, validator
        finally:
            response.close()
    raise UploadError("Too many source redirects")

def _put_part(session: requests.Session, url: str, data: bytes, part_number: int) -> str:
    if not _mocha_storage_url_allowed(url):
        raise UploadError("Mocha returned a presigned URL outside the storage host allowlist")
    response = session.put(url, data=data, headers={"Content-Type": "application/octet-stream"},
                           timeout=TIMEOUT, allow_redirects=False)
    try:
        if response.status_code < 200 or response.status_code >= 300:
            raise UploadError(f"Mocha storage rejected part {part_number} (HTTP {response.status_code})")
        etag = response.headers.get("ETag", "").strip().strip('"')
        if not etag:
            raise UploadError(f"Mocha storage omitted ETag for part {part_number}")
        return etag
    finally:
        response.close()


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

    # Require the full fixed-size PS4 PKG header, not merely its four-byte magic.
    header, total_size, validator = _read_source_range(client, source_url, 0, 0x100)
    if total_size < 0x100 or len(header) != 0x100 or header[:4] != bytes.fromhex("7f434e54"):
        raise UploadError("Source is not a complete PS4 PKG header")

    upload_id = key = node_id = None
    original_name = filename
    completion_started = False
    try:
        init = _request_json(client, "POST", "/files/multipart/init", api_key, {
            "originalName": filename,
            "size": total_size,
            "mimeType": "application/octet-stream",
            "path": remote_path,
        })
        upload_id, key, node_id = init.get("uploadId"), init.get("key"), init.get("nodeId")
        original_name = init.get("originalName") or filename
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
                client, source_url, offset, length, expected_validator=validator
            )
            if observed_size != total_size or observed_validator != validator:
                raise UploadError("Source package changed during transfer")

            urls_payload = _request_json(client, "POST", "/files/multipart/presigned", api_key, {
                "uploadId": upload_id, "key": key, "nodeId": node_id,
                "originalName": original_name, "path": remote_path,
                "partNumbers": [part_number],
            })
            urls = urls_payload.get("urls")
            if not isinstance(urls, list) or len(urls) != 1 or not isinstance(urls[0], dict):
                raise UploadError(f"Mocha did not return one presigned URL for part {part_number}")
            if type(urls[0].get("partNumber")) is not int or urls[0]["partNumber"] != part_number:
                raise UploadError(f"Mocha returned a mismatched presigned part number for part {part_number}")
            part_url = urls[0].get("url")
            if not isinstance(part_url, str) or not part_url:
                raise UploadError(f"Mocha returned an invalid presigned URL for part {part_number}")
            etag = _put_part(client, part_url, data, part_number)
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
            })
        except Exception as exc:
            raise UploadError(
                "Mocha completion outcome is unknown. The current Mocha API exposes no "
                "upload-session status/idempotency lookup; do not retry this upload blindly."
            ) from exc
        file_obj = complete.get("file")
        file_id = file_obj.get("id") if isinstance(file_obj, dict) else None
        if not isinstance(file_id, str) or not file_id:
            raise UploadError(
                "Mocha completion response omitted the file ID. The upload may have "
                "committed; do not retry until the destination is checked manually."
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
