import test from "node:test";
import assert from "node:assert/strict";
import {
  getAllowedStorageHosts,
  isAllowedPresignedUrl,
  isValidFileId,
  readCatalog,
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

test("parses exact storage host allowlist", () => {
  assert.deepEqual(
    [...getAllowedStorageHosts("storage.example, cdn.example ")],
    ["storage.example", "cdn.example"],
  );
  assert.equal(getAllowedStorageHosts("").size, 0);
});

test("accepts HTTPS URLs only on explicitly allowed hosts", () => {
  const hosts = getAllowedStorageHosts("storage.example");
  assert.equal(isAllowedPresignedUrl("https://storage.example/file?sig=a%2Fb", hosts), true);
  assert.equal(isAllowedPresignedUrl("http://storage.example/file", hosts), false);
  assert.equal(isAllowedPresignedUrl("https://evil.example/file", hosts), false);
  assert.equal(isAllowedPresignedUrl("https://user:pass@storage.example/file", hosts), false);
  assert.equal(isAllowedPresignedUrl("https://storage.example/file#fragment", hosts), false);
  assert.equal(isAllowedPresignedUrl("https://storage.example:444/file", hosts), false);
  assert.equal(isAllowedPresignedUrl("https://storage.example/file\\n", hosts), false);
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
