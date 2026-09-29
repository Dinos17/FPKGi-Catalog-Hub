import io
import os
import re
import struct
import time
from urllib.parse import unquote, urlparse

import requests
from huggingface_hub import CommitOperationAdd, HfApi

COLLECTIONS = {
    "ps4-fpkg-collection-english-t": "ps4-fpkg-collection-english-t",
    "ps4-fpkg-collection-english-d": "ps4-fpkg-collection-english-d",
}

CATEGORY_MAP = {
    "gd": "games",
    "gda": "apps",
    "gdc": "apps",
    "gdd": "apps",
    "gde": "apps",
    "gdg": "apps",
    "gdk": "apps",
    "gdl": "apps",
    "ac": "dlc",
    "gaddon": "dlc",
    "gp": "updates",
    "gpatch": "updates",
    "gup": "updates",
    "gpc": "updates",
    "gpd": "updates",
    "gpe": "updates",
    "gpk": "updates",
    "gpl": "updates",
}

def archive_pkg_urls(session, identifier):
    response = session.get(
        f"https://archive.org/metadata/{identifier}",
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    urls = []

    # Archive.org stores the download hosts and item directory at the
    # top level of the metadata response, not on each file record.
    directory = str(payload.get("dir") or "").strip()
    servers = []
    for value in (
        payload.get("server"),
        payload.get("d1"),
        payload.get("d2"),
    ):
        value = str(value or "").strip()
        if value and value not in servers:
            servers.append(value)

    for value in payload.get("workable_servers") or []:
        value = str(value or "").strip()
        if value and value not in servers:
            servers.append(value)

    for record in payload.get("files", []):
        name = str(record.get("name") or "")
        if not name.lower().endswith(".pkg"):
            continue

        # Try the normal Archive.org URL plus every usable storage server
        # advertised by the metadata. The direct storage URLs avoid relying
        # on the regional download redirect that has been returning HTTP 500.
        candidates = [
            "https://archive.org/download/"
            f"{identifier}/{name}"
        ]
        if directory:
            for server in servers:
                direct_url = f"https://{server}{directory}/{name}"
                if direct_url not in candidates:
                    candidates.append(direct_url)

        urls.append(candidates)
    return urls

class RemoteRangeFile(io.BufferedIOBase):
    def __init__(self, url, size):
        self.url = url
        self.size = size
        self.pos = 0
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "FPKGi-Catalog-Hub/1.0"}
        )

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
            new_pos = self.size + offset
        else:
            raise ValueError("invalid whence")
        if new_pos < 0 or new_pos > self.size:
            raise ValueError("seek outside remote file")
        self.pos = new_pos
        return self.pos

    def read(self, length=-1):
        if length is None or length < 0:
            length = self.size - self.pos
        if length == 0 or self.pos >= self.size:
            return b""

        end = min(self.pos + length, self.size) - 1
        response = self.session.get(
            self.url,
            headers={"Range": f"bytes={self.pos}-{end}"},
            timeout=180,
        )
        response.raise_for_status()

        expected = end - self.pos + 1
        content_range = response.headers.get("Content-Range", "")
        expected_range = f"bytes {self.pos}-{end}/{self.size}"
        if response.status_code != 206 or content_range != expected_range:
            raise RuntimeError(
                f"Range request was not honored: status={response.status_code}, "
                f"Content-Range={content_range!r}, expected={expected_range!r}"
            )

        data = response.content
        if len(data) != expected:
            raise RuntimeError(
                f"Range read mismatch: expected {expected}, got {len(data)}"
            )
        self.pos += len(data)
        return data

