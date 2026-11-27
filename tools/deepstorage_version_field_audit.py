#!/usr/bin/env python3
"""Read-only bounded-range PARAM.SFO version-field audit for fixed DeepStorage packages."""
from __future__ import annotations

import json
import re
import struct
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import deepstorage_resogun_diagnostic as parser

TARGETS = [
    ("Diablo III Reaper of Souls - [EU] [EN] [1.46].pkg", "https://share.deepstorage.dev/7d3c899ae7a5b89f535c674cfd4f26b4/Diablo%20III%20Reaper%20of%20Souls%20-%20%5BEU%5D%20%5BEN%5D%20%5B1.46%5D.pkg"),
    ("RESOGUN - [US] [EN] [1.10].pkg", "https://share.deepstorage.dev/91be0fdbedb6a37d47530386c9d135e4/RESOGUN%20-%20%5BUS%5D%20%5BEN%5D%20%5B1.10%5D.pkg"),
    ("Disco Elysium - [US] [EN] [1.19].pkg", "https://share.deepstorage.dev/5c9f2e4e9621a42a5d340801245df6a2/Disco%20Elysium%20-%20%5BUS%5D%20%5BEN%5D%20%5B1.19%5D.pkg"),
    ("Disc Jam - [EU] [EN] [1.20].pkg", "https://share.deepstorage.dev/90fa921f58a667671f13f25f8edc766d/Disc%20Jam%20-%20%5BEU%5D%20%5BEN%5D%20%5B1.20%5D.pkg"),
    ("Diablo II - [US] [EN] [1.23].pkg", "https://share.deepstorage.dev/b875b9724654170de8740ad88e43b85f/Diablo%20II%20-%20%5BUS%5D%20%5BEN%5D%20%5B1.23%5D.pkg"),
    ("Devil May Cry Trilogy - [EU] [EN] [1.01].pkg", "https://share.deepstorage.dev/989a7c1972ff0a388e9569e0e6c6e74e/Devil%20May%20Cry%20Trilogy%20-%20%5BEU%5D%20%5BEN%5D%20%5B1.01%5D.pkg"),
    ("Devil May Cry 4 - [JP] [EN] [1.02].pkg", "https://share.deepstorage.dev/55a5060be7921f4c967415ac28e93d97/Devil%20May%20Cry%204%20-%20%5BJP%5D%20%5BEN%5D%20%5B1.02%5D.pkg"),
    ("Destroy All Humans! - [US] [EN] [1.08].pkg", "https://share.deepstorage.dev/656fee201ae1ba012b8b95c04447db8d/Destroy%20All%20Humans%21%20-%20%5BUS%5D%20%5BEN%5D%20%5B1.08%5D.pkg"),
    ("Despot's Game - [EU] [EN] [1.07].pkg", "https://share.deepstorage.dev/49f3a189773d42f91f08f5dfb0627c7e/Despot%27s%20Game%20-%20%5BEU%5D%20%5BEN%5D%20%5B1.07%5D.pkg"),
    ("Deep Rock Galactic - [EU] [EN] [1.43].pkg", "https://share.deepstorage.dev/af6dbb4c3a5463b6d4cf52732ef0c7f6/Deep%20Rock%20Galactic%20-%20%5BEU%5D%20%5BEN%5D%20%5B1.43%5D.pkg"),
    ("Death's Door - [US] [EN] [1.01].pkg", "https://share.deepstorage.dev/15d0307ee292781fbe9cf719781a66fd/Death%27s%20Door%20-%20%5BUS%5D%20%5BEN%5D%20%5B1.01%5D.pkg"),
    ("Dead Rising 2 - [US] [EN] [1.02].pkg", "https://share.deepstorage.dev/00910aee6645204672d7bf2758976dd8/Dead%20Rising%202%20-%20%5BUS%5D%20%5BEN%5D%20%5B1.02%5D.pkg"),
    ("Dead Rising 2 Off The Record - [US] [EN] [1.02].pkg", "https://share.deepstorage.dev/e0d20910fcb2e4c33276b8cf79ff0eb3/Dead%20Rising%202%20Off%20The%20Record%20-%20%5BUS%5D%20%5BEN%5D%20%5B1.02%5D.pkg"),
    ("Dead Nation - [US] [EN] [1.03].pkg", "https://share.deepstorage.dev/9e516879526bd1a7ceed176131fa5de5/Dead%20Nation%20-%20%5BUS%5D%20%5BEN%5D%20%5B1.03%5D.pkg"),
    ("Deadlight - [EU] [EN] [1.02].pkg", "https://share.deepstorage.dev/87c34f45af44bc682770d708f9baa0ef/Deadlight%20-%20%5BEU%5D%20%5BEN%5D%20%5B1.02%5D.pkg"),
    ("DARK SOULS II - [EU] [EN] [1.02].pkg", "https://share.deepstorage.dev/9a808674c3006a86a161a89911d169e0/DARK%20SOULS%20II%20-%20%5BEU%5D%20%5BEN%5D%20%5B1.02%5D.pkg"),
    ("DARK SOULS - [EU] [EN] [1.03].pkg", "https://share.deepstorage.dev/cc4d8921b69cd33303c17a63eeda6142/DARK%20SOULS%20-%20%5BEU%5D%20%5BEN%5D%20%5B1.03%5D.pkg"),
    ("Darksiders Genesis - [EU] [EN] [1.03].pkg", "https://share.deepstorage.dev/9764e0c15331de7bbac83d0c362fbfce/Darksiders%20Genesis%20-%20%5BEU%5D%20%5BEN%5D%20%5B1.03%5D.pkg"),
    ("Darksiders - [EU] [EN] [1.01].pkg", "https://share.deepstorage.dev/13b6666a51323df8e28742fd595f8df0/Darksiders%20-%20%5BEU%5D%20%5BEN%5D%20%5B1.01%5D.pkg"),
    ("Assassin's Creed Rogue - [EU] [EN] [1.01].pkg", "https://share.deepstorage.dev/af49cbe2ea7e205208c37147427a010a/Assassin%27s%20Creed%20Rogue%20-%20%5BEU%5D%20%5BEN%5D%20%5B1.01%5D.pkg"),
    ("Ancestors Legacy - [EU] [EN] [1.05].pkg", "https://share.deepstorage.dev/aa1208a42eba435435cb79ebda826e6c/Ancestors%20Legacy%20-%20%5BEU%5D%20%5BEN%5D%20%5B1.05%5D.pkg"),
    ("Amnesia The Bunker - [US] [EN] [1.81].pkg", "https://share.deepstorage.dev/a9542932441da57b6b5a7557c7c8b821/Amnesia%20The%20Bunker%20-%20%5BUS%5D%20%5BEN%5D%20%5B1.81%5D.pkg"),
]

