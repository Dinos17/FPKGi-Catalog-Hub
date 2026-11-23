import {
  getAllowedFileIds,
  getAllowedStorageHosts,
  isAllowedPresignedUrl,
  isValidFileId,
  requestPresignedUrl,
} from "./_lib/mocha.mjs";

const SAFE_ERROR_CODES = new Set([
  "catalog_fetch_failed",
  "catalog_unavailable",
  "catalog_body_missing",
  "catalog_too_large",
  "catalog_invalid_encoding",
  "catalog_invalid_json",
  "catalog_invalid",
  "upstream_network_error",
  "upstream_redirect_rejected",
  "upstream_request_failed",
  "upstream_response_invalid",
]);

export default async (request) => {
  if (request.method !== "GET") {
    return Response.json({ error: "method_not_allowed" }, {
      status: 405,
      headers: { allow: "GET" },
    });
  }

  const fileId = new URL(request.url).searchParams.get("fileId");
  if (!isValidFileId(fileId)) {
    return Response.json({ error: "invalid_file_id" }, { status: 400 });
  }

  const netlifyApiKey = Netlify.env.get("MOCHA_API_KEY");
  const processApiKey = process.env.MOCHA_API_KEY;
  const apiKey = String(netlifyApiKey || processApiKey || "").trim();
  const netlifyStorageHosts = Netlify.env.get("MOCHA_STORAGE_HOSTS");
  const processStorageHosts = process.env.MOCHA_STORAGE_HOSTS;
  const allowedHosts = getAllowedStorageHosts(netlifyStorageHosts || processStorageHosts || "");
  if (!apiKey || allowedHosts.size === 0) {
    const diagnostics = {
      netlifyApiKeyPresent: Boolean(String(netlifyApiKey ?? "").trim()),
      processApiKeyPresent: Boolean(String(processApiKey ?? "").trim()),
      netlifyStorageHostsPresent: Boolean(String(netlifyStorageHosts ?? "").trim()),
      processStorageHostsPresent: Boolean(String(processStorageHosts ?? "").trim()),
      allowedStorageHostCount: allowedHosts.size,
    };
    console.error("mocha_resolver_not_configured", diagnostics);
    const body = { error: "resolver_not_configured" };
    if (url.searchParams.get("diagnostics") === "1") body.diagnostics = diagnostics;
    return Response.json(body, { status: 503 });
  }

  try {
    const { ids: allowedIds, complete } = await getAllowedFileIds();
    if (!allowedIds.has(fileId)) {
      if (!complete) {
        return Response.json({ error: "catalog_temporarily_unavailable" }, { status: 503 });
      }
      return Response.json({ error: "file_not_found" }, { status: 404 });
    }

    const result = await requestPresignedUrl(fileId, apiKey);
    if (!isAllowedPresignedUrl(result.url, allowedHosts)) {
      return Response.json({ error: "invalid_upstream_url" }, { status: 502 });
    }

    return Response.json({
      url: result.url,
      ...(result.expiresIn !== null ? { expiresIn: result.expiresIn } : {}),
    }, {
      status: 200,
      headers: { "cache-control": "no-store" },
    });
  } catch (error) {
    const code = error instanceof Error && SAFE_ERROR_CODES.has(error.message)
      ? error.message
      : "unexpected_error";
    console.error("mocha_resolver_failure", { code });
    return Response.json({ error: "resolver_upstream_unavailable" }, { status: 502 });
  }
};

export const config = {
  path: "/presigned",
  method: ["GET"],
  rateLimit: {
    action: "rate_limit",
    aggregateBy: ["ip"],
    windowSize: 60,
    windowLimit: 30,
  },
};
