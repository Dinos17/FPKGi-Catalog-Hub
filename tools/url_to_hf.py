import hashlib
import io
import os
from urllib.parse import quote, urlparse

import requests
from huggingface_hub import CommitOperationAdd, CommitOperationDelete, HfApi, hf_hub_url

try:
    from .archive_to_hf import destination, inspect_remote_pkg
except ImportError:  # direct execution: python tools/url_to_hf.py
    from archive_to_hf import destination, inspect_remote_pkg

# Each durable chunk is committed to the target dataset. A rerun skips chunks
# already present, so GitHub Actions' job time limit does not restart the source
# transfer from byte zero.
CHUNK_SIZE = 512 * 1024 * 1024
STAGING_ROOT = ".__upload_staging__"

def set_complete_output():
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as output:
            output.write("complete=true\n")


class StagedCompositeFile(io.BufferedIOBase):
    """Seekable view of ordered Hugging Face staging chunks without disk usage."""

    def __init__(self, repo_id, repo_type, paths, sizes, token, total_size):
        self.repo_id = repo_id
        self.repo_type = repo_type
        self.paths = paths
        self.sizes = sizes
        self.total_size = total_size
        self.pos = 0
        self.index_starts = []
        offset = 0
        for size in sizes:
            self.index_starts.append(offset)
            offset += size
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}"})
        self._cached_index = None
        self._cached_data = b""

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=0):
        if whence == 0:
            new_pos = offset
        elif whence == 1:
            new_pos = self.pos + offset
        elif whence == 2:
            new_pos = self.total_size + offset
        else:
            raise ValueError("invalid whence")
        if new_pos < 0 or new_pos > self.total_size:
            raise ValueError("seek outside staged composite file")
        self.pos = new_pos
        return self.pos

    def _load_part(self, index):
        if self._cached_index == index:
            return
        url = hf_hub_url(
            repo_id=self.repo_id,
            filename=self.paths[index],
            repo_type=self.repo_type,
        )
        response = self.session.get(url, timeout=(30, 600))
        response.raise_for_status()
        data = response.content
        if len(data) != self.sizes[index]:
            raise RuntimeError(
                f"Staging part size mismatch for {self.paths[index]}: "
                f"expected {self.sizes[index]}, got {len(data)}"
            )
        self._cached_index = index
        self._cached_data = data

    def read(self, length=-1):
        if length is None or length < 0:
            length = self.total_size - self.pos
        length = min(length, self.total_size - self.pos)
        if length <= 0:
            return b""

        output = bytearray()
        while length:
            index = len(self.index_starts) - 1
            for candidate, start in enumerate(self.index_starts):
                if start <= self.pos < start + self.sizes[candidate]:
                    index = candidate
                    break
            self._load_part(index)
            local_offset = self.pos - self.index_starts[index]
            take = min(length, self.sizes[index] - local_offset)
            output.extend(self._cached_data[local_offset:local_offset + take])
            self.pos += take
            length -= take
        return bytes(output)


