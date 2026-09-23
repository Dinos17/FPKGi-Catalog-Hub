import json
import subprocess
from pathlib import Path

files = subprocess.check_output(
    ["git", "diff", "--cached", "--name-only", "--", "*.json"],
    text=True,
).splitlines()

added_total = 0
removed_total = 0
changed_total = 0
changed_files = []

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
    except (json.JSONDecodeError, AttributeError):
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
print("The file list above only includes JSON files whose DATA records actually changed.")
