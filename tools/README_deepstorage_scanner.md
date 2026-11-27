# DeepStorage scanner and catalog sync

The scanner is hardcoded to the public share
[https://deepstorage.dev/fallen](https://deepstorage.dev/fallen). It recursively
inventories every folder beneath `PS4/` and `PS5/`. There are no workflow
inputs for changing the username or scan roots.

The inventory is read-only. It does not upload, delete, move, or download
package binaries. Catalog proposals are a separate, guarded stage.

## Local usage

Install the dependency:

```sh
python3 -m pip install requests
```

Run the fixed-scope inventory:

```sh
python3 tools/deepstorage_scanner.py --output reports/deepstorage-scan.json
```

The scanner paginates each folder, discovers immediate child folders from the
first listing page, and recursively visits them. It enforces global limits for
listing pages, folders, and files. Invalid paths, non-advancing pagination,
malformed metadata, and links outside `share.deepstorage.dev` fail the scan
closed.

## Catalog synchronization

After a complete scan, the workflow runs
`tools/deepstorage_catalog_sync.py`.

- PS4 Games and DLC packages may be considered for catalog changes only after
  bounded Range-based PKG metadata extraction and structural validation.
- PS5 packages are inventoried and reported as `unsupported`; they are not
  sent to the PS4 extractor and cannot be registered or used to update catalogs.
- Existing Mocha registrations are protected.
- Ambiguous, conflicting, malformed, incomplete, and structurally invalid
  packages are blocked.
- Catalog changes are made only in the GitHub Actions working tree and proposed
  through a pull request; the workflow never merges that pull request.

The current validated metadata extractor supports PS4 only. PS5 catalog
registration remains disabled until PS5 PKG parsing is independently validated.
No package binaries are downloaded and no Archive.org requests are made.

## GitHub Actions

`.github/workflows/deepstorage-scan.yml` supports manual dispatch and a weekly
scheduled scan. Both triggers always scan the same fixed roots: `PS4/` and
`PS5/`, including all nested folders. The validation workflow uses the same
fixed scope and is read-only.

If eligible PS4 catalog changes are produced, the workflow creates or updates
the pull request branch `automation/deepstorage-catalog-sync` and opens a
pull request. It never merges that pull request. Review the Actions summary and
the `deepstorage-catalog-sync-reports` artifact before merging.

The repository must allow GitHub Actions' `GITHUB_TOKEN` to create pull
requests. If that repository setting is disabled, the catalog proposal step
will fail rather than pushing directly to `main`.

## Inventory and safety guarantees

- A scan is complete only after both root trees and every discovered nested
  folder have been listed successfully.
- Pagination must advance; malformed offsets and safety-limit violations are
  rejected.
- Duplicate direct links are de-duplicated across both platform trees.
- The API does not guarantee a consistent point-in-time snapshot.
- The scanner only requests listings for descendants of `PS4/` and `PS5/`.
- No PKG binaries are downloaded, no files are uploaded or deleted, and no
  Mocha registration is overwritten.
