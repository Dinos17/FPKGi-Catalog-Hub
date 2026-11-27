# DeepStorage scanner and catalog sync

The DeepStorage workflow inventories a public share and can propose guarded
catalog additions or source-URL updates. Catalog changes are never pushed
directly to `main`: GitHub Actions creates or updates a pull request for review.

## Local usage

Install the dependency:

```sh
python3 -m pip install requests
```

Scan the public `fallen` share's PS4 Games folder:

```sh
python3 tools/deepstorage_scanner.py \
  --username fallen \
  --path "PS4/Games/" \
  --output reports/deepstorage-scan.json
```

Folder paths are relative to the share root and cannot contain traversal
components. The sync currently supports `PS4/Games/` and `PS4/DLC/`.

## Catalog synchronization

After a complete scan, the workflow runs
`tools/deepstorage_catalog_sync.py`. For every PKG it:

1. Validates that the source URL is HTTPS on the exact host
   `share.deepstorage.dev`.
2. Reads the PKG header, entry table, and `PARAM.SFO` through bounded HTTP
   Range requests. Every redirect must remain on that exact host; there is no
   full-package download fallback and no Archive.org request.
3. Requires valid PKG structural bounds, agreement between the listing and
   Range-reported package size, and agreement between filename identity tags
   and extracted PARAM.SFO identity where both are available.
4. Requires a valid title ID, package title, version, and filename region.
5. Adds a new PS4 catalog entry when no existing identity matches.
6. Replaces the source URL of a uniquely matched non-Mocha entry with the
   DeepStorage URL, preserving existing metadata and filling only missing
   supported fields.

The sync fails closed for incomplete scans, invalid package structure,
conflicting metadata, ambiguous identities, duplicate scanned identities, and
uncertain catalog matches. Existing Mocha registrations are never replaced.
Packages that cannot establish a region from their filename are skipped rather
than assigned an inferred region.

New entries include title ID, region, title, version, package size, minimum
firmware when available, and `download_source: "deepstorage"`. Release dates
and cover URLs are omitted when they cannot be established from the package;
the workflow does not invent them.

The current validated metadata extractor supports PS4 only. PS5 packages are
not registered by this workflow. It does not cryptographically verify package
contents because no trusted checksum is supplied by the DeepStorage listing.

## GitHub Actions

`.github/workflows/deepstorage-scan.yml` supports manual dispatch and a weekly
scheduled scan. The default folder is `PS4/Games/`; manual dispatch can select
`PS4/DLC/`.

If catalog changes are produced, the workflow creates or updates the pull
request branch `automation/deepstorage-catalog-sync` and opens a pull request.
It never merges that pull request. Review the Actions summary and the
`deepstorage-catalog-sync-reports` artifact before merging.

The repository must allow GitHub Actions' `GITHUB_TOKEN` to create pull
requests. If that repository setting is disabled, the catalog proposal step
will fail rather than pushing directly to `main`.

## Inventory and safety guarantees

- A scan is complete only after all listing pages have been read successfully.
- Pagination must advance; malformed offsets and safety-limit violations are
  rejected.
- Duplicate direct links are de-duplicated.
- The API does not guarantee a consistent point-in-time snapshot.
- Catalog JSON is changed only in the workflow working tree and then proposed
  through a pull request.
- No PKG binaries are downloaded, no files are uploaded or deleted, and no
  Mocha registration is overwritten.
