import { createHash, timingSafeEqual } from "node:crypto";
import { isDeepStrictEqual } from "node:util";
import { getUser } from "@netlify/identity";
import { getStore } from "@netlify/blobs";

const STORE_NAME = "fpkgi-private-review";
const QUEUE_KEY = "queue/current.json";
const CANDIDATES_KEY = "candidates/current.json";
const MAX_BODY_BYTES = 16 * 1024;
const MAX_PUBLISH_BYTES = 5 * 1024 * 1024;
const PUBLISH_KEYS = Object.freeze({
  summary: "summary/current.json",
  inventory: "inventory/current.json",
  scan: "raw/scan/current.json",
  comparison: "raw/comparison/current.json",
  queue: QUEUE_KEY,
  candidates: CANDIDATES_KEY,
  rejections: "rejections/current.json",
});
const MACHINE_READ_KEYS = Object.freeze({
  summary: PUBLISH_KEYS.summary,
  inventory: PUBLISH_KEYS.inventory,
  scan: PUBLISH_KEYS.scan,
  comparison: PUBLISH_KEYS.comparison,
  queue: PUBLISH_KEYS.queue,
  candidates: PUBLISH_KEYS.candidates,
  rejections: PUBLISH_KEYS.rejections,
});

function json(data, status = 200) {
  return Response.json(data, {
    status,
    headers: {
      "cache-control": "no-store, private",
      "x-content-type-options": "nosniff",
      "vary": "cookie, authorization",
    },
  });
}

function validPublisherToken(request) {
  const expected = Netlify.env.get("REVIEW_PUBLISH_TOKEN");
  const supplied = request.headers.get("authorization") || "";
  if (typeof expected !== "string" || expected.length < 32 ||
      !supplied.startsWith("Bearer ")) return false;
  const actualBytes = Buffer.from(supplied.slice(7), "utf8");
  const expectedBytes = Buffer.from(expected, "utf8");
  return actualBytes.length === expectedBytes.length &&
    timingSafeEqual(actualBytes, expectedBytes);
}

function validPublishPayload(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload) ||
      payload.schema_version !== 1 ||
      !/^[0-9a-f]{40}$/.test(payload.catalog_commit || "") ||
      !Object.hasOwn(PUBLISH_KEYS, payload.resource) ||
      !payload.value || typeof payload.value !== "object" || Array.isArray(payload.value)) {
    return false;
  }
  const value = payload.value;
  if (payload.resource === "summary") {
    return value.schema_version === 1 && value.scanner === "mocha" &&
      value.scan_complete === true && value.catalog_commit === payload.catalog_commit &&
      typeof value.scan_observed_at === "string";
  }
  if (payload.resource === "inventory") {
    return value.schema_version === 1 && value.scanner === "mocha" &&
      Array.isArray(value.files) && value.file_count === value.files.length &&
      typeof value.scan_observed_at === "string";
  }
  if (payload.resource === "scan") {
    return value.scanner === "mocha" && value.scan_complete === true &&
      typeof value.scan_observed_at === "string" && Array.isArray(value.files);
  }
  if (payload.resource === "comparison") {
    return value.comparison === "mocha_catalog" &&
      value.catalog_commit === payload.catalog_commit &&
      Array.isArray(value.results);
  }
  if (payload.resource === "queue") {
    return value.schema_version === 1 && value.scanner === "mocha" &&
      value.catalog_commit === payload.catalog_commit &&
      Array.isArray(value.entries) && value.pending_count === value.entries.length &&
      typeof value.scan_observed_at === "string";
  }
  if (payload.resource === "candidates") {
    return value.schema_version === 1 && value.scanner === "mocha" &&
      value.catalog_commit === payload.catalog_commit &&
      Array.isArray(value.candidates) && value.candidate_count === value.candidates.length &&
      typeof value.scan_observed_at === "string";
  }
  if (payload.resource === "rejections") {
    return value.schema_version === 1 && Array.isArray(value.records);
  }
  return false;
}

