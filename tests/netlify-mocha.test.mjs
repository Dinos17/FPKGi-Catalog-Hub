import test from "node:test";
import assert from "node:assert/strict";
import handler from "../netlify/functions/presigned.mjs";
import {
  getAllowedFileIds,
  getAllowedStorageHosts,
  isAllowedPresignedUrl,
  isValidFileId,
  readCatalog,
  requestPresignedUrl,
  resetCatalogCacheForTests,
} from "../netlify/functions/_lib/mocha.mjs";

test("accepts only bounded opaque Mocha file IDs", () => {
  assert.equal(isValidFileId("d5ac3913-802b-4d68-9803-8f5c07c7640e"), true);
  assert.equal(isValidFileId("A_b-9.x"), true);
  assert.equal(isValidFileId(""), false);
  assert.equal(isValidFileId("../file"), false);
  assert.equal(isValidFileId("bad id"), false);
  assert.equal(isValidFileId("x".repeat(129)), false);
  assert.equal(isValidFileId(null), false);
});

test("parses only valid exact DNS storage hostnames", () => {
  assert.deepEqual(
    [...getAllowedStorageHosts("storage.example, CDN.example, *.example, bad host, cdn.example:443")],
    ["storage.example", "cdn.example"],
  );
  assert.equal(getAllowedStorageHosts("").size, 0);
});

test("accepts HTTPS URLs only on explicitly allowed hosts", () => {
  const hosts = getAllowedStorageHosts("storage.example");
  assert.equal(isAllowedPresignedUrl("https://storage.example/file?sig=a%2Fb", hosts), true);
  assert.equal(isAllowedPresignedUrl("https://storage.example:443/file", hosts), true);
  assert.equal(isAllowedPresignedUrl("http://storage.example/file", hosts), false);
  assert.equal(isAllowedPresignedUrl("https://evil.example/file", hosts), false);
  assert.equal(isAllowedPresignedUrl("https://user:pass@storage.example/file", hosts), false);
  assert.equal(isAllowedPresignedUrl("https://@storage.example/file", hosts), false);
  assert.equal(isAllowedPresignedUrl("https://storage.example/file#", hosts), false);
  assert.equal(isAllowedPresignedUrl("https://storage.example/file#fragment", hosts), false);
  assert.equal(isAllowedPresignedUrl("https://%73torage.example/file", hosts), false);
  assert.equal(isAllowedPresignedUrl("https://storage.example:444/file", hosts), false);
  assert.equal(isAllowedPresignedUrl("https://storage.example/file\n", hosts), false);
  assert.equal(isAllowedPresignedUrl("x".repeat(2049), hosts), false);
});

test("reads a valid catalog through the bounded stream reader", async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async () => new Response(JSON.stringify({
    DATA: { "valid-file-id": { download_source: "mocha" } },
  }));

  const data = await readCatalog("https://catalog.example/games.json");
  assert.deepEqual(data, {
    "valid-file-id": { download_source: "mocha" },
  });
});

test("cancels catalog response as soon as streamed bytes exceed 5 MiB", async (t) => {
  const originalFetch = globalThis.fetch;
  let cancelled = false;
  t.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async () => new Response(new ReadableStream({
    start(controller) {
      controller.enqueue(new Uint8Array(5 * 1024 * 1024 + 1));
    },
    cancel() {
      cancelled = true;
    },
  }));

  await assert.rejects(
    readCatalog("https://catalog.example/oversized.json"),
    /catalog_too_large/,
  );
  assert.equal(cancelled, true);
});

test("concurrent catalog lookups share one refresh and cache only complete results", async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
    resetCatalogCacheForTests();
  });
  resetCatalogCacheForTests();
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    await Promise.resolve();
    return Response.json({ DATA: {
      "shared-file-id": { download_source: "mocha" },
    } });
  };

  const [first, second] = await Promise.all([
    getAllowedFileIds(),
    getAllowedFileIds(),
  ]);
  assert.equal(calls, 4);
  assert.equal(first.complete, true);
  assert.equal(second.complete, true);
  assert.equal(first.ids.has("shared-file-id"), true);
  assert.equal(second.ids.has("shared-file-id"), true);
});

test("a failed catalog does not block an ID found in another healthy catalog", async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => {
    globalThis.fetch = originalFetch;
    resetCatalogCacheForTests();
  });
  resetCatalogCacheForTests();
  globalThis.fetch = async (url) => {
    if (String(url).endsWith("/ps4/dlc.json")) {
      return new Response("unavailable", { status: 503 });
    }
    return Response.json({ DATA: {
      "healthy-file-id": { download_source: "mocha" },
    } });
  };

  const result = await getAllowedFileIds();
  assert.equal(result.complete, false);
  assert.equal(result.ids.has("healthy-file-id"), true);
});

