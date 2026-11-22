const FILE_ID_RE = /^[A-Za-z0-9._-]{1,128}$/;
const CATALOG_URLS = [
  "https://dinos17.github.io/FPKGi-Catalog-Hub/ps4/games.json",
  "https://dinos17.github.io/FPKGi-Catalog-Hub/ps4/dlc.json",
  "https://dinos17.github.io/FPKGi-Catalog-Hub/ps5/ps5-games.json",
  "https://dinos17.github.io/FPKGi-Catalog-Hub/ps5/ps5-dlc.json",
];
const CATALOG_CACHE_MS = 60_000;
const MAX_CATALOG_BYTES = 5 * 1024 * 1024;
let catalogCache = { expiresAt: 0, ids: new Set() };

export function isValidFileId(value) {
  return typeof value === "string" && FILE_ID_RE.test(value);
}

export function getAllowedStorageHosts(raw) {
  if (typeof raw !== "string" || !raw.trim()) return new Set();
  return new Set(raw.split(",").map((host) => host.trim().toLowerCase()).filter(Boolean));
}

export function isAllowedPresignedUrl(value, allowedHosts) {
  if (typeof value !== "string" || value.length < 1 || value.length > 2048) return false;
  if (/[\u0000-\u0020\u007f-\uffff\\]/.test(value)) return false;
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    return false;
  }
  if (parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.hash) return false;
  if (parsed.port && parsed.port !== "443") return false;
  return allowedHosts.has(parsed.hostname.toLowerCase());
}

async function readCatalog(url) {
  const response = await fetch(url, {
    method: "GET",
    redirect: "error",
    signal: AbortSignal.timeout(10_000),
    headers: { accept: "application/json" },
  });
  if (!response.ok) throw new Error("catalog_unavailable");
  const text = await response.text();
  if (new TextEncoder().encode(text).byteLength > MAX_CATALOG_BYTES) {
    throw new Error("catalog_too_large");
  }
  const document = JSON.parse(text);
  if (!document || typeof document !== "object" || !document.DATA ||
      typeof document.DATA !== "object" || Array.isArray(document.DATA)) {
    throw new Error("catalog_invalid");
  }
  return document.DATA;
}

export async function getAllowedFileIds(now = Date.now()) {
  if (catalogCache.expiresAt > now) return catalogCache.ids;

  const ids = new Set();
  for (const url of CATALOG_URLS) {
    const data = await readCatalog(url);
    for (const [key, metadata] of Object.entries(data)) {
      if (metadata && typeof metadata === "object" &&
          metadata.download_source === "mocha" && isValidFileId(key)) {
        ids.add(key);
      }
    }
  }
  catalogCache = { expiresAt: now + CATALOG_CACHE_MS, ids };
  return ids;
}

export function resetCatalogCacheForTests() {
  catalogCache = { expiresAt: 0, ids: new Set() };
}

export async function requestPresignedUrl(fileId, apiKey) {
  const response = await fetch("https://api.mocha.my/api/files/presigned", {
    method: "POST",
    redirect: "manual",
    signal: AbortSignal.timeout(10_000),
    headers: {
      authorization: `Bearer ${apiKey}`,
      "content-type": "application/json",
      accept: "application/json",
    },
    body: JSON.stringify({ fileId, disposition: "attachment" }),
  });
  if (response.status >= 300 && response.status < 400) {
    throw new Error("upstream_redirect_rejected");
  }
  if (!response.ok) throw new Error("upstream_request_failed");
  const payload = await response.json();
  if (!payload || typeof payload.url !== "string") throw new Error("upstream_response_invalid");
  return {
    url: payload.url,
    expiresIn: Number.isInteger(payload.expiresIn) ? payload.expiresIn : null,
  };
}