async function publishPrivateReport(request, store) {
  const declaredLength = Number(request.headers.get("content-length") || 0);
  if (declaredLength > MAX_PUBLISH_BYTES) return json({ error: "payload_too_large" }, 413);
  const raw = await request.text();
  if (new TextEncoder().encode(raw).byteLength > MAX_PUBLISH_BYTES) {
    return json({ error: "payload_too_large" }, 413);
  }
  let payload;
  try {
    payload = JSON.parse(raw);
  } catch {
    return json({ error: "invalid_json" }, 400);
  }
  if (!validPublishPayload(payload)) return json({ error: "invalid_publish_payload" }, 400);

  const observed = payload.value.scan_observed_at;
  if (payload.resource !== "rejections") {
    if (typeof observed !== "string" || !Number.isFinite(Date.parse(observed)) ||
        !/(?:Z|[+-]\d{2}:?\d{2})$/i.test(observed)) {
      return json({ error: "invalid_scan_timestamp" }, 400);
    }
    if (payload.resource !== "summary" &&
        (payload.value.catalog_commit !== undefined &&
         payload.value.catalog_commit !== payload.catalog_commit)) {
      return json({ error: "catalog_commit_mismatch" }, 409);
    }
  }
  const snapshotId = payload.resource === "rejections"
    ? createHash("sha256").update(JSON.stringify(payload.value)).digest("hex")
    : observed.replace(/[^0-9TZ.-]/g, "_");
  const snapshotKey = "snapshots/" + payload.catalog_commit + "/" +
    snapshotId + "/" + payload.resource + ".json";
  const existingSnapshot = await store.get(snapshotKey, {
    type: "json",
    consistency: "strong",
  });
  if (existingSnapshot !== null && !isDeepStrictEqual(existingSnapshot, payload.value)) {
    return json({ error: "snapshot_conflict" }, 409);
  }

  const currentSummary = payload.resource === "rejections" ? null : await store.get(PUBLISH_KEYS.summary, {
    type: "json",
    consistency: "strong",
  });
  if (currentSummary && typeof currentSummary.scan_observed_at === "string" &&
      Date.parse(observed) < Date.parse(currentSummary.scan_observed_at)) {
    return json({ error: "stale_scan_snapshot" }, 409);
  }

  if (existingSnapshot === null) {
    const snapshot = await store.setJSON(snapshotKey, payload.value, { onlyIfNew: true });
    if (!snapshot.modified) {
      const racedSnapshot = await store.get(snapshotKey, { type: "json", consistency: "strong" });
      if (!isDeepStrictEqual(racedSnapshot, payload.value)) {
        return json({ error: "snapshot_conflict" }, 409);
      }
    }
  }

  // The publisher workflow must remain serialized and publish summary last.
  // Equal-timestamp retries are idempotent; older snapshots never replace newer pointers.
  if (!currentSummary || Date.parse(observed) >= Date.parse(currentSummary.scan_observed_at || "")) {
    await store.setJSON(PUBLISH_KEYS[payload.resource], payload.value);
  }
  return json({
    ok: true,
    resource: payload.resource,
    catalog_commit: payload.catalog_commit,
    already_published: existingSnapshot !== null,
  }, existingSnapshot === null ? 201 : 200);
}

async function recordMachineDecision(request, store) {
  const declaredLength = Number(request.headers.get("content-length") || 0);
  if (declaredLength > MAX_BODY_BYTES) return json({ error: "payload_too_large" }, 413);
  const raw = await request.text();
  if (new TextEncoder().encode(raw).byteLength > MAX_BODY_BYTES) {
    return json({ error: "payload_too_large" }, 413);
  }
  let payload;
  try {
    payload = JSON.parse(raw);
  } catch {
    return json({ error: "invalid_json" }, 400);
  }
  if (!validDecision(payload) || !Number.isSafeInteger(payload.issue_number) ||
      payload.issue_number < 1 || typeof payload.decided_by !== "string" ||
      !/^[A-Za-z0-9_-]{1,39}$/.test(payload.decided_by)) {
    return json({ error: "invalid_decision" }, 400);
  }
  const queue = await readQueue(store);
  if (!queue) return json({ error: "review_queue_not_ready" }, 503);
  const pending = queue.entries.find((entry) =>
    entry && entry.platform === payload.platform &&
    entry.category === payload.category &&
    entry.created_at === payload.created_at &&
    typeof entry.name === "string" &&
    normalizeName(entry.name) === normalizeName(payload.name)
  );
  if (!pending) return json({ error: "item_not_pending_in_current_scan" }, 409);
  const key = decisionKey(payload);
  const record = {
    schema_version: 1,
    decision_key: key,
    name: pending.name,
    platform: pending.platform,
    category: pending.category,
    created_at: new Date(pending.created_at).toISOString(),
    action: payload.action,
    decided_at: new Date().toISOString(),
    decided_by: payload.decided_by,
    issue_number: payload.issue_number,
    scan_observed_at: queue.scan_observed_at,
    catalog_commit: queue.catalog_commit,
    status: "resolved",
  };
  const result = await store.setJSON("decisions/" + key + ".json", record, { onlyIfNew: true });
  if (!result.modified) return json({ error: "decision_already_recorded" }, 409);
  return json({ ok: true, decision: record }, 201);
}

