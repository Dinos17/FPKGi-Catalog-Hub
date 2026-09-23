# FPKGi Catalog Hub

[![Auto Merge](https://github.com/Dinos17/FPKGi-Catalog-Hub/actions/workflows/auto_merge.yml/badge.svg)](https://github.com/Dinos17/FPKGi-Catalog-Hub/actions/workflows/auto_merge.yml)

> A unified FPKGi catalog hub that I built to bring multiple compatible catalog sources together, validate them, remove duplicates, and keep the resulting JSON catalogs updated automatically.

## What is this?

I wanted one place where I could keep my FPKGi catalogs organized instead of relying on a bunch of separate sources.

**FPKGi Catalog Hub** collects compatible public FPKGi JSON catalogs, checks the data, combines them, removes duplicate package URLs, and publishes the result as category-based JSON files.

The goal is simple:

**one place → organized catalogs → automatic updates → ready to use with FPKGi.**

I also added GitHub Release integration so supported `.pkg` assets can be discovered and turned into catalog entries automatically.

---

## Use the catalogs

The generated catalogs are available directly from the repository's `main` branch.

| Category | Catalog |
|---|---|
| 🎮 Games | `games.json` |
| 📦 DLC | `dlc.json` |
| 🛠️ Apps | `apps.json` |
| 🏠 Homebrew | `homebrew.json` |
| 🧪 Demos | `demos.json` |
| 🕹️ Emulators | `emulators.json` |
| 🎨 Themes | `themes.json` |
| 💿 PS1 | `ps1.json` |
| 💿 PS2 | `ps2.json` |
| 🎮 PSP | `psp.json` |
| 🔄 Updates | `updates.json` |

### Direct raw URLs

You can use the generated JSON files directly in FPKGi.

For example:

```
https://raw.githubusercontent.com/Dinos17/FPKGi-Catalog-Hub/main/games.json
https://raw.githubusercontent.com/Dinos17/FPKGi-Catalog-Hub/main/dlc.json
https://raw.githubusercontent.com/Dinos17/FPKGi-Catalog-Hub/main/apps.json
https://raw.githubusercontent.com/Dinos17/FPKGi-Catalog-Hub/main/homebrew.json
```

The other catalog URLs follow the same pattern.

---

## How I built it

The basic idea is pretty straightforward:

```text
             Public Catalog Sources
                       │
                       ▼
                 Fetch Sources
                       │
                       ▼
                  Validate Data
                       │
                       ▼
                 Merge Entries
                       │
                       ▼
                Remove Duplicates
                       │
                       ▼
              Generated JSON Catalogs
                       │
                       ▼
                     FPKGi
```

Every automated run:

1. Fetches the configured catalog sources.
2. Checks that the JSON has the expected structure.
3. Validates individual entries.
4. Merges entries from the different sources.
5. Removes duplicate package URLs.
6. Checks configured GitHub Releases for supported `.pkg` assets.
7. Generates the category JSON files.
8. Commits changes only when the catalogs actually change.

---

## Automatic updates

I use **GitHub Actions** to keep the catalogs maintained automatically.

The workflow runs on a daily schedule, and I can also start it manually from the **Actions** tab.

If nothing changed, nothing gets committed.

If the catalogs changed, the workflow commits the updated JSON files automatically.

---

## GitHub Release integration

I also built support for discovering `.pkg` files from specific GitHub Releases.

The merger can:

- find supported release assets;
- inspect PKG metadata using HTTP range requests;
- extract information such as title ID, version, firmware requirement, size, and region when available; and
- generate an FPKGi catalog entry from the asset.

This means I can add supported packages through Releases without having to manually maintain another external JSON source.

Only packages that are legally redistributable should be hosted through this mechanism.

---

## Source configuration

The sources I use are defined in:

```text
config/sources.json
```

Each catalog category can have multiple sources.

For example:

```json
"games": [
  "https://example.com/GAMES.json",
  "https://example.com/another-games.json"
]
```

The merger combines the valid entries from those sources into the corresponding catalog.

---

## Validation

I don't want one bad entry to break the entire catalog.

The merger checks things such as:

- valid HTTP(S) package URLs;
- valid JSON structure;
- metadata types;
- title ID format;
- package size;
- version format; and
- required catalog structure.

Invalid entries can be rejected while usable entries continue through the merge.

If an entire source is unavailable or malformed, the merger reports the problem and continues with the remaining sources.

---

## Duplicate handling

The **package URL** is used as the unique key for an entry.

That means different versions, regions, or packages can still coexist when they have different package URLs.

---

## Project structure

```text
FPKGi-Catalog-Hub/
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
├── LICENSE
├── CATALOG-LICENSE.md
└── README.md
```

### The main tools

- `merge.py` — fetches, validates, merges, and generates the catalogs.
- `release_sources.py` — discovers supported PKG assets from GitHub Releases.
- `pkg_metadata.py` — reads PKG metadata using HTTP range requests.

---

## Want to suggest a source?

I'm open to **source suggestions**.

If you know a compatible public FPKGi catalog that could be useful here, you can open an issue and point me to it.

I will check the source before adding it.

Please make sure the source is publicly accessible and that its use is compatible with the relevant rights and terms.

---

## Important

This project is a **catalog aggregator**. The information inside the generated catalogs can come from third-party sources.

I don't claim ownership of third-party metadata, packages, trademarks, or other third-party material just because it appears in a catalog.

The presence of a package or URL in a catalog does **not** mean I am granting permission to redistribute that package.

Use and distribution of individual packages is your responsibility and depends on the rights applicable to those packages and their sources.

---

## Licensing

I intentionally keep the **project code** and the **generated catalogs** under separate terms.

### Project code

The original code, scripts, documentation, configuration, and other original project materials are **not open source**.

They are governed by [LICENSE](LICENSE).

### Generated catalogs

The generated FPKGi catalogs are governed by [CATALOG-LICENSE.md](CATALOG-LICENSE.md).

The catalog terms allow the catalogs to be used with FPKGi, subject to the rights applicable to third-party information contained within them.

For the full terms, read:

- [LICENSE](LICENSE)
- [CATALOG-LICENSE.md](CATALOG-LICENSE.md)

---

## Disclaimer

This project is provided as-is.

Catalog contents, external URLs, metadata, and package availability can change or disappear at any time.

Always make sure that your use or distribution of any referenced package is legally permitted in your jurisdiction.

---

**Built and maintained by Dinos17.**
