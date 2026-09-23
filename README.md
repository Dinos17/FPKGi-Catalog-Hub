# FPKGi Catalog Hub

[![Auto Merge](https://github.com/Dinos17/FPKGi-Catalog-Hub/actions/workflows/auto_merge.yml/badge.svg)](https://github.com/Dinos17/FPKGi-Catalog-Hub/actions/workflows/auto_merge.yml)

> A unified FPKGi catalog hub that I built to bring multiple compatible catalog sources together, validate them, remove duplicates, and keep the resulting JSON catalogs updated automatically.

## ⚠️ First-time FPKGi setup

**Before downloading packages from this catalog, make sure FPKGi is using Direct Download mode.**

This guide assumes you are starting from scratch. You do **not** need to understand FTP, JSON, GitHub redirects, or BGFT beforehand.

### Step 1 — Open FPKGi at least once

Before looking for `config.json`, make sure FPKGi is installed on your PS4 and has been opened at least once.

Start FPKGi from the PS4 Home Screen and press **X** to open the application.

This first launch creates FPKGi's configuration files, including:

```text
/user/data/FPKGi/config.json
```

You only need to do this once.

### Step 2 — Enable the FTP server on your PS4

From the PS4 Home Screen:

1. Open **Settings**.
2. Open **GoldHEN** at the top of the settings list.
3. Open **Server Settings**.
4. Turn on **Enable FTP Server**.
5. Note the **IP address** and **FTP port** shown by GoldHEN.

### Step 3 — Download FileZilla

On your PC, download **FileZilla Client** from the official FileZilla website. Choose the download that matches your operating system.

**[Download FileZilla Client](https://filezilla-project.org/download.php)**

### Step 4 — Connect FileZilla to your PS4

Open FileZilla.

At the top, enter:

- **Host:** your PS4 IP address
- **Port:** the FTP port shown by GoldHEN

Leave the other fields unchanged unless your setup requires them.

Click **Quickconnect**.

> **Screenshot placeholder — FileZilla Quickconnect:** Show the Host and Port fields filled in and the **Quickconnect** button clearly visible.

### Step 5 — Open the FPKGi folder

After connecting, use FileZilla's **Remote site** panel (the PS4 side).

Open:

```text
/user/data/FPKGi/
```

You should find:

```text
config.json
```

> **Screenshot placeholder — FPKGi folder:** Show the Remote site path `/user/data/FPKGi/` with `config.json` visible.

### Step 6 — Edit config.json directly in FileZilla

You do **not** need to download `config.json` to your PC.

In FileZilla, right-click `config.json` in the **Remote site** panel and choose **View/Edit**.

FileZilla will open the file using your configured text editor. Edit the file there.

Find:

```json
"directDownload": false
```

Change it to:

```json
"directDownload": true
```

> **Screenshot placeholder — Direct Download:** Show the exact `directDownload` setting before/after the change, with the value `true` clearly visible.

### Step 7 — Add the FPKGi Catalog Hub links

Inside `config.json`, find the `CONTENT_URLS` section.

**Replace the entire `CONTENT_URLS` section with the following:**

```json
    "CONTENT_URLS": {
      "PS1": "https://raw.githubusercontent.com/Dinos17/fpkgi-merged/main/ps1.json",
      "PS2": "https://raw.githubusercontent.com/Dinos17/fpkgi-merged/main/ps2.json",
      "PSP": "https://raw.githubusercontent.com/Dinos17/fpkgi-merged/main/psp.json",
      "PS5": "https://raw.githubusercontent.com/Dinos17/fpkgi-merged/main/dlc.json",
      "games": "https://raw.githubusercontent.com/Dinos17/fpkgi-merged/main/games.json",
      "apps": "https://raw.githubusercontent.com/Dinos17/fpkgi-merged/main/apps.json",
      "updates": "https://raw.githubusercontent.com/Dinos17/FPKGi-Catalog-Hub/refs/heads/main/updates.json",
      "DLC": "https://raw.githubusercontent.com/Dinos17/fpkgi-merged/main/dlc.json",
      "demos": "https://raw.githubusercontent.com/Dinos17/fpkgi-merged/main/demos.json",
      "homebrew": "https://raw.githubusercontent.com/Dinos17/fpkgi-merged/main/homebrew.json",
      "emulators": "https://raw.githubusercontent.com/Dinos17/fpkgi-merged/main/emulators.json",
      "themes": "https://raw.githubusercontent.com/Dinos17/fpkgi-merged/main/themes.json"
    }
  }
}
```

You can also find these same **Direct raw URLs** further down in this README under **“Direct raw URLs”** in the **“Use the catalogs”** section.

You only need to do this once. The URLs point to the same catalog files on GitHub, so you do **not** need to replace them whenever the catalog is updated.

> **Screenshot placeholder — CONTENT_URLS:** Show the `CONTENT_URLS` section with the Catalog Hub raw URLs in place.

### Step 8 — Save the configuration

Save the edited `config.json`.

Make sure it is still named:

```text
config.json
```

Do not save it as `config.json.txt`.

> **Screenshot placeholder — Saved config:** Show the edited `config.json` saved on the PC.

### Step 9 — Restart FPKGi

After FileZilla shows **Transfer completed**, close/restart FPKGi so it loads the updated configuration.

You should now have:

- **Direct Download enabled**
- **FPKGi Catalog Hub catalog URLs configured**
- automatic access to updated catalog data using the same URLs

You do **not** need to repeat this setup every time the catalog is updated.

### If you use the configuration file instead

The relevant configuration file is:

```text
/user/data/FPKGi/config.json
```

The important setting is:

```json
"PREFERENCES": {
  "DOWNLOADS": {
    "directDownload": true
  }
}
```

### Why is Direct Download required?

Packages hosted through this project's GitHub Releases are served through GitHub's release-asset download system. During testing, FPKGi's normal background/BGFT download path rejected the redirected package URL with:

```text
[BGFT] ERROR: [2239] Not supported extension.
sceBgftServiceIntDebugDownloadRegisterPkg failed error: 80990033
```

**Direct Download uses a different download path and avoids that failure for these GitHub-hosted packages.**

> **First-time users:** You do not need to understand JSON, GitHub redirects, BGFT, or any of the technical details above. Follow the numbered steps, enable Direct Download, and add the Catalog Hub URLs once.

> This setting is specifically important for packages served from this project's GitHub Releases. See the [FPKGi documentation](https://github.com/ItsJokerZz/FPKGi) for the application's configuration and JSON format.

---

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

### PS5-only catalogs

The repository also generates optional PS5-only catalogs from packages published through the supported `PS5-*` GitHub Release tags. These are kept separate from the normal combined catalogs so PS5 users can use a PS5-only source when they want one.

| Category | PS5-only catalog |
|---|---|
| 🎮 Games | `ps5-games.json` |
| 📦 DLC | `ps5-dlc.json` |
| 🛠️ Apps | `ps5-apps.json` |
| 🏠 Homebrew | `ps5-homebrew.json` |
| 🧪 Demos | `ps5-demos.json` |
| 🕹️ Emulators | `ps5-emulators.json` |
| 🎨 Themes | `ps5-themes.json` |
| 🔄 Updates | `ps5-updates.json` |

These PS5-only files do **not** create new FPKGi UI categories. They are alternative JSON sources containing only packages discovered from the corresponding `PS5-*` release tags. The normal catalogs remain available and continue to contain all merged sources.

### Direct raw URLs

If you want to use a catalog with FPKGi, copy the **Raw URL** for the JSON file and add it to your FPKGi configuration.

I've also listed the raw URLs here so you don't have to open each file manually:

- **Games:** https://raw.githubusercontent.com/Dinos17/FPKGi-Catalog-Hub/main/games.json
- **DLC:** https://raw.githubusercontent.com/Dinos17/FPKGi-Catalog-Hub/main/dlc.json
- **Apps:** https://raw.githubusercontent.com/Dinos17/FPKGi-Catalog-Hub/main/apps.json
- **Homebrew:** https://raw.githubusercontent.com/Dinos17/FPKGi-Catalog-Hub/main/homebrew.json
- **Demos:** https://raw.githubusercontent.com/Dinos17/FPKGi-Catalog-Hub/main/demos.json
- **Emulators:** https://raw.githubusercontent.com/Dinos17/FPKGi-Catalog-Hub/main/emulators.json
- **Themes:** https://raw.githubusercontent.com/Dinos17/FPKGi-Catalog-Hub/main/themes.json
- **PS1:** https://raw.githubusercontent.com/Dinos17/FPKGi-Catalog-Hub/main/ps1.json
- **PS2:** https://raw.githubusercontent.com/Dinos17/FPKGi-Catalog-Hub/main/ps2.json
- **PSP:** https://raw.githubusercontent.com/Dinos17/FPKGi-Catalog-Hub/main/psp.json
- **Updates:** https://raw.githubusercontent.com/Dinos17/FPKGi-Catalog-Hub/main/updates.json

### PS5-only raw URLs

- **PS5 Games:** https://raw.githubusercontent.com/Dinos17/FPKGi-Catalog-Hub/main/ps5-games.json
- **PS5 DLC:** https://raw.githubusercontent.com/Dinos17/FPKGi-Catalog-Hub/main/ps5-dlc.json
- **PS5 Apps:** https://raw.githubusercontent.com/Dinos17/FPKGi-Catalog-Hub/main/ps5-apps.json
- **PS5 Homebrew:** https://raw.githubusercontent.com/Dinos17/FPKGi-Catalog-Hub/main/ps5-homebrew.json
- **PS5 Demos:** https://raw.githubusercontent.com/Dinos17/FPKGi-Catalog-Hub/main/ps5-demos.json
- **PS5 Emulators:** https://raw.githubusercontent.com/Dinos17/FPKGi-Catalog-Hub/main/ps5-emulators.json
- **PS5 Themes:** https://raw.githubusercontent.com/Dinos17/FPKGi-Catalog-Hub/main/ps5-themes.json
- **PS5 Updates:** https://raw.githubusercontent.com/Dinos17/FPKGi-Catalog-Hub/main/ps5-updates.json

### Add the links once

You only need to add these catalog links to FPKGi **once**.

After that, you don't need to replace or re-add the links whenever the catalogs are updated. The links always point to the same JSON files on GitHub.

When the GitHub repository gets an update, the catalog JSON files are updated automatically. The next time FPKGi fetches the catalogs, it gets the latest data from GitHub using the same links.

**In short:**

1. Add the catalog links to FPKGi once.
2. Leave them there.
3. I keep the catalogs updated automatically.
4. FPKGi gets the latest data from the same GitHub links.

So you **don't need to keep changing the URLs** every time the catalog gets updated.

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
7. Generates the normal combined category JSON files.
8. Generates matching PS5-only JSON files from `PS5-*` release tags.
9. Commits changes only when the catalogs actually change.

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
- `release_sources.py` — discovers supported PKG assets from GitHub Releases and tracks PS5 release entries separately.
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