test("requests a bounded, valid presigned response without following redirects", async (t) => {
  const originalFetch = globalThis.fetch;
  let request;
  t.after(() => { globalThis.fetch = originalFetch; });
  globalThis.fetch = async (url, options) => {
    request = { url, options };
    return Response.json({ url: "https://storage.example/file?sig=x", expiresIn: 86400 });
  };

  const result = await requestPresignedUrl("file-id", "test-key");
  assert.deepEqual(result, {
    url: "https://storage.example/file?sig=x",
    expiresIn: 86400,
  });
  assert.equal(request.url, "https://api.mocha.my/api/files/presigned");
  assert.equal(request.options.redirect, "manual");
  assert.equal(request.options.headers.authorization, "Bearer test-key");
  assert.deepEqual(JSON.parse(request.options.body), {
    fileId: "file-id",
    disposition: "attachment",
  });
});

test("rejects redirects, oversized bodies, malformed payloads, and invalid expiry", async (t) => {
  const originalFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = originalFetch; });

  globalThis.fetch = async () => new Response(null, { status: 302 });
  await assert.rejects(requestPresignedUrl("file-id", "key"), /upstream_redirect_rejected/);

  globalThis.fetch = async () => new Response(new ReadableStream({
    start(controller) {
      controller.enqueue(new Uint8Array(16 * 1024 + 1));
    },
  }));
  await assert.rejects(requestPresignedUrl("file-id", "key"), /upstream_response_invalid/);

  globalThis.fetch = async () => Response.json({ url: "https://storage.example/file", expiresIn: 0 });
  await assert.rejects(requestPresignedUrl("file-id", "key"), /upstream_response_invalid/);

  globalThis.fetch = async () => new Response("{");
  await assert.rejects(requestPresignedUrl("file-id", "key"), /upstream_response_invalid/);
});

test("endpoint rejects non-GET methods and malformed IDs before touching secrets", async () => {
  const methodResponse = await handler(new Request("https://resolver.example/presigned", {
    method: "POST",
  }));
  assert.equal(methodResponse.status, 405);

  const idResponse = await handler(new Request("https://resolver.example/presigned?fileId=bad%2Fid"));
  assert.equal(idResponse.status, 400);
});

test("endpoint returns 503 when an ID cannot be verified because a catalog is unavailable", async (t) => {
  const originalFetch = globalThis.fetch;
  const originalNetlify = globalThis.Netlify;
  t.after(() => {
    globalThis.fetch = originalFetch;
    globalThis.Netlify = originalNetlify;
    resetCatalogCacheForTests();
  });
  resetCatalogCacheForTests();
  globalThis.Netlify = { env: { get: (name) => ({
    MOCHA_API_KEY: "test-key",
    MOCHA_STORAGE_HOSTS: "storage.example",
  })[name] } };
  globalThis.fetch = async (url) => {
    if (String(url).endsWith("/ps4/dlc.json")) {
      return new Response("unavailable", { status: 503 });
    }
    return Response.json({ DATA: {} });
  };

  const response = await handler(new Request(
    "https://resolver.example/presigned?fileId=unknown-file-id",
  ));
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: "catalog_temporarily_unavailable" });
});

test("endpoint only mints a URL for a Mocha ID present in the public catalog", async (t) => {
  const originalFetch = globalThis.fetch;
  const originalNetlify = globalThis.Netlify;
  t.after(() => {
    globalThis.fetch = originalFetch;
    globalThis.Netlify = originalNetlify;
    resetCatalogCacheForTests();
  });
  resetCatalogCacheForTests();
  globalThis.Netlify = { env: { get: (name) => ({
    MOCHA_API_KEY: "test-key",
    MOCHA_STORAGE_HOSTS: "storage.example",
  })[name] } };
  globalThis.fetch = async () => Response.json({ DATA: {
    "approved-file-id": { download_source: "mocha" },
  } });

  const response = await handler(new Request(
    "https://resolver.example/presigned?fileId=not-in-catalog",
  ));
  assert.equal(response.status, 404);
  assert.deepEqual(await response.json(), { error: "file_not_found" });
});