def main():
    source_url = os.environ.get("SOURCE_URL", "").strip()
    token = os.environ.get("HF_TOKEN", "").strip()
    repo_override = os.environ.get("HF_REPO", "").strip()
    requested_path = os.environ.get("HF_PATH", "").strip()

    parsed = urlparse(source_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise SystemExit("SOURCE_URL must be a valid HTTPS download URL.")
    if not token:
        raise SystemExit("HF_TOKEN repository secret is not configured.")

    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Referer": "https://vikingfile.com/",
    })

    print("Inspecting remote PKG metadata.")
    info = inspect_remote_pkg(session, source_url)
    if requested_path and not repo_override:
        repo_override = (
            "dinos17/PS-Games-Dataset"
            if info["category"] == "games"
            else "dinos17/PS-Applications"
        )
    repo_id, path_in_repo = destination(repo_override, requested_path, info)

    # Bind checkpoints to the exact source, size, repo and destination.
    fingerprint = hashlib.sha256(
        f"{source_url}\n{info['size']}\n{repo_id}\n{path_in_repo}".encode("utf-8")
    ).hexdigest()[:24]
    staging_dir = f"{STAGING_ROOT}/{fingerprint}"
    marker_path = f"{staging_dir}/COMPLETE"
    part_count = (info["size"] + CHUNK_SIZE - 1) // CHUNK_SIZE
    part_paths = [
        f"{staging_dir}/part-{index:05d}.bin" for index in range(part_count)
    ]
    part_sizes = [
        min(CHUNK_SIZE, info["size"] - index * CHUNK_SIZE)
        for index in range(part_count)
    ]

    print(f"Title: {info['title']}")
    print(f"Title ID: {info['title_id']}")
    print(f"PKG category: {info['pkg_category']}")
    print(f"File size: {info['size']:,} bytes")
    print(f"Hugging Face destination: {repo_id}/{path_in_repo}")
    print(f"Resumable transfer: {part_count} chunks of up to {CHUNK_SIZE:,} bytes")
    print(f"Checkpoint directory: {staging_dir}")

    api = HfApi(token=token)
    existing = set(
        api.list_repo_files(repo_id=repo_id, repo_type="dataset")
    )
    if marker_path in existing and path_in_repo in existing:
        print("This exact source upload is already finalized; nothing to do.")
        set_complete_output()
        return
    missing = [i for i, path in enumerate(part_paths) if path not in existing]
    print(
        f"Checkpoint status: {part_count - len(missing)}/{part_count} chunks "
        "already stored; completed chunks will be skipped."
    )

    if missing:
        info["remote"].start_progress()

    for number in missing:
        start = number * CHUNK_SIZE
        length = part_sizes[number]
        print(
            f"Downloading and checkpointing chunk {number + 1}/{part_count}: "
            f"bytes {start:,}-{start + length - 1:,} ({length:,} bytes)"
        )
        info["remote"].seek(start)
        chunk = info["remote"].read(length)
        if len(chunk) != length:
            raise RuntimeError(
                f"Source chunk {number} was short: expected {length}, got {len(chunk)}"
            )
        api.create_commit(
            repo_id=repo_id,
            repo_type="dataset",
            operations=[
                CommitOperationAdd(
                    path_in_repo=part_paths[number],
                    path_or_fileobj=io.BytesIO(chunk),
                )
            ],
            commit_message=(
                f"Checkpoint upload chunk {number + 1}/{part_count} "
                f"for {info['filename']}"
            ),
        )
        print(f"Checkpoint saved: {part_paths[number]}")

    if missing:
        print()  # End the in-place terminal progress line.

    print(
        "All source chunks are safely stored on Hugging Face. "
        "Assembling the original file by streaming the stored checkpoints; no full local copy is created..."
    )
    composite = StagedCompositeFile(
        repo_id=repo_id,
        repo_type="dataset",
        paths=part_paths,
        sizes=part_sizes,
        token=token,
        total_size=info["size"],
    )
    operations = [
        CommitOperationAdd(
            path_in_repo=path_in_repo,
            path_or_fileobj=composite,
        )
    ]
    operations.extend(
        CommitOperationDelete(path_in_repo=path) for path in part_paths
    )
    marker_data = (
        "source=" + fingerprint + "\nsize=" + str(info["size"])
        + "\ndestination=" + repo_id + "/" + path_in_repo + "\n"
    ).encode("utf-8")
    operations.append(
        CommitOperationAdd(
            path_in_repo=marker_path,
            path_or_fileobj=io.BytesIO(marker_data),
        )
    )
    api.create_commit(
        repo_id=repo_id,
        repo_type="dataset",
        operations=operations,
        commit_message=f"Finalize resumable upload: {info['filename']}",
    )
    print(f"SUCCESS: https://huggingface.co/datasets/{repo_id}/blob/main/{quote(path_in_repo, safe='/')}")
    set_complete_output()


if __name__ == "__main__":
    main()