async function readMachineResource(name, store) {
  if (name === "decisions") {
    const { blobs } = await store.list({ prefix: "decisions/" });
    const records = [];
    for (const blob of blobs) {
      const record = await store.get(blob.key, { type: "json", consistency: "strong" });
      if (record && record.schema_version === 1 && record.decision_key === blob.key.slice("decisions/".length, -5)) {
        records.push(record);
      }
    }
    records.sort((a, b) => String(a.decided_at || "").localeCompare(String(b.decided_at || "")));
    const body = JSON.stringify({ resource: name, records });
    if (new TextEncoder().encode(body).byteLength > MAX_PUBLISH_BYTES) {
      return json({ error: "machine_response_too_large", resource: name }, 413);
    }
    return new Response(body, {
      headers: {
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store, private",
        "x-content-type-options": "nosniff",
        "vary": "authorization",
      },
    });
  }

  if (!Object.hasOwn(MACHINE_READ_KEYS, name)) return json({ error: "unknown_machine_resource" }, 400);
  const value = await store.get(MACHINE_READ_KEYS[name], { type: "json", consistency: "strong" });
  if (value === null) return json({ error: "machine_resource_not_ready", resource: name }, 404);
  const body = JSON.stringify({ resource: name, value });
  if (new TextEncoder().encode(body).byteLength > MAX_PUBLISH_BYTES) {
    return json({ error: "machine_response_too_large", resource: name }, 413);
  }
  return new Response(body, {
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store, private",
      "x-content-type-options": "nosniff",
      "vary": "authorization",
    },
  });
}

function validOrigin(request) {
  const origin = request.headers.get("origin");
  if (!origin) return request.method === "GET" || request.method === "HEAD";
  try {
    return new URL(origin).origin === new URL(request.url).origin;
  } catch {
    return false;
  }
}

function normalizeName(value) {
  return value.normalize("NFKC").trim().toLocaleLowerCase("en-US");
}

function decisionKey(item) {
  const material = [
    item.platform,
    item.category,
    normalizeName(item.name),
    new Date(item.created_at).toISOString(),
  ].join("\0");
  return createHash("sha256").update(material, "utf8").digest("hex");
}

function validDecision(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  if (value.schema_version !== 1) return false;
  if (!["keep", "reject", "resolve"].includes(value.action)) return false;
  if (typeof value.name !== "string" || !value.name.trim() || value.name.length > 500) return false;
  if (!["PS4", "PS5"].includes(value.platform)) return false;
  if (!["games", "dlc"].includes(value.category)) return false;
  if (typeof value.created_at !== "string" || value.created_at.length > 64) return false;
  const date = new Date(value.created_at);
  return Number.isFinite(date.getTime()) && /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value.created_at);
}

async function readQueue(store) {
  const queue = await store.get(QUEUE_KEY, { type: "json", consistency: "strong" });
  if (!queue || queue.schema_version !== 1 || queue.scanner !== "mocha" ||
      !Array.isArray(queue.entries) || !Number.isSafeInteger(queue.pending_count) ||
      queue.pending_count !== queue.entries.length ||
      typeof queue.scan_observed_at !== "string" || typeof queue.catalog_commit !== "string") {
    return null;
  }
  return queue;
}

