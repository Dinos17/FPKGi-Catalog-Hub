import html
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
IDENTITY_FIELDS = ("title_id", "region", "version", "size")
UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


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
    """Normalize unreserved percent escapes; preserve reserved delimiters."""
    parts = urlsplit(str(url))
    def normalize_escape(match):
        char = chr(int(match.group(1), 16))
        return char if char in UNRESERVED else "%" + match.group(1).upper()
    path = re.sub(r"%([0-9a-fA-F]{2})", normalize_escape, parts.path)
    return parts._replace(scheme=parts.scheme.lower(), netloc=parts.netloc.lower(), path=path).geturl()


def report_value(value):
    text = html.escape(str(value), quote=True)
    return text.replace("\r", " ").replace("\n", " ").replace("`", "&#96;").replace("#", "&#35;")


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
    elif isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip()):
        size = int(value.strip())
    else:
        return None
    return size if size > 0 else None


def package_identity(filename, metadata):
    if not isinstance(metadata, dict):
        return None
    values = []
    for field in IDENTITY_FIELDS:
        value = metadata.get(field)
        if field != "size":
            if not isinstance(value, str) or not value.strip():
                return None
            value = value.strip().upper()
        else:
            value = valid_package_size(value)
            if value is None:
                return None
        values.append(value)
    category = catalog_category(filename)
    identity = (Path(filename).parent.name.upper(), category, *values)
    if category == "dlc":
        name = metadata.get("name")
        if not isinstance(name, str) or not name.strip():
            return None
        identity += (name.strip().casefold(),)
    return identity


def incomplete_identity_fields(filename, metadata):
    if not isinstance(metadata, dict):
        return ["metadata object"]
    missing = [field for field in ("title_id", "region", "version") if not isinstance(metadata.get(field), str) or not metadata[field].strip()]
    if valid_package_size(metadata.get("size")) is None:
        missing.append("size")
    if catalog_category(filename) == "dlc":
        name = metadata.get("name")
        if not isinstance(name, str) or not name.strip():
            missing.append("name")
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

    filename_a = a.path.rsplit("/", 1)[-1]
    filename_b = b.path.rsplit("/", 1)[-1]
    return bool(filename_a) and filename_a == filename_b


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
    platform, category, title_id, region, version, size, *package_name = identity
    result = (
        f"{report_value(platform)} {report_value(category)} {report_value(title_id)} "
        f"{report_value(region)} v{report_value(version)} ({size} bytes)"
    )
    if package_name:
        result += f" — {report_value(package_name[0])}"
    return result


def build_report(
    catalogs,
    url_collisions,
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
        f"**URL normalization collisions:** {len(url_collisions)}",
        f"**Possible Archive.org ↔ Hugging Face mirrors (unverified):** {len(possible_mirrors)}",
        f"**Duplicate package identities:** {len(duplicate_identities)}",
        f"**Records with incomplete identity metadata:** {len(incomplete_records)}",
        "",
    ]

    if url_collisions:
        lines.extend(["#### ❌ URL normalization collisions", ""])
        for url, records in sorted(url_collisions.items()):
            lines.append(f"- `{report_value(url)}`")
            for filename in sorted(records):
                lines.append(f"  - Found in: {report_value(filename)}")
        lines.append("")

    if possible_mirrors:
        lines.extend([
            "#### ⚠️ Possible Archive.org ↔ Hugging Face mirrors (not binary-verified)",
            "",
        ])
        for identity, records in sorted(possible_mirrors.items()):
            lines.append(f"- **{format_identity(identity)}**")
            for filename, url in records:
                lines.append(f"  - `{report_value(filename)}` — `{report_value(url)}`")
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
                lines.append(f"  - `{report_value(filename)}` — `{report_value(url)}`")
        lines.append("")

    if incomplete_records:
        lines.extend(["#### ⚠️ Incomplete identity metadata (not classified)", ""])
        for filename, url, missing in sorted(incomplete_records):
            lines.append("- `" + report_value(filename) + "` — `" + report_value(url) + "` (missing or invalid: " + report_value(", ".join(missing)) + ")")
        lines.append("")

    if url_collisions:
        lines.extend([
            "### ❌ Result: URL normalization collisions detected.",
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
            (
                "### ⚠️ Result: No duplicates among complete records; catalog not fully verified."
                if incomplete_records
                else "### ✅ Result: No confirmed duplicate registrations detected."
            ),
            "",
            (
                str(len(incomplete_records)) + " record(s) were excluded from identity matching."
                if incomplete_records
                else "All records had complete, valid identity metadata."
            ),
            "",
            "Possible mirrors are unverified; this check does not establish binary identity.",
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
                    (filename, pkg_url, incomplete_identity_fields(filename, metadata))
                )
                continue
            by_identity[identity].append((filename, pkg_url))

    url_collisions = {
        url: files for url, files in by_url.items() if len(files) > 1
    }
    possible_mirrors, duplicate_identities = classify_identity_duplicates(by_identity)

    report = build_report(
        catalogs,
        url_collisions,
        possible_mirrors,
        duplicate_identities,
        incomplete_records,
    )
    print(report, end="")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        Path(summary_path).write_text(report, encoding="utf-8")

    if url_collisions or duplicate_identities:
        raise SystemExit(1)


if __name__ == "__main__":
    check_duplicates()