def filename_version(name: str) -> str | None:
    match = re.search(r"\[([0-9]+\.[0-9]+)\]\.pkg$", name, re.IGNORECASE)
    return match.group(1) if match else None

class PackageInspectionError(RuntimeError):
    def __init__(self, message: str, reader: parser.BoundedRangeReader | None):
        super().__init__(message)
        self.reader = reader


def inspect_one(name: str, url: str) -> dict[str, object]:
    reader: parser.BoundedRangeReader | None = None
    try:
        reader = parser.BoundedRangeReader(url)
        header = reader.read(0, 0x20)
        entry_count, table_offset = parser.parse_pkg_table(header)
        table = reader.read(table_offset, entry_count * 32)
        sfo_offset, sfo_size = parser.find_sfo_entry(table, entry_count)
        if reader.total_size is None or sfo_offset + sfo_size > reader.total_size:
            raise parser.DiagnosticError("PARAM.SFO byte range exceeds reported package size")
        fields = parser.parse_sfo_independent(reader.read(sfo_offset, sfo_size))
        return {
            "package": name,
            "filename_version": filename_version(name),
            "title": fields.get("TITLE"),
            "title_id": fields.get("TITLE_ID"),
            "category": fields.get("CATEGORY"),
            "version": fields.get("VERSION"),
            "app_ver": fields.get("APP_VER"),
            "system_ver": fields.get("SYSTEM_VER"),
            "package_size_bytes": reader.total_size,
            "bytes_requested": reader.bytes_read,
            "ranges_requested": reader.ranges,
            "status": "success",
        }
    except (parser.DiagnosticError, requests.RequestException, UnicodeError, struct.error) as exc:
        raise PackageInspectionError(str(exc), reader) from exc


MARKDOWN_SPECIAL = "\\\\*_{}[]()#+.!|>~-:" + chr(96)


def markdown_cell(value: object) -> str:
    """Render untrusted metadata as inert, single-line Markdown table text."""
    text = " ".join(str(value if value is not None else "—").split())
    return "".join("\\\\" + char if char in MARKDOWN_SPECIAL else char for char in text)


def build_report(results: list[dict[str, object]], total_bytes: int, complete: bool) -> dict[str, object]:
    succeeded = sum(item.get("status") == "success" for item in results)
    targeted = len(TARGETS)
    return {
        "schema_version": 1,
        "diagnostic": "deepstorage-ps4-version-field-audit",
        "read_only": True,
        "package_binary_downloaded": False,
        "catalog_modified": False,
        "packages_targeted": targeted,
        "packages_completed": len(results),
        "packages_succeeded": succeeded,
        "packages_failed": len(results) - succeeded,
        "total_bytes_requested": total_bytes,
        "results": results,
        "status": ("success" if succeeded == targeted else "partial_failure") if complete
                  else "in_progress",
    }


def write_report(path: Path, report: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\\n", encoding="utf-8")
    temporary.replace(path)

def main() -> int:
    output_path = Path("reports/deepstorage-version-field-audit.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []
    total_bytes = 0
    write_report(output_path, build_report(results, total_bytes, complete=False))
    for name, url in TARGETS:
        try:
            item = inspect_one(name, url)
        except PackageInspectionError as exc:
            reader = exc.reader
            item = {
                "package": name,
                "filename_version": filename_version(name),
                "status": "failed",
                "error": str(exc),
                "bytes_requested": reader.bytes_read if reader is not None else 0,
                "ranges_requested": reader.ranges if reader is not None else [],
                "package_size_bytes": reader.total_size if reader is not None else None,
            }
        total_bytes += int(item.get("bytes_requested", 0))
        results.append(item)
        # Atomic checkpoint after every package so a job timeout preserves completed work.
        write_report(output_path, build_report(results, total_bytes, complete=False))
    report = build_report(results, total_bytes, complete=True)
    write_report(output_path, report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["status"] == "success" else 1

if __name__ == "__main__":
    raise SystemExit(main())
