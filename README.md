# FPKGi Merged Catalog

[![Auto Merge](https://github.com/Dinos17/fpkgi-merged/actions/workflows/auto_merge.yml/badge.svg)](https://github.com/Dinos17/fpkgi-merged/actions/workflows/auto_merge.yml)

> A unified, automatically maintained FPKGi catalog that combines multiple compatible JSON sources into clean, category-based catalogs.

## Overview

**FPKGi Merged Catalog** is a catalog aggregation project for [FPKGi](https://github.com/ItsJokerZz/FPKGi).

It collects compatible FPKGi JSON sources, validates their structure and entry metadata, merges their entries, removes duplicate package URLs, and publishes the resulting catalogs through GitHub.

The goal is to provide a simple, organized, and reliable set of catalog URLs for use with FPKGi.

---

## Catalogs

| Category      | Catalog          |
| ------------- | ---------------- |
| 🎮 Games      | `games.json`     |
| 📦 DLC        | `dlc.json`       |
| 🏠 Homebrew   | `homebrew.json`  |
| 🧪 Demos      | `demos.json`     |
| 🕹️ Emulators | `emulators.json` |
| 🎨 Themes     | `themes.json`    |
| 💿 PS1        | `ps1.json`       |
| 💿 PS2        | `ps2.json`       |
| 🎮 PSP        | `psp.json`       |
| 🛠️ Apps      | `apps.json`      |
| 🔄 Updates    | `updates.json`   |

All generated catalogs use the FPKGi JSON format.

---

## How It Works

```text
                    Source Catalogs
                          │
                          ▼
                  ┌───────────────┐
                  │   merge.py    │
                  │    (tools/)   │
                  └───────┬───────┘
                          │
              ┌───────────┼───────────┐
              │           │           │
           Fetch       Validate     Merge
              │           │           │
              └───────────┼───────────┘
                          ▼
                  Generated Catalogs
                          │
                          ▼
                    GitHub Repository
                          │
                          ▼
                         FPKGi
```

The merger:

1. Downloads configured source catalogs.
2. Validates their JSON structure and individual entries.
3. Extracts the `DATA` objects.
4. Combines entries from multiple sources.
5. Removes duplicate package URLs.
6. Adds entries discovered from configured GitHub Releases.
7. Generates the category JSON files.
8. Publishes updated catalogs only when changes are detected.

---

## FPKGi Configuration

The generated catalogs can be added to FPKGi through its `CONTENT_URLS` configuration.

Example:

```json
"CONTENT_URLS": {
  "PS1": "https://raw.githubusercontent.com/Dinos17/fpkgi-merged/main/ps1.json",
  "PS2": "https://raw.githubusercontent.com/Dinos17/fpkgi-merged/main/ps2.json",
  "PSP": "https://raw.githubusercontent.com/Dinos17/fpkgi-merged/main/psp.json",
  "PS5": "https://raw.githubusercontent.com/ps4arab/fpkgi/main/GAMES.json",
  "games": "https://raw.githubusercontent.com/Dinos17/fpkgi-merged/main/games.json",
  "apps": "https://raw.githubusercontent.com/Dinos17/fpkgi-merged/main/apps.json",
  "updates": "https://raw.githubusercontent.com/Dinos17/fpkgi-merged/main/updates.json",
  "DLC": "https://raw.githubusercontent.com/Dinos17/fpkgi-merged/main/dlc.json",
  "demos": "https://raw.githubusercontent.com/Dinos17/fpkgi-merged/main/demos.json",
  "homebrew": "https://raw.githubusercontent.com/Dinos17/fpkgi-merged/main/homebrew.json",
  "emulators": "https://raw.githubusercontent.com/Dinos17/fpkgi-merged/main/emulators.json",
  "themes": "https://raw.githubusercontent.com/Dinos17/fpkgi-merged/main/themes.json"
}
```

---

## Automated Updates

The repository uses **GitHub Actions** to maintain the generated catalogs.

The workflow:

1. Checks out the repository on a pinned `ubuntu-24.04` GitHub-hosted runner.
2. Sets up Python.
3. Installs the required dependencies.
4. Runs `tools/merge.py`.
5. Validates and generates the catalog files.
6. Detects whether generated JSON files changed.
7. Commits and pushes changes only when updates are detected.

The workflow runs on a daily schedule and can also be started manually from the **Actions** tab.

The workflow can also be started manually from the **Actions** tab.

---

## Project Structure

```text
fpkgi-merged/
│
├── .github/
│   └── workflows/
│       └── auto_merge.yml
│
├── config/
│   └── sources.json
│
├── tools/
│   ├── merge.py
│   ├── pkg_metadata.py
│   └── release_sources.py
│
├── apps.json
├── demos.json
├── dlc.json
├── emulators.json
├── games.json
├── homebrew.json
├── ps1.json
├── ps2.json
├── psp.json
├── themes.json
├── updates.json
│
└── README.md
```

### `config/`

* `sources.json` — defines the external FPKGi catalog sources used by the merger.

### `tools/`

Contains the scripts used to maintain the project.

* `merge.py` — downloads, validates, merges, and generates the catalog files.
* `release_sources.py` — discovers `.pkg` assets from configured GitHub Releases and extracts metadata for catalog entries.
* `pkg_metadata.py` — reads PS4 PKG metadata using HTTP range requests without downloading the complete package.

### Catalog files

The `.json` files in the repository root are the generated FPKGi catalogs.

They are intended to be consumed directly by FPKGi.

---

## Adding Sources

Source URLs are configured in:

```text
config/sources.json
```

Each category can contain one or more compatible FPKGi JSON sources.

For example:

```json
"games": [
  "https://example.com/GAMES.json",
  "https://example.com/another-games.json"
]
```

Entries from all valid sources are combined into the corresponding catalog.

Duplicate entries are detected using the package URL.

---

## Source & Entry Validation

Each source must contain a structure similar to:

```json
{
  "DATA": {
    "https://example.com/example.pkg": {
      "title_id": "CUSA00000",
      "region": "USA",
      "name": "Example",
      "version": "01.00",
      "release": "01-01-2026",
      "size": 123456789,
      "min_fw": "9.00",
      "cover_url": null
    }
  }
}
```

The merger rejects structurally invalid entries and reports metadata warnings without unnecessarily discarding otherwise usable entries. A source that fails to load or does not contain a valid `DATA` object is skipped so other sources can still be processed.

---

## Duplicate Handling

The merger uses the **package URL** as the unique key for catalog entries.

This allows different versions, regions, or package files associated with the same title ID to coexist when their package URLs are different.

---

## Error Handling

If a configured source is unavailable, returns invalid JSON, or does not contain the expected `DATA` object, the merger reports the error and continues processing the remaining sources.

This prevents a single unavailable source from stopping the entire catalog update.

---

## GitHub Release Integration

The merger can discover `.pkg` assets from specific GitHub Releases and automatically generate corresponding FPKGi catalog entries.

The release integration reads package metadata through HTTP range requests, so it can inspect the relevant PKG structures without downloading the entire package during metadata scanning.

Only packages that are legally redistributable should be hosted this way.

The workflow is:

```text
Local PKG
   │
   ▼
GitHub Release
   │
   ▼
Direct Download URL
   │
   ▼
FPKGi Catalog Entry
   │
   ▼
apps.json / homebrew.json
```

This will make adding new supported packages much easier without requiring a new external catalog source.

---

## Contributing

This repository does **not** operate under an open contribution model.

Do not submit code, documentation, configuration, or other material with the expectation that it will automatically receive broad reuse rights or that you will receive a license to the project's proprietary materials.

If you wish to propose a change, open an issue or contact the repository owner first. Any contribution or permission to incorporate third-party material must be separately agreed upon where necessary.

When proposing a new catalog source:

* Make sure it uses the expected FPKGi JSON structure.
* Verify that the source is publicly accessible.
* Avoid adding duplicate sources.
* Make sure package distribution is permitted by the relevant copyright and licensing terms.

---

## Disclaimer

This project provides catalog aggregation and automation tools.

Individual packages referenced by external catalogs may have their own licenses, copyrights, and redistribution restrictions.

Only distribute packages that you have the legal right or permission to redistribute.

---

## Licensing

This repository intentionally separates the licensing of the project's original materials from the generated catalog files.

### Original Code and Project Materials

The project's original Python source code, automation scripts, documentation, configuration, and other original project materials are **not released under an open-source license**.

They are governed by [LICENSE](LICENSE).

Except for the limited permissions expressly granted there, the original materials may not be copied, modified, redistributed, republished, incorporated into another project, or commercially exploited without permission from Dinos17.

### Generated Catalogs

The generated FPKGi JSON catalogs are governed separately by [CATALOG-LICENSE.md](CATALOG-LICENSE.md).

The catalogs may be accessed and used as catalogs with FPKGi in accordance with those terms.

The catalog files contain information obtained from multiple sources. Dinos17 does **not** claim ownership of third-party metadata merely because it appears in these catalogs. Third-party material remains subject to the rights and terms applicable to its original source.

Permission to use a catalog does not grant permission to redistribute any package referenced by that catalog.

### GitHub Platform

This repository is public. GitHub may provide technical functions such as viewing, downloading, cloning, or forking public repository content.

Those platform functions do not expand the permissions granted under [LICENSE](LICENSE) or [CATALOG-LICENSE.md](CATALOG-LICENSE.md).

---

## License Files

* [LICENSE](LICENSE) — terms governing the project's original code and other Original Materials.
* [CATALOG-LICENSE.md](CATALOG-LICENSE.md) — terms governing use of the generated FPKGi catalogs, subject to third-party rights.

Third-party catalog data, package metadata, packages, libraries, trademarks, and other third-party materials remain subject to their respective rights and licenses.