test("endpoint returns only validated, non-cacheable presigned URLs", async (t) => {
  const originalFetch = globalThis.fetch;
  const originalNetlify = globalThis.Netlify;
  t.after(() => {
    globalThis.fetch = originalFetch;
    globalThis.Netlify = originalNetlify;
    resetCatalogCacheForTests();
  });
  resetCatalogCacheForTests();
  globalThis.Netlify = { env: { get: (name) => ({
    MOCHA_API_KEY: "test-key",
    MOCHA_STORAGE_HOSTS: "storage.example",
  })[name] } };
  globalThis.fetch = async (url) => {
    if (String(url).includes("api.mocha.my")) {
      return Response.json({
        url: "https://storage.example/file?signature=temporary",
        expiresIn: 86400,
      });
    }
    return Response.json({ DATA: {
      "approved-file-id": { download_source: "mocha" },
    } });
  };

  const response = await handler(new Request(
    "https://resolver.example/presigned?fileId=approved-file-id",
  ));
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.deepEqual(await response.json(), {
    url: "https://storage.example/file?signature=temporary",
    expiresIn: 86400,
  });
});


test("handler never calls Mocha when the requested ID is absent from every catalog", async (t) => {
  const originalFetch = globalThis.fetch;
  const originalNetlify = globalThis.Netlify;
  t.after(() => {
    globalThis.fetch = originalFetch;
    globalThis.Netlify = originalNetlify;
    resetCatalogCacheForTests();
  });
  resetCatalogCacheForTests();
  let mochaCalls = 0;
  globalThis.Netlify = { env: { get: (name) => ({
    MOCHA_API_KEY: "test-key",
    MOCHA_STORAGE_HOSTS: "storage.example",
  })[name] } };
  globalThis.fetch = async (url) => {
    if (String(url).includes("api.mocha.my")) {
      mochaCalls += 1;
      throw new Error("Mocha must not be contacted for an unauthorized ID");
    }
    return Response.json({ DATA: {
      "different-approved-id": { download_source: "mocha" },
    } });
  };

  const response = await handler(new Request(
    "https://resolver.example/presigned?fileId=not-approved",
  ));
  assert.equal(response.status, 404);
  assert.deepEqual(await response.json(), { error: "file_not_found" });
  assert.equal(mochaCalls, 0);
});

test("handler rejects an unapproved Mocha storage host without returning its URL", async (t) => {
  const originalFetch = globalThis.fetch;
  const originalNetlify = globalThis.Netlify;
  t.after(() => {
    globalThis.fetch = originalFetch;
    globalThis.Netlify = originalNetlify;
    resetCatalogCacheForTests();
  });
  resetCatalogCacheForTests();
  globalThis.Netlify = { env: { get: (name) => ({
    MOCHA_API_KEY: "test-key",
    MOCHA_STORAGE_HOSTS: "storage.example",
  })[name] } };
  globalThis.fetch = async (url) => {
    if (String(url).includes("api.mocha.my")) {
      return Response.json({
        url: "https://attacker.example/file?secret=must-not-leak",
        expiresIn: 86400,
      });
    }
    return Response.json({ DATA: {
      "approved-file-id": { download_source: "mocha" },
    } });
  };

  const response = await handler(new Request(
    "https://resolver.example/presigned?fileId=approved-file-id",
  ));
  assert.equal(response.status, 502);
  assert.deepEqual(await response.json(), { error: "invalid_upstream_url" });
  assert.equal(response.headers.get("cache-control"), null);
});


test("handler rejects Mocha redirects and never returns a redirected URL", async (t) => {
  const originalFetch = globalThis.fetch;
  const originalNetlify = globalThis.Netlify;
  t.after(() => {
    globalThis.fetch = originalFetch;
    globalThis.Netlify = originalNetlify;
    resetCatalogCacheForTests();
  });
  resetCatalogCacheForTests();
  globalThis.Netlify = { env: { get: (name) => ({
    MOCHA_API_KEY: "test-key",
    MOCHA_STORAGE_HOSTS: "storage.example",
  })[name] } };
  globalThis.fetch = async (url) => {
    if (String(url).includes("api.mocha.my")) {
      return new Response(null, {
        status: 302,
        headers: { location: "https://attacker.example/collect?token=must-not-leak" },
      });
    }
    return Response.json({ DATA: {
      "approved-file-id": { download_source: "mocha" },
    } });
  };

  const response = await handler(new Request(
    "https://resolver.example/presigned?fileId=approved-file-id",
  ));
  assert.equal(response.status, 502);
  assert.deepEqual(await response.json(), { error: "resolver_upstream_unavailable" });
});
