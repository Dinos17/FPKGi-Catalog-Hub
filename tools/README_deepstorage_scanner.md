# DeepStorage scanner

The scanner is intentionally read-only. It reads a public-share folder listing
from DeepStorage and produces a JSON inventory. It does **not** download PKG
binaries, change catalogs, approve candidates, or delete/upload files.

## Local usage

Install the single dependency:

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

The username and folder are configurable. Folder paths are relative to the share
root and cannot contain traversal components.

## GitHub Actions

The workflow `.github/workflows/deepstorage-scan.yml` supports manual dispatch
and a weekly scheduled scan. Reports are stored as a GitHub Actions artifact for
14 days. The workflow has read-only repository permissions and does not commit
reports.

## Report safety

- A report is marked complete only after pagination finishes successfully.
- Listing URLs must be HTTPS and hosted on `share.deepstorage.dev`.
- Pagination must advance; repeated/non-advancing offsets are rejected.
- Safety limits prevent unbounded pages or inventory growth.
- Duplicate direct links are de-duplicated.
- Catalog registration remains a separate, human-reviewed step. This scanner
  currently generates an inventory only; catalog matching and candidate
  generation are not implemented here.
- API listings do not guarantee a consistent point-in-time snapshot.
