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

SOURCE_URLS = [
    "https://r.jina.ai/https://www.psdevwiki.com/ps4/Game_Titles/db",
    "https://r.jina.ai/http://www.psdevwiki.com/ps4/Game_Titles/db",
]
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
    last_error: Exception | None = None

    for source_url in SOURCE_URLS:
        try:
            request = urllib.request.Request(
                source_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; FPKGi-Catalog-Hub/1.0)",
                    "Accept": "text/plain, text/markdown, text/html",
                },
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                text = response.read().decode("utf-8", errors="replace")

            if not text.strip():
                raise RuntimeError("proxy returned an empty response")

            # Do not accept a proxy error/landing page as the database.
            if "CUSA00112" not in text and "CUSA01116" not in text:
                raise RuntimeError("response does not contain PS4 Title ID data")

            print(f"Fetched PS4 title database from {source_url}")
            return text
        except Exception as exc:
            last_error = exc
            print(f"Source failed: {source_url}: {exc}")

    raise RuntimeError(f"Unable to fetch PS4 title database: {last_error}")
def build_database(source: str) -> dict[str, dict[str, str]]:
    parser = TableParser()
    parser.feed(source)

    database: dict[str, dict[str, str]] = {}

    def add_row(title_id: str, name: str, type_code: str) -> None:
        title_id = title_id.upper()
        type_code = type_code.upper()
        if TITLE_ID_RE.fullmatch(title_id) and type_code in {"GAME", "APPLICATION"}:
            database[title_id] = {
                "name": name.strip(),
                "type": type_code,
                "source": "psdevwiki",
            }

    for row in parser.rows:
        if len(row) >= 4:
            add_row(row[0], row[2], row[3])

    # The proxy currently returns rows like:
    # CUSA01116  | 205453 | YouTube | APPLICATION
    # Split on pipes and locate the four logical columns without relying
    # on leading/trailing Markdown pipes.
    for line in source.splitlines():
        if "|" not in line:
            continue

        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) < 4:
            continue

        title_match = re.search(r"([A-Z]{4}\d{5})", cells[0], re.IGNORECASE)
        type_match = re.search(r"\b(GAME|APPLICATION)\b", cells[-1], re.IGNORECASE)

        if not title_match or not type_match:
            continue

        add_row(title_match.group(1), cells[2], type_match.group(1))

    return dict(sorted(database.items()))

