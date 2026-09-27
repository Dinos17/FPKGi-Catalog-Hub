import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXCLUDED = {"ps5.json"}

def load_catalogs():
    catalogs = {}
    for path in sorted(ROOT.glob("*.json")):
        if path.name in EXCLUDED or path.name == "new-registrations.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name}: invalid JSON: {exc}") from exc
        entries = data.get("DATA") if isinstance(data, dict) else None
        if not isinstance(entries, dict):
            continue
        catalogs[path.name] = entries
    return catalogs

def build_report(catalogs, exact_duplicates, suspicious_duplicates):
    lines = [
        "### FPKGi Duplication Check",
        "",
        f"**Catalogs scanned:** {len(catalogs)}",
        f"**Unique package URLs:** {sum(len(entries) for entries in catalogs.values())}",
        f"**Exact duplicate package URLs:** {len(exact_duplicates)}",
        f"**Possible title/version duplicates:** {len(suspicious_duplicates)}",
        "",
    ]

    if exact_duplicates:
        lines.extend(["#### ❌ Exact duplicates", ""])
        for url, files in sorted(exact_duplicates.items()):
            lines.append(f"- `{url}`")
            lines.append("  - Found in: " + ", ".join(sorted(files)))
        lines.append("")

    if suspicious_duplicates:
        lines.extend(["#### ⚠️ Possible title/version duplicates", ""])
        for (is_ps5, title_id, version), records in sorted(suspicious_duplicates.items()):
            platform = "PS5" if is_ps5 else "PS4"
            lines.append(f"- **{platform} {title_id} v{version}**")
            for filename, url in records:
                lines.append(f"  - `{filename}` — {url}")
        lines.append("")

    if exact_duplicates:
        lines.extend([
            "### ❌ Result: Exact duplicate package URLs detected.",
            "",
            "The workflow failed so these duplicates can be reviewed before changing the catalogs.",
        ])
    elif suspicious_duplicates:
        lines.extend([
            "### ⚠️ Result: No exact duplicate package URLs detected.",
            "",
            "Possible title/version duplicates are warnings only.",
        ])
    else:
        lines.append("### ✅ Result: No duplicate registrations detected.")

    return "\n".join(lines) + "\n"


def check_duplicates():
    catalogs = load_catalogs()
    by_url = defaultdict(list)
    by_identity = defaultdict(list)
    for filename, entries in catalogs.items():
        for pkg_url, metadata in entries.items():
            by_url[pkg_url].append(filename)
            if not isinstance(metadata, dict):
                continue
            title_id = metadata.get("title_id")
            version = metadata.get("version")
            if not title_id or not version:
                continue
            identity = (filename.startswith("ps5-"), str(title_id).upper(), str(version))
            by_identity[identity].append((filename, pkg_url))
    exact_duplicates = {url: files for url, files in by_url.items() if len(files) > 1}
    suspicious_duplicates = {identity: records for identity, records in by_identity.items() if len({url for _, url in records}) > 1}
    report = build_report(catalogs, exact_duplicates, suspicious_duplicates)
    print(report, end="")

    import os
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        Path(summary_path).write_text(report, encoding="utf-8")
    if exact_duplicates:
        print("#### Exact duplicates")
        for url, files in sorted(exact_duplicates.items()):
            print(f"- {url}")
            print("  - Found in: " + ", ".join(sorted(files)))
        print("")
    if suspicious_duplicates:
        print("#### Possible title/version duplicates")
        for (is_ps5, title_id, version), records in sorted(suspicious_duplicates.items()):
            platform = "PS5" if is_ps5 else "PS4"
            print(f"- {platform} {title_id} v{version}")
            for filename, url in records:
                print(f"  - {filename} — {url}")
        print("")
    if not exact_duplicates and not suspicious_duplicates:
        print("### No duplicate registrations detected.")
    elif not exact_duplicates:
        print("### No exact duplicate package URLs detected.")
        print("Possible title/version duplicates are warnings only.")
    if exact_duplicates:
        raise SystemExit(1)

if __name__ == "__main__":
    check_duplicates()