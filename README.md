# FPKGi Merged Catalog

> A unified, automatically maintained FPKGi catalog that combines multiple compatible JSON sources into clean, category-based catalogs.

## Overview

**FPKGi Merged Catalog** is a catalog aggregation project for [FPKGi](https://github.com/ItsJokerZz/FPKGi).

It collects compatible FPKGi JSON sources, validates them, merges their entries, removes duplicate package URLs, and publishes the resulting catalogs through GitHub.

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
2. Validates their JSON structure.
3. Extracts the `DATA` objects.
4. Combines entries from multiple sources.
5. Removes duplicate package URLs.
6. Generates the category JSON files.
7. Publishes the updated catalogs through the repository.

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

The repository uses **GitHub Actions** to automatically maintain the generated catalogs.

The workflow:

1. Checks out the repository.
2. Sets up Python.
3. Installs the required dependencies.
4. Runs `tools/merge.py`.
5. Generates the updated catalog files.
6. Commits changes when updates are detected.
7. Pushes the updated catalogs back to the repository.

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
├── tools/
│   └── merge.py
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

### `tools/`

Contains the scripts used to maintain the project.

* `merge.py` — downloads, validates, merges, and generates the catalog files.

### Catalog files

The `.json` files in the repository root are the generated FPKGi catalogs.

They are intended to be consumed directly by FPKGi.

---

## Adding Sources

Source URLs are configured in:

```text
tools/merge.py
```

Each category can contain one or more compatible FPKGi JSON sources.

For example:

```python
"games": [
    "https://example.com/GAMES.json",
    "https://example.com/another-games.json",
],
```

Entries from all valid sources are combined into the corresponding catalog.

Duplicate entries are detected using the package URL.

---

## Source Validation

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

Invalid or incompatible sources are skipped instead of stopping the entire merge process.

---

## Duplicate Handling

The merger uses the **package URL** as the unique key for catalog entries.

This allows different versions, regions, or package files associated with the same title ID to coexist when their package URLs are different.

---

## Error Handling

If a configured source is unavailable, returns invalid JSON, or does not contain the expected `DATA` object, the merger reports the error and continues processing the remaining sources.

This prevents a single unavailable source from stopping the entire catalog update.

---

## Package Hosting

The project can also support packages hosted independently from third-party catalog servers.

For packages that are legally redistributable, future tooling can upload `.pkg` files to GitHub Releases and automatically generate the corresponding FPKGi catalog entries.

The planned workflow is:

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

Contributions are welcome.

When adding a new catalog source:

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

## License

The catalog aggregation and automation code in this repository is provided under the repository's chosen license.

Third-party catalog data and packages remain subject to their respective licenses and distribution terms.