def parse_sfo(data):
    magic, _, key_offset, data_offset, count = struct.unpack_from(
        "<5I", data, 0
    )
    if magic != 0x46535000:
        raise ValueError("Invalid PARAM.SFO.")

    result = {}
    for index in range(count):
        offset = 20 + index * 16
        key_index, fmt, value_len, _, value_offset = struct.unpack_from(
            "<HHIII", data, offset
        )
        key_start = key_offset + key_index
        key_end = data.find(b"\x00", key_start)
        key = data[key_start:key_end].decode("utf-8", errors="replace")
        raw = data[
            data_offset + value_offset:
            data_offset + value_offset + value_len
        ]
        if fmt & 0xFF == 0x04 and len(raw) >= 4:
            result[key] = int.from_bytes(raw[:4], "little")
        else:
            result[key] = raw.rstrip(b"\x00").decode(
                "utf-8", errors="replace"
            )
    return result

def classify_application(session, title_id):
    if not title_id:
        return "apps", None

    for source_name in ("applications", "homebrew"):
        url = (
            "https://raw.githubusercontent.com/ohhsodead/"
            f"arisen-studio-database/main/PS4/{source_name}.json"
        )
        try:
            response = session.get(url, timeout=60)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            print(f"Classification source unavailable: {source_name}: {exc}")
            continue

        for record in payload.get("Mods", []):
            if str(record.get("TitleId") or "").strip().upper() != title_id:
                continue

            category_id = str(record.get("CategoryId") or "").strip().lower()
            if category_id in {"emu", "emulator"}:
                return "emulators", None
            if category_id == "media":
                return "apps", "Media"
            if category_id in {"util", "utili", "utility"}:
                return "apps", "Utility"
            if source_name == "homebrew":
                return "homebrew", None
            return "apps", None

    return "apps", None

def inspect_remote_pkg(session, source_url):
    probe = session.get(
        source_url,
        allow_redirects=True,
        timeout=60,
        headers={"Range": "bytes=0-0"},
    )
    probe.raise_for_status()

    final_url = probe.url
    content_range = probe.headers.get("Content-Range", "")
    match = re.fullmatch(r"bytes 0-0/(\d+)", content_range)
    if not match:
        raise RuntimeError(
            f"Source did not return a valid Content-Range: {content_range!r}"
        )

    size = int(match.group(1))
    filename = unquote(os.path.basename(urlparse(final_url).path))
    if not filename or size <= 0:
        raise RuntimeError("Could not determine remote filename or size.")

    remote = RemoteRangeFile(final_url, size)

    def read_at(offset, length):
        remote.seek(offset)
        return remote.read(length)

    if read_at(0, 4) != bytes.fromhex("7F504B47"):
        raise ValueError("Source is not a valid PS4 PKG.")

    header = read_at(0, 0x1000)
    entry_count = struct.unpack_from(">I", header, 0x10)[0]
    table_offset = struct.unpack_from(">I", header, 0x18)[0]
    if entry_count <= 0 or table_offset >= size:
        raise ValueError("Invalid PKG file table.")

    table = read_at(table_offset, entry_count * 0x20)
    sfo_offset = None
    sfo_size = None

    for index in range(entry_count):
        entry = table[index * 0x20:(index + 1) * 0x20]
        entry_id, _, _, _, offset, entry_size = struct.unpack_from(
            ">IIIIII", entry, 0
        )
        if entry_id == 0x1000:
            sfo_offset = offset
            sfo_size = entry_size
            break

    if sfo_offset is None or sfo_size is None:
        raise ValueError("PKG does not contain PARAM.SFO.")

    params = parse_sfo(read_at(sfo_offset, sfo_size))
    title_id = str(params.get("TITLE_ID") or "").strip().upper()
    title = str(params.get("TITLE") or filename).strip()
    pkg_category = str(params.get("CATEGORY") or "").strip().lower()

    category = CATEGORY_MAP.get(pkg_category)
    if not category:
        raise ValueError(
            f"Unknown PKG CATEGORY {pkg_category!r}; refusing to guess."
        )

    subcategory = None
    if category == "apps":
        category, subcategory = classify_application(session, title_id)

    return {
        "url": final_url,
        "filename": filename,
        "size": size,
        "title_id": title_id,
        "title": title,
        "pkg_category": pkg_category,
        "category": category,
        "subcategory": subcategory,
        "remote": remote,
    }

