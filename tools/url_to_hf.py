import os
from urllib.parse import urlparse

from huggingface_hub import CommitOperationAdd, HfApi

from archive_to_hf import destination, inspect_remote_pkg


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

    import requests
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Referer": "https://vikingfile.com/",
    })

    print("Inspecting the remote file and reading PKG metadata without saving it to GitHub.")
    info = inspect_remote_pkg(session, source_url)
    if requested_path and not repo_override:
        repo_override = (
            "dinos17/PS-Games-Dataset"
            if info["category"] == "games"
            else "dinos17/PS-Applications"
        )
    repo_id, path_in_repo = destination(repo_override, requested_path, info)

    print(f"Title: {info['title']}")
    print(f"Title ID: {info['title_id']}")
    print(f"PKG category: {info['pkg_category']}")
    print(f"File size: {info['size']:,} bytes")
    print(f"Hugging Face destination: {repo_id}/{path_in_repo}")
    print("Streaming the remote file directly into Hugging Face; no PKG file is stored in the GitHub repository.")

    api = HfApi(token=token)
    info["remote"].start_progress()
    operation = CommitOperationAdd(
        path_in_repo=path_in_repo,
        path_or_fileobj=info["remote"],
    )
    api.create_commit(
        repo_id=repo_id,
        repo_type="dataset",
        operations=[operation],
        commit_message=f"Upload {info['filename']} from supplied URL",
    )
    print(f"SUCCESS: https://huggingface.co/datasets/{repo_id}/blob/main/{path_in_repo}")


if __name__ == "__main__":
    main()
