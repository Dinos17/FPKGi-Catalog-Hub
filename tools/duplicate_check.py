import json
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote, urlsplit

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


def normalize_package_url(url):
    """Normalize URL encoding in the path without changing query semantics."""
    parts = urlsplit(url)
    return parts._replace(
        scheme=parts.scheme.lower(),
        netloc=parts.netloc.lower(),
        path=unquote(parts.path),
    ).geturl()


def is_expected_mirror(url_a, url_b):
    """Return True for matching Archive.org and Hugging Face package mirrors."""
    a = urlsplit(url_a)
    b = urlsplit(url_b)

    def host_is(host, domain):
        host = host.lower().split(":", 1)[0]
        return host == domain or host.endswith("." + domain)

    archive_a = host_is(a.netloc, "archive.org")
    archive_b = host_is(b.netloc, "archive.org")
    hf_a = host_is(a.netloc, "huggingface.co")
    hf_b = host_is(b.netloc, "huggingface.co")

    if not ((archive_a and hf_b) or (archive_b and hf_a)):
        return False

    filename_a = unquote(a.path).rsplit("/", 1)[-1]
    filename_b = unquote(b.path).rsplit("/", 1)[-1]
    return filename_a == filename_b


def classify_identity_duplicates(by_identity):
    expected_mirrors = {}
    duplicates = {}

    for identity, records in by_identity.items():
        unique_normalized = {}
        for filename, url in records:
            unique_normalized.setdefault(normalize_package_url(url), []).append(
                (filename, url)
            )

        if len(unique_normalized) <= 1:
            continue

        urls = list(unique_normalized)
        all_expected_mirrors = (
            len(urls) == 2
            and is_expected_mirror(urls[0], urls[1])
        )

        if all_expected_mirrors:
            expected_mirrors[identity] = records
        else:
            duplicates[identity] = records

    return expected_mirrors, duplicates


def build_report(
    catalogs,
    exact_duplicates,
    expected_mirrors,
    duplicate_identities,
):
    total_registrations = sum(len(entries) for entries in catalogs.values())
    unique_urls = len(
        {
            url
            for entries in catalogs.values()
            for url in entries
        }
    )

    lines = [
        "### FPKGi Duplication Check",
        "",
        f"**Catalogs scanned:** {len(catalogs)}",
        f"**Package registrations:** {total_registrations}",
        f"**Unique package URLs:** {unique_urls}",
        f"**Exact duplicate package URLs:** {len(exact_duplicates)}",
        f"**Expected Archive.org ↔ Hugging Face mirrors:** {len(expected_mirrors)}",
        f"**Duplicate package identities:** {len(suspicious_duplicates)}",
        "",
    ]

    if exact_duplicates:
        lines.extend(["#### ❌ Exact duplicates", ""])
        for url, records in sorted(exact_duplicates.items()):
            lines.append(f"- `{url}`")
            for filename in sorted(records):
                lines.append(f"  - Found in: {filename}")
        lines.append("")

    if expected_mirrors:
        lines.extend(["#### ℹ️ Expected Archive.org ↔ Hugging Face mirrors", ""])
        for (is_ps5, title_id, version), records in sorted(expected_mirrors.items()):
            platform = "PS5" if is_ps5 else "PS4"
            lines.append(f"- **{platform} {title_id} v{version}**")
            for filename, url in records:
                lines.append(f"  - `{filename}` — {url}")
        lines.append("")

    if suspicious_duplicates:
        lines.extend(["#### ❌ Duplicate package identities", ""])
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
            "### ❌ Result: Duplicate package identities detected.",
            "",
            "The workflow failed so these duplicate registrations can be reviewed before changing the catalogs.",
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
            identity = (
                filename.startswith("ps5-"),
                str(title_id).upper(),
                str(version),
            )
            by_identity[identity].append((filename, pkg_url))

    exact_duplicates = {
        url: files for url, files in by_url.items() if len(files) > 1
    }
    expected_mirrors, suspicious_duplicates = classify_identity_duplicates(by_identity)

    report = build_report(
        catalogs,
        exact_duplicates,
        expected_mirrors,
        suspicious_duplicates,
    )
    print(report, end="")

    import os

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        Path(summary_path).write_text(report, encoding="utf-8")

    if exact_duplicates or duplicate_identities:
        raise SystemExit(1)


if __name__ == "__main__":
    check_duplicates()