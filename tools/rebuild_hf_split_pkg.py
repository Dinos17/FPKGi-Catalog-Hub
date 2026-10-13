import os
import time

import requests
import hf_xet
from huggingface_hub import HfApi, CommitOperationAdd, CommitOperationDelete
from huggingface_hub.lfs import UploadInfo
from huggingface_hub._commit_api import _fetch_upload_modes, _send_commit

REPO_ID = "dinos17/PS-Games-Dataset"
REPO_TYPE = "dataset"
TARGET_PATH = "Game/9nV1JUjHhJ"
PART_PATHS = [
    "Game/9nV1JUjHhJ.part001",
    "Game/9nV1JUjHhJ.part002",
]
EXPECTED_SIZE = 85_290_778_624
CHUNK_BYTES = 4 * 1024 * 1024
ENDPOINT = "https://huggingface.co"


def main():
    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        raise SystemExit("HF_TOKEN secret is missing.")

    api = HfApi(endpoint=ENDPOINT, token=token)
    headers = api._build_hf_headers(token=token)
    refresh_url = (
        f"{ENDPOINT}/api/datasets/{REPO_ID}/xet-write-token/main"
    )

    print("Checking that both published parts exist and have the expected sizes.", flush=True)
    paths = api.get_paths_info(
        repo_id=REPO_ID,
        paths=PART_PATHS,
        repo_type=REPO_TYPE,
        revision="main",
    )
    if len(paths) != 2:
        raise RuntimeError(f"Expected 2 part files, found {len(paths)}.")
    sizes = [getattr(item, "size", None) for item in paths]
    if sizes != [42_645_389_312, 42_645_389_312]:
        raise RuntimeError(f"Unexpected part sizes: {sizes!r}")
    if sum(sizes) != EXPECTED_SIZE:
        raise RuntimeError(f"Parts total {sum(sizes)} bytes, expected {EXPECTED_SIZE}.")

    # Seed the commit operation with correct size and sample metadata before
    # asking the Hub which storage mode to use. The empty placeholder is never
    # uploaded; Xet receives the actual reconstructed byte stream below.
    sample_response = requests.get(
        f"{ENDPOINT}/datasets/{REPO_ID}/resolve/main/{PART_PATHS[0]}",
        headers=headers,
        stream=True,
        timeout=(30, 600),
    )
    sample_response.raise_for_status()
    try:
        sample = next(sample_response.iter_content(chunk_size=512))
        if not sample:
            raise RuntimeError("Could not read the first bytes of part 1.")
        sample = sample[:512]
    finally:
        sample_response.close()

    operation = CommitOperationAdd(path_in_repo=TARGET_PATH, path_or_fileobj=b"")
    operation.upload_info = UploadInfo(
        size=EXPECTED_SIZE,
        sample=sample,
        sha256=b"\x00" * 32,
    )
    _fetch_upload_modes(
        additions=[operation],
        repo_type=REPO_TYPE,
        repo_id=REPO_ID,
        headers=headers,
        revision="main",
        endpoint=ENDPOINT,
    )
    if operation._upload_mode != "lfs":
        raise RuntimeError(
            f"Hub selected unexpected upload mode {operation._upload_mode!r}; refusing to continue."
        )

    print("Starting native Xet streaming upload; no full PKG is written to disk.", flush=True)
    session = hf_xet.XetSession()
    with session.new_upload_commit(
        token_refresh_url=refresh_url,
        token_refresh_headers=headers,
    ) as xet_commit:
        stream = xet_commit.start_upload_stream(name=TARGET_PATH)
        bytes_sent = 0
        start_time = time.monotonic()
        last_log = start_time
        for part_path in PART_PATHS:
            url = f"{ENDPOINT}/datasets/{REPO_ID}/resolve/main/{part_path}"
            response = requests.get(
                url,
                headers=headers,
                stream=True,
                timeout=(30, 600),
            )
            response.raise_for_status()
            try:
                for block in response.iter_content(chunk_size=CHUNK_BYTES):
                    if not block:
                        continue
                    stream.write(block)
                    bytes_sent += len(block)
                    now = time.monotonic()
                    if now - last_log >= 30:
                        elapsed = max(now - start_time, 1)
                        rate = bytes_sent / elapsed / (1024 * 1024)
                        print(
                            f"Streamed {bytes_sent:,}/{EXPECTED_SIZE:,} bytes "
                            f"({bytes_sent * 100 / EXPECTED_SIZE:.1f}%; {rate:.1f} MiB/s)",
                            flush=True,
                        )
                        last_log = now
            finally:
                response.close()
        if bytes_sent != EXPECTED_SIZE:
            stream.abort()
            raise RuntimeError(
                f"Streamed {bytes_sent:,} bytes, expected {EXPECTED_SIZE:,}; "
                "published parts have been left untouched."
            )
        metadata = stream.finish()
        xet_commit.wait_to_finish()

    sha256_hex = metadata.xet_info.sha256
    if len(sha256_hex) != 64:
        raise RuntimeError(f"Xet returned an invalid SHA-256: {sha256_hex!r}")
    operation.upload_info = UploadInfo(
        size=EXPECTED_SIZE,
        sample=sample,
        sha256=bytes.fromhex(sha256_hex),
    )
    operation._is_uploaded = True

    print("Xet upload completed. Publishing the single-file commit while keeping both parts.", flush=True)
    _send_commit(
        operations=[operation],
        files_to_copy={},
        commit_message="Publish reconstructed Red Dead Redemption 2 PKG",
        commit_description="Reconstructed in a streaming Xet upload from the two existing dataset parts; no full local PKG file.",
        repo_type=REPO_TYPE,
        repo_id=REPO_ID,
        headers=headers,
        revision="main",
        endpoint=ENDPOINT,
        retry_on_error=False,
    )

    print("Verifying the published single file before deleting any parts.", flush=True)
    target_info = api.get_paths_info(
        repo_id=REPO_ID,
        paths=[TARGET_PATH],
        repo_type=REPO_TYPE,
        revision="main",
    )
    if len(target_info) != 1 or getattr(target_info[0], "size", None) != EXPECTED_SIZE:
        actual = [getattr(item, "size", None) for item in target_info]
        raise RuntimeError(
            f"Published file size verification failed: {actual!r}. "
            "Both original parts are intentionally retained."
        )

    print("Single-file size verified. Removing the two parts in a separate commit.", flush=True)
    api.create_commit(
        repo_id=REPO_ID,
        repo_type=REPO_TYPE,
        operations=[CommitOperationDelete(path_in_repo=path) for path in PART_PATHS],
        commit_message="Remove split parts after verifying reconstructed PKG",
    )

    remaining = set(api.list_repo_files(repo_id=REPO_ID, repo_type=REPO_TYPE))
    if TARGET_PATH not in remaining or any(path in remaining for path in PART_PATHS):
        raise RuntimeError(
            "Post-cleanup verification failed; check the repository contents manually."
        )
    print(
        f"SUCCESS: {REPO_ID}/{TARGET_PATH} is published as a single "
        f"{EXPECTED_SIZE:,}-byte file; split parts removed after verification.",
        flush=True,
    )


if __name__ == "__main__":
    main()
