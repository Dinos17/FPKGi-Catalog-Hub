#!/usr/bin/env python3
"""Create Mocha permanent links for explicitly selected file IDs.

This utility is opt-in. It never modifies catalog JSON or resolves public URLs.
A private local state file prevents repeat creation for IDs already confirmed.
Ambiguous network outcomes are recorded as unknown and are not retried silently.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import requests

API_URL = "https://api.mocha.my/api/permanent-links"
CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = 2
OVERALL_TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 64 * 1024
MAX_FILE_IDS = 100


class PermanentLinkError(RuntimeError):
    """Raised when Mocha cannot confirm permanent-link creation."""

    def __init__(self, message: str, *, outcome_unknown: bool = False):
        super().__init__(message)
        self.outcome_unknown = outcome_unknown


class MochaPermanentLinkClient:
    def __init__(self, api_key: str, session: requests.Session | None = None):
        if not api_key:
            raise PermanentLinkError("MOCHA_API_KEY is not configured")
        self.session = session or requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "FPKGi-Catalog-Hub-Mocha-Permanent-Link/1.0",
        })

    def create(self, file_id: str) -> dict[str, Any]:
        if not isinstance(file_id, str) or not file_id.strip():
            raise PermanentLinkError("Invalid Mocha file ID")
        deadline = time.monotonic() + OVERALL_TIMEOUT_SECONDS
        response = None
        try:
            response = self.session.post(
                API_URL,
                json={"fileId": file_id},
                timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
                allow_redirects=False,
                stream=True,
            )
            if 300 <= response.status_code < 400:
                raise PermanentLinkError(
                    "Mocha permanent-link API unexpectedly redirected; refusing "
                    "to forward credentials"
                )
            if response.status_code >= 500:
                raise PermanentLinkError(
                    f"Mocha permanent-link API returned HTTP {response.status_code}; "
                    "creation outcome is treated as unknown",
                    outcome_unknown=True,
                )
            if response.status_code >= 400:
                raise PermanentLinkError(
                    f"Mocha permanent-link API returned HTTP {response.status_code}"
                )

            body = bytearray()
            for chunk in response.iter_content(chunk_size=4096):
                if time.monotonic() > deadline:
                    raise PermanentLinkError(
                        "Mocha response exceeded the overall deadline",
                        outcome_unknown=True,
                    )
                if not chunk:
                    continue
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise PermanentLinkError(
                        f"Mocha response exceeded {MAX_RESPONSE_BYTES} bytes",
                        outcome_unknown=True,
                    )
            if time.monotonic() > deadline:
                raise PermanentLinkError(
                    "Mocha response exceeded the overall deadline",
                    outcome_unknown=True,
                )
        except PermanentLinkError:
            raise
        except requests.RequestException as exc:
            raise PermanentLinkError(
                f"Mocha permanent-link request failed ({type(exc).__name__}); "
                "creation status may be unknown",
                outcome_unknown=True,
            ) from exc
        finally:
            if response is not None:
                response.close()

        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise PermanentLinkError(
                "Mocha returned invalid JSON; creation outcome is unknown",
                outcome_unknown=True,
            ) from exc

        link = payload.get("link") if isinstance(payload, dict) else None
        if not isinstance(link, dict):
            raise PermanentLinkError(
                "Mocha response is missing the link object; creation outcome is unknown",
                outcome_unknown=True,
            )
        token = link.get("token")
        returned_file_id = link.get("file_id")
        permanent = link.get("is_permanent_link")
        if not isinstance(token, str) or not token.strip():
            raise PermanentLinkError(
                "Mocha response is missing a valid link token; creation outcome is unknown",
                outcome_unknown=True,
            )
        if returned_file_id is None or not isinstance(returned_file_id, (str, int)):
            raise PermanentLinkError(
                "Mocha response is missing a valid file ID; creation outcome is unknown",
                outcome_unknown=True,
            )
        if str(returned_file_id) != file_id:
            raise PermanentLinkError(
                "Mocha response file ID does not match the request; creation outcome is unknown",
                outcome_unknown=True,
            )
        if permanent is not True:
            raise PermanentLinkError(
                "Mocha did not confirm this is a permanent link; creation outcome is unknown",
                outcome_unknown=True,
            )

        return {
            "file_id": file_id,
            "token": token,
            "is_permanent_link": True,
            "creation_confirmed": True,
        }


def _write_private_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def create_links(
    file_ids: list[str],
    client: MochaPermanentLinkClient,
    state: dict[str, Any] | None = None,
    persist_state=None,
    persist_report=None,
) -> dict[str, Any]:
    normalized = list(dict.fromkeys(
        value.strip() for value in file_ids
        if isinstance(value, str) and value.strip()
    ))
    if not normalized:
        raise PermanentLinkError("No Mocha file IDs were supplied")
    if len(normalized) > MAX_FILE_IDS:
        raise PermanentLinkError(f"Refusing more than {MAX_FILE_IDS} link creations in one run")

    state = state if state is not None else {"files": {}}
    state.setdefault("files", {})
    results: list[dict[str, Any]] = []

    def snapshot() -> dict[str, Any]:
        return {
            "service": "mocha",
            "operation": "create_permanent_links",
            "requested_count": len(normalized),
            "created_count": sum(row["status"] in ("created", "already_created") for row in results),
            "failed_count": sum(row["status"] in ("error", "unknown") for row in results),
            "catalog_write_performed": False,
            "public_url_resolved": False,
            "results": list(results),
        }

    for file_id in normalized:
        previous = state["files"].get(file_id)
        if isinstance(previous, dict) and previous.get("status") == "created":
            results.append({
                "status": "already_created",
                "file_id": file_id,
                "token": previous["token"],
                "is_permanent_link": True,
                "creation_confirmed": True,
            })
        elif isinstance(previous, dict) and previous.get("status") == "unknown":
            results.append({
                "status": "unknown",
                "file_id": file_id,
                "error": "Previous attempt had an ambiguous outcome; refusing automatic retry",
            })
        else:
            try:
                created = client.create(file_id)
                state["files"][file_id] = {
                    "status": "created",
                    "token": created["token"],
                    "is_permanent_link": True,
                }
                results.append({"status": "created", **created})
            except PermanentLinkError as exc:
                status = "unknown" if exc.outcome_unknown else "error"
                state["files"][file_id] = {"status": status, "error": str(exc)}
                results.append({"status": status, "file_id": file_id, "error": str(exc)})
            if persist_state:
                persist_state(state)
        if persist_report:
            persist_report(snapshot())

    return snapshot()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file-id", action="append", required=True, dest="file_ids",
                        help="Mocha file ID; may be supplied more than once")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--state",
        type=Path,
        default=Path.home() / ".local/state/fpkgi-catalog-hub/mocha-permanent-links.json",
        help="Private persistent state file (default: user state directory)",
    )
    args = parser.parse_args(argv)

    api_key = os.environ.get("MOCHA_API_KEY", "").strip()
    if not api_key:
        print("ERROR: MOCHA_API_KEY is not configured", file=sys.stderr)
        return 2

    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.state.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = args.state.with_name(args.state.name + ".lock")
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    os.fchmod(lock_fd, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            output_fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            print("ERROR: output file already exists; choose a new --output path", file=sys.stderr)
            return 2
        os.fchmod(output_fd, 0o600)
        os.close(output_fd)

        if args.state.exists():
            try:
                state = json.loads(args.state.read_text(encoding="utf-8"))
                if not isinstance(state, dict) or not isinstance(state.get("files"), dict):
                    raise ValueError("invalid state structure")
            except (OSError, ValueError) as exc:
                print(f"ERROR: cannot read state file: {exc}", file=sys.stderr)
                return 2
        else:
            state = {"files": {}}

        def persist_state(value):
            _write_private_json(args.state, value)

        def persist_report(value):
            _write_private_json(args.output, value)

        try:
            report = create_links(
                args.file_ids,
                MochaPermanentLinkClient(api_key),
                state=state,
                persist_state=persist_state,
                persist_report=persist_report,
            )
        except PermanentLinkError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

        persist_report(report)
        print(
            f"Permanent-link creation: {report['created_count']} confirmed/already known, "
            f"{report['failed_count']} failed or unknown. Tokens are stored in private files."
        )
        return 0 if report["failed_count"] == 0 else 1
    finally:
        os.close(lock_fd)


if __name__ == "__main__":
    raise SystemExit(main())