def destination(repo_override, requested_path, info):
    category = info["category"]
    subcategory = info["subcategory"]
    filename = info["filename"]

    if requested_path:
        return repo_override, requested_path.strip("/")

    if category == "games":
        folder = "Game"
    elif category == "apps":
        folder = subcategory or "Application"
    elif category == "emulators":
        folder = "Emulator"
    elif category == "homebrew":
        folder = "Homebrew"
    elif category == "dlc":
        folder = "DLC"
    elif category == "updates":
        folder = "Update"
    else:
        raise ValueError(f"No destination folder for category {category!r}")

    repo = repo_override or (
        "dinos17/PS-Games-Dataset"
        if category == "games"
        else "dinos17/PS-Applications"
    )
    return repo, f"{folder}/{filename}"

def main():
    source_collection = os.environ["SOURCE_COLLECTION"]
    token = os.environ.get("HF_TOKEN", "")
    repo_override = os.environ.get("HF_REPO", "").strip()
    requested_path = os.environ.get("HF_PATH", "").strip()

    if not token:
        raise SystemExit("HF_TOKEN repository secret is not configured.")

    if source_collection == "both":
        identifiers = list(COLLECTIONS.values())
    elif source_collection in COLLECTIONS:
        identifiers = [COLLECTIONS[source_collection]]
    else:
        raise SystemExit(f"Unknown Archive.org collection: {source_collection}")

    session = requests.Session()
    session.headers.update({"User-Agent": "FPKGi-Catalog-Hub/1.0"})

    source_urls = []
    seen_files = set()
    for identifier in identifiers:
        url_candidates = archive_pkg_urls(session, identifier)
        print(f"{identifier}: found {len(url_candidates)} PKG files")
        for candidates in url_candidates:
            key = tuple(candidates)
            if key not in seen_files:
                seen_files.add(key)
                source_urls.append(candidates)

    print(f"Total unique Archive.org PKG URLs: {len(source_urls)}")

    api = HfApi(token=token)
    uploaded = 0
    skipped = 0

    max_attempts = 50
    retry_delay = 5

    for index, source_url_candidates in enumerate(source_urls, 1):
        print("\n" + "=" * 80)
        print(f"[{index}/{len(source_urls)}] {source_url_candidates[0]}")

        completed = False
        last_error = None

        for attempt in range(1, max_attempts + 1):
            source_url = source_url_candidates[(attempt - 1) % len(source_url_candidates)]
            print(f"Attempt {attempt}/{max_attempts} via {source_url}")

            try:
                # Recreate the remote stream on every attempt so a failed
                # range request or upload never leaves us with a consumed
                # file-like object.
                info = inspect_remote_pkg(session, source_url)
                repo_id, path_in_repo = destination(
                    repo_override, requested_path, info
                )

                print(f"Title: {info['title']}")
                print(f"Title ID: {info['title_id']}")
                print(f"PKG CATEGORY: {info['pkg_category']}")
                print(f"Resolved category: {info['category']}")
                print(f"Remote size: {info['size']:,} bytes")
                print(f"HF destination: {repo_id}/{path_in_repo}")
                print("Streaming directly from Archive.org to Hugging Face.")

                operation = CommitOperationAdd(
                    path_in_repo=path_in_repo,
                    path_or_fileobj=info["remote"],
                )
                api.create_commit(
                    repo_id=repo_id,
                    repo_type="dataset",
                    operations=[operation],
                    commit_message=f"Upload {info['filename']}",
                )
                print(f"Uploaded: {repo_id}/{path_in_repo}")
                uploaded += 1
                completed = True
                break
            except Exception as exc:
                last_error = exc
                print(f"FAILED: {exc}")
                if attempt < max_attempts:
                    print(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)

        if not completed:
            print(f"SKIP after {max_attempts} attempts: {last_error}")
            skipped += 1

    print("\n" + "=" * 80)
    print(f"Finished. Uploaded: {uploaded} | Skipped/failed: {skipped}")

if __name__ == "__main__":
    main()
