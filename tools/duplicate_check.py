import json
import os
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
IDENTITY_FIELDS = ("title_id", "region", "version", "size")


def load_catalogs():
    catalogs = {}
    for platform_dir in (ROOT / "ps4", ROOT / "ps5"):
        if not platform_dir.is_dir():
            continue
        for path in sorted(platform_dir.glob("*.json")):
            if path.name == "new-registrations.json":
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}: invalid JSON: {exc}") from exc
            entries = data.get("DATA") if isinstance(data, dict) else None
            if not isinstance(entries, dict):
                continue
            catalogs[path.relative_to(ROOT).as_posix()] = entries
    return catalogs


def normalize_package_url(url):
    """Normalize URL encoding in the path without changing query semantics."""
    parts = urlsplit(str(url))
    return parts._replace(
        scheme=parts.scheme.lower(),
        netloc=parts.netloc.lower(),
        path=unquote(parts.path),
    ).geturl()


def catalog_category(filename):
    """Return a platform-independent catalog type such as games or dlc."""
    path = Path(filename)
    stem = path.stem.lower()
    prefix = path.parent.name.lower() + "-"
    if stem.startswith(prefix):
        stem = stem[len(prefix):]
    return stem


def valid_package_size(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        size = value
    elif isinstance(value, str) and value.strip().isdigit():
        size = int(value.strip())
    elif isinstance(value, float) and value.is_integer():
        size = int(value)
    else:
        return None
    return size if size >= 0 else None


def package_identity(filename, metadata):
    """Build a conservative identity; incomplete metadata is never guessed."""
    if not isinstance(metadata, dict):
        return None
    values = []
    for field in IDENTITY_FIELDS:
        value = metadata.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        if field == "size":
            value = valid_package_size(value)
            if value is None:
                return None
        else:
            value = str(value).strip().upper()
        values.append(value)
    path = Path(filename)
    return (path.parent.name.upper(), catalog_category(filename), *values)


def incomplete_identity_fields(metadata):
    if not isinstance(metadata, dict):
        return ["metadata object"]
    missing = []
    for field in IDENTITY_FIELDS:
        value = metadata.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(field)
        elif field == "size" and valid_package_size(value) is None:
            missing.append(field)
    return missing


def is_expected_mirror(url_a, url_b):
    """Identify a possible Archive.org/Hugging Face mirror pair by filename."""
    a = urlsplit(url_a)
    b = urlsplit(url_b)

    def host_is(host, domain):
        host = (host or "").lower()
        return host == domain or host.endswith("." + domain)

    archive_a = host_is(a.hostname, "archive.org")
    archive_b = host_is(b.hostname, "archive.org")
    hf_a = host_is(a.hostname, "huggingface.co")
    hf_b = host_is(b.hostname, "huggingface.co")

    if not ((archive_a and hf_b) or (archive_b and hf_a)):
        return False

    filename_a = unquote(a.path).rsplit("/", 1)[-1]
    filename_b = unquote(b.path).rsplit("/", 1)[-1]
    return bool(filename_a) and filename_a.casefold() == filename_b.casefold()


def classify_identity_duplicates(by_identity):
    """Separate possible mirrors from identity collisions; never verify binaries."""
    possible_mirrors = {}
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
        is_possible_mirror_pair = (
            len(urls) == 2 and is_expected_mirror(urls[0], urls[1])
        )
        if is_possible_mirror_pair:
            possible_mirrors[identity] = records
        else:
            duplicates[identity] = records

    return possible_mirrors, duplicates


def format_identity(identity):
    platform, category, title_id, region, version, size = identity
    return (
        f"{platform} {category} {title_id} "
        f"{region} v{version} ({size} bytes)"
    )


def build_report(
    catalogs,
    exact_duplicates,
    possible_mirrors,
    duplicate_identities,
    incomplete_records,
):
    total_registrations = sum(len(entries) for entries in catalogs.values())
    lines = [
        "### FPKGi Duplication Check",
        "",
        f"**Catalogs scanned:** {len(catalogs)}",
        f"**Package registrations:** {total_registrations}",
        f"**Exact duplicate package URLs (normalized):** {len(exact_duplicates)}",
        f"**Possible Archive.org ↔ Hugging Face mirrors (unverified):** {len(possible_mirrors)}",
        f"**Duplicate package identities:** {len(duplicate_identities)}",
        f"**Records with incomplete identity metadata:** {len(incomplete_records)}",
        "",
    ]

    if exact_duplicates:
        lines.extend(["#### ❌ Exact duplicate package URLs", ""])
        for url, records in sorted(exact_duplicates.items()):
            lines.append(f"- `{url}`")
            for filename in sorted(records):
                lines.append(f"  - Found in: {filename}")
        lines.append("")

    if possible_mirrors:
        lines.extend([
            "#### ⚠️ Possible Archive.org ↔ Hugging Face mirrors (not binary-verified)",
            "",
        ])
        for identity, records in sorted(possible_mirrors.items()):
            lines.append(f"- **{format_identity(identity)}**")
            for filename, url in records:
                lines.append(f"  - `{filename}` — {url}")
        lines.extend([
            "",
            "These pairs share package metadata and filename, but the checker has not compared their binary contents.",
            "",
        ])

    if duplicate_identities:
        lines.extend(["#### ❌ Duplicate package identities", ""])
        for identity, records in sorted(duplicate_identities.items()):
            lines.append(f"- **{format_identity(identity)}**")
            for filename, url in records:
                lines.append(f"  - `{filename}` — {url}")
        lines.append("")

    if incomplete_records:
        lines.extend(["#### ⚠️ Incomplete identity metadata (not classified)", ""])
        for filename, url, missing in sorted(incomplete_records):
            lines.append(
                f"- `{filename}` — {url} (missing or invalid: {', '.join(missing)})"
            )
        lines.append("")

    if exact_duplicates:
        lines.extend([
            "### ❌ Result: Exact duplicate package URLs detected.",
            "",
            "The workflow failed so these registrations can be reviewed before changing the catalogs.",
        ])
    elif duplicate_identities:
        lines.extend([
            "### ❌ Result: Duplicate package identities detected.",
            "",
            "The workflow failed so these registrations can be reviewed before changing the catalogs.",
        ])
    else:
        lines.extend([
            "### ✅ Result: No confirmed duplicate registrations detected.",
            "",
            "Possible mirrors and incomplete records are reported separately. This check does not establish binary identity.",
        ])

    return "\n".join(lines) + "\n"


def check_duplicates():
    catalogs = load_catalogs()
    by_url = defaultdict(list)
    by_identity = defaultdict(list)
    incomplete_records = []

    for filename, entries in catalogs.items():
        for pkg_url, metadata in entries.items():
            by_url[normalize_package_url(pkg_url)].append(filename)
            identity = package_identity(filename, metadata)
            if identity is None:
                incomplete_records.append(
                    (filename, pkg_url, incomplete_identity_fields(metadata))
                )
                continue
            by_identity[identity].append((filename, pkg_url))

    exact_duplicates = {
        url: files for url, files in by_url.items() if len(files) > 1
    }
    possible_mirrors, duplicate_identities = classify_identity_duplicates(by_identity)

    report = build_report(
        catalogs,
        exact_duplicates,
        possible_mirrors,
        duplicate_identities,
        incomplete_records,
    )
    print(report, end="")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        Path(summary_path).write_text(report, encoding="utf-8")

    if exact_duplicates or duplicate_identities:
        raise SystemExit(1)


if __name__ == "__main__":
    check_duplicates()