export default async (request) => {
  if (!["GET", "POST"].includes(request.method)) {
    return json({ error: "method_not_allowed" }, 405);
  }

  const url = new URL(request.url);
  const resource = url.searchParams.get("resource") || "queue";
  const store = getStore({ name: STORE_NAME, consistency: "strong" });

  // GitHub Actions uses a dedicated machine token for private report reads
  // and publishing. Machine access is limited to explicit resource allowlists.
  if (resource === "machine" && request.method === "GET") {
    if (!validPublisherToken(request)) return json({ error: "unauthorized_publisher" }, 401);
    return readMachineResource(url.searchParams.get("name") || "", store);
  }
  if (request.method === "POST" && resource === "publish") {
    if (!validPublisherToken(request)) return json({ error: "unauthorized_publisher" }, 401);
    return publishPrivateReport(request, store);
  }
  if (request.method === "POST" && resource === "decision") {
    if (!validPublisherToken(request)) return json({ error: "unauthorized_publisher" }, 401);
    return recordMachineDecision(request, store);
  }
  if (!validOrigin(request)) return json({ error: "origin_not_allowed" }, 403);

  let user;
  try {
    user = await getUser();
  } catch (error) {
    console.error("review_identity_verification_failed", error);
    return json({ error: "authentication_unavailable" }, 503);
  }
  if (!user) return json({ error: "unauthorized" }, 401);

  if (request.method === "GET") {
    if (resource === "queue") {
      const queue = await readQueue(store);
      if (!queue) return json({ error: "review_queue_not_ready" }, 503);
      return json({ queue, user: { email: user.email } });
    }
    if (resource === "candidates") {
      const report = await store.get(CANDIDATES_KEY, { type: "json", consistency: "strong" });
      if (!report || report.schema_version !== 1 || report.scanner !== "mocha" ||
          !Array.isArray(report.candidates) || !Number.isSafeInteger(report.candidate_count) ||
          report.candidate_count !== report.candidates.length ||
          report.catalog_write_performed !== false || report.automatic_approval !== true ||
          report.binary_integrity_verified !== false ||
          typeof report.scan_observed_at !== "string" || typeof report.catalog_commit !== "string") {
        return json({ error: "candidate_report_not_ready" }, 503);
      }
      return json({ report, user: { email: user.email } });
    }
    if (resource === "decisions") {
      const { blobs } = await store.list({ prefix: "decisions/" });
      const decisions = [];
      for (const blob of blobs) {
        const record = await store.get(blob.key, { type: "json", consistency: "strong" });
        if (record && record.schema_version === 1) decisions.push(record);
      }
      decisions.sort((a, b) => String(a.decided_at).localeCompare(String(b.decided_at)));
      return json({ decisions, user: { email: user.email } });
    }
    return json({ error: "unknown_resource" }, 400);
  }

  const declaredLength = Number(request.headers.get("content-length") || 0);
  if (declaredLength > MAX_BODY_BYTES) return json({ error: "payload_too_large" }, 413);
  const raw = await request.text();
  if (new TextEncoder().encode(raw).byteLength > MAX_BODY_BYTES) return json({ error: "payload_too_large" }, 413);

  let payload;
  try {
    payload = JSON.parse(raw);
  } catch {
    return json({ error: "invalid_json" }, 400);
  }
  if (!validDecision(payload)) return json({ error: "invalid_decision" }, 400);

  const queue = await readQueue(store);
  if (!queue) return json({ error: "review_queue_not_ready" }, 503);

  const key = decisionKey(payload);
  const pending = queue.entries.find((entry) =>
    entry && entry.platform === payload.platform &&
    entry.category === payload.category &&
    entry.created_at === payload.created_at &&
    typeof entry.name === "string" &&
    normalizeName(entry.name) === normalizeName(payload.name)
  );
  if (!pending) return json({ error: "item_not_pending_in_current_scan" }, 409);

  const record = {
    schema_version: 1,
    decision_key: key,
    name: pending.name,
    platform: pending.platform,
    category: pending.category,
    created_at: new Date(pending.created_at).toISOString(),
    action: payload.action,
    decided_at: new Date().toISOString(),
    decided_by: user.email || user.id,
    scan_observed_at: queue.scan_observed_at,
    catalog_commit: queue.catalog_commit,
    status: "resolved",
  };

  const result = await store.setJSON("decisions/" + key + ".json", record, { onlyIfNew: true });
  if (!result.modified) return json({ error: "decision_already_recorded" }, 409);
  return json({ ok: true, decision: record }, 201);
};

export const config = {
  path: "/api/review",
  method: ["GET", "POST"],
};
