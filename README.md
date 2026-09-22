# FPKGi Merged Catalog

> A unified, automatically maintained FPKGi catalog that combines multiple JSON sources into clean, category-based catalogs.

## Overview

**FPKGi Merged Catalog** collects compatible FPKGi JSON sources, merges their entries, removes duplicates, and publishes the resulting catalogs through GitHub.

The goal is to provide a simple and reliable set of catalog URLs that can be used directly with FPKGi.

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

All generated catalogs use the standard FPKGi JSON structure.

## How It Works

```text
Source Catalogs
      │
      ▼
   merge.py
      │
      ├── Download sources
      ├── Validate JSON
      ├── Merge entries
      └── Remove duplicates
      │
      ▼
Generated Catalogs
      │
      ▼
GitHub Repository
      │
      ▼
       FPKGi
```

The merger runs through GitHub Actions and automatically updates the generated JSON files.

## FPKGi Configuration

The generated catalogs can be added to FPKGi through its `CONTENT_URLS` configuration.

Example:

```json
"CONTENT_URLS": {
  "games": "https://raw.githubusercontent.com/Dinos17/fpkgi-merged/main/games.json",
  "apps": "https://raw.githubusercontent.com/Dinos17/fpkgi-merged/main/apps.json",
  "DLC": "https://raw.githubusercontent.com/Dinos17/fpkgi-merged/main/dlc.json",
  "demos": "https://raw.githubusercontent.com/Dinos17/fpkgi-merged/main/demos.json",
  "homebrew": "https://raw.githubusercontent.com/Dinos17/fpkgi-merged/main/homebrew.json",
  "emulators": "https://raw.githubusercontent.com/Dinos17/fpkgi-merged/main/emulators.json",
  "themes": "https://raw.githubusercontent.com/Dinos17/fpkgi-merged/main/themes.json",
  "PS1": "https://raw.githubusercontent.com/Dinos17/fpkgi-merged/main/ps1.json",
  "PS2": "https://raw.githubusercontent.com/Dinos17/fpkgi-merged/main/ps2.json",
  "PSP": "https://raw.githubusercontent.com/Dinos17/fpkgi-merged/main/psp.json"
}
```

## Automated Updates

The repository uses **GitHub Actions** to:

1. Fetch configured source catalogs.
2. Validate their JSON structure.
3. Merge their `DATA` entries.
4. Remove duplicate package URLs.
5. Generate the category JSON files.
6. Commit updated catalogs back to the repository.

The merger can be run manually from the **Actions** tab.

## Project Structure

```text
fpkgi-merged/
├── .github/
│   └── workflows/
│       └── auto_merge.yml
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
├── merge.py
└── README.md
```

## Adding Sources

Source URLs are configured in `merge.py`.

Each category can contain one or more source catalogs. Entries from all valid sources are combined into a single catalog.

Duplicate entries are detected using their package URL.

## Reliability

If one configured source is unavailable or contains invalid JSON, the merger skips that source and continues processing the remaining sources.

This prevents one failed catalog from stopping the entire update process.

## Package Hosting

The project can also support packages hosted independently from third-party catalog servers.

For packages that are legally redistributable, future tooling can upload `.pkg` files to GitHub Releases
