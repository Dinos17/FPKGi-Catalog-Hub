import json
import os
import subprocess
from pathlib import Path

NEW_REGISTRATIONS_PATH = Path("new-registrations.json")
from pathlib import Path

files = subprocess.check_output(
    ["git", "diff", "--cached", "--name-only", "--", "*.json", "**/*.json"],
    text=True,
).splitlines()

added_total = 0
removed_total = 0
changed_total = 0
changed_files = []
new_records = {}

for filename in files:
    path = Path(filename)

    try:
        old_raw = subprocess.check_output(
            ["git", "show", f"HEAD:{filename}"],
            text=True,
        )
        old_data = json.loads(old_raw).get("DATA", {})
    except (subprocess.CalledProcessError, json.JSONDecodeError, AttributeError):
        old_data = {}

    try:
        new_data = json.loads(path.read_text(encoding="utf-8")).get("DATA", {})
    except (FileNotFoundError, json.JSONDecodeError, AttributeError):
        new_data = {}

    old_keys = set(old_data)
    new_keys = set(new_data)

    added = new_keys - old_keys
    removed = old_keys - new_keys
    changed = {
        key for key in old_keys & new_keys
        if old_data[key] != new_data[key]
    }

    if not (added or removed or changed):
        continue

    added_total += len(added)
    removed_total += len(removed)
    changed_total += len(changed)
    changed_files.append((filename, added, removed, changed))

    for pkg_url in sorted(added):
        metadata = new_data.get(pkg_url)
        if not isinstance(metadata, dict):
            metadata = {}
        new_records[pkg_url] = {"url": pkg_url, **metadata}

if new_records:
    NEW_REGISTRATIONS_PATH.write_text(
        json.dumps({"DATA": new_records}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
else:
    NEW_REGISTRATIONS_PATH.unlink(missing_ok=True)

repo = os.environ.get("GITHUB_REPOSITORY", "")
server_url = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
run_url = (
    f"{server_url}/{repo}/actions/runs/{os.environ.get('GITHUB_RUN_ID')}"
    if repo and os.environ.get("GITHUB_RUN_ID")
    else None
)

print("### ✅ FPKGi Auto Merge")
print()
print("**Status:** Success")
print()
print("**Catalog changes detected:** Yes")
print()
print("**Updated catalog files:**")
for filename, added, removed, changed in changed_files:
    parts = []
    if added:
        parts.append(f"{len(added)} new")
    if removed:
        parts.append(f"{len(removed)} removed")
    if changed:
        parts.append(f"{len(changed)} modified")

    print(f"- {filename} — " + ", ".join(parts))

print()
print("**Record changes:**")
print(f"- 🆕 New records: **{added_total}**")
print(f"- 🗑️ Removed records: **{removed_total}**")
print(f"- 🔄 Modified records: **{changed_total}**")
print()

if new_records and repo:
    registrations_link = f"{server_url}/{repo}/blob/main/{NEW_REGISTRATIONS_PATH.as_posix()}"
    print(f"📄 [View JSON with only the new registrations]({registrations_link})")
    print()

if added_total:
    print("<details>")
    print(f"<summary>🆕 <strong>View exactly what was registered ({added_total} new records)</strong></summary>")
    print()

    for filename, added, removed, changed in changed_files:
        if not added:
            continue

        try:
            new_data = json.loads(
                Path(filename).read_text(encoding="utf-8")
            ).get("DATA", {})
        except (FileNotFoundError, json.JSONDecodeError, AttributeError):
            new_data = {}

        print(f"### {filename}")
        print()

        for pkg_url in sorted(added):
            metadata = new_data.get(pkg_url)

            if not isinstance(metadata, dict):
                metadata = {}

            record = {"url": pkg_url, **metadata}

            print(f"**{metadata.get('name') or pkg_url}**")
            print()
            print("```json")
            print(json.dumps(record, indent=2, ensure_ascii=False))
            print("```")
            print()

    print("</details>")
    print()
if run_url:
    print(f"🔎 [Open this workflow run]({run_url})")
    print()

print("The file list above only includes JSON files whose DATA records actually changed.")
