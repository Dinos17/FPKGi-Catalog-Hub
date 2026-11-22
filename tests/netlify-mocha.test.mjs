import test from "node:test";
import assert from "node:assert/strict";
import {
  getAllowedStorageHosts,
  isAllowedPresignedUrl,
  isValidFileId,
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
