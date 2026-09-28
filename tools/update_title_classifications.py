#!/usr/bin/env python3
"""Build a normalized PS4 Title ID classification database from PS4 Developer Wiki.

The source provides Title ID, English name, and broad GAME/APPLICATION type.
This tool intentionally does not guess more specific categories such as media,
utility, emulator, or homebrew. Those require a more specific source or a
human classification.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

SOURCE_URL = "https://r.jina.ai/https://www.psdevwiki.com/ps4/Game_Titles/db"
TITLE_ID_RE = re.compile(r"^[A-Z]{4}\d{5}$", re.IGNORECASE)


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_row = False
        self.in_cell = False
        self.rows: list[list[str]] = []
        self.current_row: list[str] = []
        self.current_cell: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag == "tr":
            self.in_row = True
            self.current_row = []
        elif tag in {"td", "th"} and self.in_row:
            self.in_cell = True
            self.current_cell = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self.in_cell:
            value = " ".join("".join(self.current_cell).split())
            self.current_row.append(value)
            self.in_cell = False
        elif tag == "tr" and self.in_row:
            if self.current_row:
                self.rows.append(self.current_row)
            self.in_row = False

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.current_cell.append(data)


def fetch_source() -> str:
    request = urllib.request.Request(
        SOURCE_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; FPKGi-Catalog-Hub/1.0)",
            "Accept": "text/plain, text/markdown",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        text = response.read().decode("utf-8", errors="replace")
    if not text.strip():
        raise RuntimeError("PS4 Developer Wiki proxy returned no title database content.")
    return text


def build_database(source: str) -> dict[str, dict[str, str]]:
    parser = TableParser()
    parser.feed(source)

    database: dict[str, dict[str, str]] = {}

    # First accept rows parsed from real HTML.
    rows = list(parser.rows)
    for row in rows:
        if len(row) < 4:
            continue
        title_id, concept_id, name, type_code = [cell.strip() for cell in row[:4]]
        title_id = title_id.upper()
        type_code = type_code.upper()
        if TITLE_ID_RE.fullmatch(title_id) and type_code in {"GAME", "APPLICATION"}:
            database[title_id] = {
                "name": name,
                "type": type_code,
                "source": "psdevwiki",
            }

    # r.jina.ai returns the Developer Wiki table as plain/Markdown text.
    # Parse the complete row directly instead of depending on Markdown
    # pipe placement.
    row_re = re.compile(
        r"^\s*\|?\s*([A-Z]{4}\d{5})\s*\|\s*([^|]+?)"
        r"\s*\|\s*(.*?)\s*\|\s*(GAME|APPLICATION)"
        r"\s*\|?\s*$",
        re.IGNORECASE,
    )

    for line in source.splitlines():
        match = row_re.match(line)
        if not match:
            continue

        title_id, concept_id, name, type_code = match.groups()
        title_id = title_id.upper()
        type_code = type_code.upper()

        database[title_id] = {
            "name": name.strip(),
            "type": type_code,
            "source": "psdevwiki",
        }

    return dict(sorted(database.items()))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="config/title_classifications.json",
        help="Output JSON path",
    )
    args = parser.parse_args()

    source = fetch_source()
    database = build_database(source)

    if not database:
        raise RuntimeError("No Title ID records were parsed from the source.")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "source": SOURCE_URL,
                "source_description": "PS4 Master List by Zecoxao obtained from PS5 System Software 13.20",
                "records": database,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"Parsed {len(database)} PS4 Title ID classifications.")
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
