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

API_BASE = "https://api.mocha.my"
TIMEOUT = (15, 180)
MAX_REDIRECTS = 6
MAX_PART_BYTES = 128 * 1024 * 1024
SOURCE_HOSTS = {"archive.org", "huggingface.co", "hf.co"}
RETRYABLE = {408, 425, 429, 500, 502, 503, 504}


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
                       length: int) -> tuple[bytes, int]:
    if not _source_host_allowed(url):
        raise UploadError("Source URL or redirect host is not allowlisted")
    end = start + length - 1
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        if not _source_host_allowed(current):
            raise UploadError("Source redirect host is not allowlisted")
        response = session.get(current, headers={
            "Range": f"bytes={start}-{end}",
            "Accept-Encoding": "identity",
            "User-Agent": "FPKGi-Mocha-PKG-Uploader/1.0",
        }, timeout=TIMEOUT, allow_redirects=False, stream=True)
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
            match = re.fullmatch(r"bytes (\\d+)-(\\d+)/(\\d+)", response.headers.get("Content-Range", "").strip())
            if not match or (int(match.group(1)), int(match.group(2))) != (start, end):
                raise UploadError("Source returned an invalid Content-Range")
            total = int(match.group(3))
            if total <= end:
                raise UploadError("Source Content-Range total is inconsistent")
            data = bytearray()
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    data.extend(chunk)
                    if len(data) > length:
                        raise UploadError("Source returned more bytes than requested")
            if len(data) != length:
                raise UploadError("Source returned fewer bytes than requested")
            return bytes(data), total
        finally:
            response.close()
    raise UploadError("Too many source redirects")


def _put_part(session: requests.Session, url: str, data: bytes, part_number: int) -> str:
    if not _valid_https_url(url):
        raise UploadError("Mocha returned an invalid presigned part URL")
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

    # Probe one byte first: this also proves the origin supports exact Range reads.
    first, total_size = _read_source_range(client, source_url, 0, 1)
    if first[:4] == b"":
        raise UploadError("Empty source package")

    init = _request_json(client, "POST", "/files/multipart/init", api_key, {
        "originalName": filename,
        "size": total_size,
        "mimeType": "application/octet-stream",
        "path": remote_path,
    })
    upload_id, key, node_id = init.get("uploadId"), init.get("key"), init.get("nodeId")
    original_name = init.get("originalName") or filename
    part_size = init.get("partSizeBytes")
    if not all(isinstance(v, str) and v for v in (upload_id, key, node_id)):
        raise UploadError("Mocha multipart init response is incomplete")
    if type(part_size) is not int or not 0 < part_size <= MAX_PART_BYTES:
        raise UploadError("Mocha returned an unsafe or unsupported part size")

    completed_parts: list[dict[str, Any]] = []
    try:
        part_count = (total_size + part_size - 1) // part_size
        for part_number in range(1, part_count + 1):
            offset = (part_number - 1) * part_size
            length = min(part_size, total_size - offset)
            data = None
            for attempt in range(4):
                try:
                    data, observed_size = _read_source_range(client, source_url, offset, length)
                    if observed_size != total_size:
                        raise UploadError("Source package size changed during transfer")
                    break
                except (requests.RequestException, UploadError):
                    if attempt == 3:
                        raise
                    time.sleep(min(2 ** attempt, 8))
            urls_payload = _request_json(client, "POST", "/files/multipart/presigned", api_key, {
                "uploadId": upload_id, "key": key, "nodeId": node_id,
                "originalName": original_name, "path": remote_path,
                "partNumbers": [part_number],
            })
            urls = urls_payload.get("urls")
            if not isinstance(urls, list) or len(urls) != 1 or not isinstance(urls[0], dict):
                raise UploadError(f"Mocha did not return one presigned URL for part {part_number}")
            part_url = urls[0].get("url")
            last_error = None
            for attempt in range(4):
                try:
                    etag = _put_part(client, part_url, data, part_number)
                    last_error = None
                    break
                except (requests.RequestException, UploadError) as exc:
                    last_error = exc
                    if attempt < 3:
                        time.sleep(min(2 ** attempt, 8))
            if last_error:
                raise last_error
            completed_parts.append({"partNumber": part_number, "etag": etag})
            print(f"Uploaded part {part_number}/{part_count} ({offset + length:,}/{total_size:,} bytes)",
                  file=sys.stderr, flush=True)

        complete = _request_json(client, "POST", "/files/multipart/complete", api_key, {
            "uploadId": upload_id, "key": key, "nodeId": node_id,
            "originalName": original_name, "path": remote_path,
            "mimeType": "application/octet-stream", "size": total_size,
            "parts": completed_parts,
        })
        file_obj = complete.get("file")
        file_id = file_obj.get("id") if isinstance(file_obj, dict) else None
        if not isinstance(file_id, str) or not file_id:
            raise UploadError("Mocha completed the upload but returned no file ID")
        return {"file_id": file_id, "filename": filename, "size": total_size,
                "remote_path": remote_path, "parts": part_count}
    except Exception:
        # Abort is best-effort; never mask the original failure.
        try:
            _request_json(client, "POST", "/files/multipart/abort", api_key, {
                "uploadId": upload_id, "key": key, "nodeId": node_id,
                "originalName": original_name, "path": remote_path,
            })
        except Exception:
            pass
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
