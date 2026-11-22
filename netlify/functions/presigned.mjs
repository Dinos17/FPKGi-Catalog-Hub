import {
  getAllowedFileIds,
  getAllowedStorageHosts,
  isAllowedPresignedUrl,
  isValidFileId,
  requestPresignedUrl,
} from "./_lib/mocha.mjs";

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

  const apiKey = Netlify.env.get("MOCHA_API_KEY");
  const allowedHosts = getAllowedStorageHosts(Netlify.env.get("MOCHA_STORAGE_HOSTS"));
  if (!apiKey || allowedHosts.size === 0) {
    return Response.json({ error: "resolver_not_configured" }, { status: 503 });
  }

  try {
    const allowedIds = await getAllowedFileIds();
    if (!allowedIds.has(fileId)) {
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
  } catch {
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
