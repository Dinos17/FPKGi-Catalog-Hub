# FPKGi Mocha Resolver

Netlify Function endpoint: `GET /presigned?fileId=<Mocha file ID>`.

The function:
- accepts only bounded opaque Mocha file IDs;
- checks the ID against the four public catalog JSON documents and requires `download_source: "mocha"`;
- requests a fresh URL from Mocha using the server-side `MOCHA_API_KEY`;
- rejects redirects and validates the returned HTTPS host against an explicit allowlist;
- returns the temporary URL with `Cache-Control: no-store`.

## Required Netlify environment variables

- `MOCHA_API_KEY`: Mocha API key; Functions scope must be enabled.
- `MOCHA_STORAGE_HOSTS`: comma-separated exact hostnames observed in genuine Mocha presigned URLs. Do not use wildcards. Until configured, the resolver fails closed.

The function uses Netlify's platform IP rate limit (30 requests per IP per 60-second window). It does not claim a per-file distributed rate limit.

## Deploy settings

- Base directory: empty
- Build command: `echo "No build required"`
- Publish directory: `netlify-empty`
- Functions directory: `netlify/functions`

Do not deploy until the storage hostname allowlist is populated and reviewed. The current native client also has its own separate hostname allowlist, which must be configured with the same verified hostnames before PS4 runtime testing.
