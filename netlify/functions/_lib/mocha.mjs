const FILE_ID_RE = /^[A-Za-z0-9._-]{1,128}$/;
const HOST_RE = /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))*$/;
const CATALOG_URLS = [
  "https://raw.githubusercontent.com/Dinos17/FPKGi-Catalog-Hub/refs/heads/main/ps4/games.json",
  "https://raw.githubusercontent.com/Dinos17/FPKGi-Catalog-Hub/refs/heads/main/ps4/dlc.json",
  "https://raw.githubusercontent.com/Dinos17/FPKGi-Catalog-Hub/refs/heads/main/ps5/ps5-games.json",
  "https://raw.githubusercontent.com/Dinos17/FPKGi-Catalog-Hub/refs/heads/main/ps5/ps5-dlc.json",
];
const CATALOG_CACHE_MS = 60_000;
const MAX_CATALOG_BYTES = 5 * 1024 * 1024;
const MAX_UPSTREAM_BYTES = 16 * 1024;
let catalogCache = { expiresAt: 0, ids: new Set() };
let catalogRefresh = null;

export function isValidFileId(value) {
  return typeof value === "string" && FILE_ID_RE.test(value);
}

export function getAllowedStorageHosts(raw) {
  if (typeof raw !== "string" || !raw.trim()) return new Set();
  const hosts = new Set();
  for (const entry of raw.split(",")) {
    const host = entry.trim().toLowerCase();
    if (HOST_RE.test(host)) hosts.add(host);
  }
  return hosts;
}

export function isAllowedPresignedUrl(value, allowedHosts) {
  if (typeof value !== "string" || value.length < 1 || value.length > 2048) return false;
  if (/[\u0000-\u0020\u007f-\uffff\\]/.test(value) || value.includes("#")) return false;

  const authorityMatch = /^https:\/\/([^/?#]*)/i.exec(value);
  if (!authorityMatch || !authorityMatch[1] || authorityMatch[1].includes("@") ||
      authorityMatch[1].includes("%")) return false;

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

async function readBoundedJson(response, maxBytes) {
  if (!response.body) throw new Error("upstream_body_missing");
  const reader = response.body.getReader();
  const chunks = [];
  let totalBytes = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      totalBytes += value.byteLength;
      if (totalBytes > maxBytes) {
        await reader.cancel("upstream_response_too_large");
        throw new Error("upstream_response_too_large");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }

  const bytes = new Uint8Array(totalBytes);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  return JSON.parse(text);
}

export async function readCatalog(url) {
  let response;
  try {
    response = await fetch(url, {
      method: "GET",
      redirect: "error",
      signal: AbortSignal.timeout(10_000),
      headers: { accept: "application/json" },
    });
  } catch {
    throw new Error("catalog_fetch_failed");
  }
  if (!response.ok) throw new Error("catalog_unavailable");
  if (!response.body) throw new Error("catalog_body_missing");

  const reader = response.body.getReader();
  const chunks = [];
  let totalBytes = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      totalBytes += value.byteLength;
      if (totalBytes > MAX_CATALOG_BYTES) {
        await reader.cancel("catalog_too_large");
        throw new Error("catalog_too_large");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }

  const bytes = new Uint8Array(totalBytes);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }

  let text;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    throw new Error("catalog_invalid_encoding");
  }
  let document;
  try {
    document = JSON.parse(text);
  } catch {
    throw new Error("catalog_invalid_json");
  }
  if (!document || typeof document !== "object" || !document.DATA ||
      typeof document.DATA !== "object" || Array.isArray(document.DATA)) {
    throw new Error("catalog_invalid");
  }
  return document.DATA;
}

async function refreshAllowedFileIds() {
  const results = await Promise.allSettled(CATALOG_URLS.map((url) => readCatalog(url)));
  const ids = new Set();
  let complete = true;

  for (const result of results) {
    if (result.status !== "fulfilled") {
      complete = false;
      continue;
    }
    for (const [key, metadata] of Object.entries(result.value)) {
      if (metadata && typeof metadata === "object" &&
          metadata.download_source === "mocha" && isValidFileId(key)) {
        ids.add(key);
      }
    }
  }

  if (complete) {
    catalogCache = { expiresAt: Date.now() + CATALOG_CACHE_MS, ids };
  }
  return { ids, complete };
}

export async function getAllowedFileIds(now = Date.now()) {
  if (catalogCache.expiresAt > now) {
    return { ids: catalogCache.ids, complete: true };
  }
  if (!catalogRefresh) {
    catalogRefresh = refreshAllowedFileIds().finally(() => {
      catalogRefresh = null;
    });
  }
  return catalogRefresh;
}

export function resetCatalogCacheForTests() {
  catalogCache = { expiresAt: 0, ids: new Set() };
  catalogRefresh = null;
}

export async function requestPresignedUrl(fileId, apiKey) {
  let response;
  try {
    response = await fetch("https://api.mocha.my/api/files/presigned", {
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
  } catch {
    throw new Error("upstream_network_error");
  }
  if (response.status >= 300 && response.status < 400) {
    throw new Error("upstream_redirect_rejected");
  }
  if (!response.ok) throw new Error("upstream_request_failed");

  let payload;
  try {
    payload = await readBoundedJson(response, MAX_UPSTREAM_BYTES);
  } catch {
    throw new Error("upstream_response_invalid");
  }
  if (!payload || typeof payload !== "object" || Array.isArray(payload) ||
      typeof payload.url !== "string" || payload.url.length < 1 || payload.url.length > 2048) {
    throw new Error("upstream_response_invalid");
  }
  if (payload.expiresIn !== undefined &&
      (!Number.isSafeInteger(payload.expiresIn) || payload.expiresIn <= 0)) {
    throw new Error("upstream_response_invalid");
  }
  return {
    url: payload.url,
    expiresIn: payload.expiresIn ?? null,
  };
}
