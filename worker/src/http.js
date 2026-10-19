export class ApiError extends Error {
  constructor(status, detail) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export function jsonResponse(value, status = 200, extraHeaders = {}) {
  const headers = new Headers({
    "Cache-Control": "no-store",
    "Content-Type": "application/json; charset=utf-8",
    ...extraHeaders,
  });
  return new Response(JSON.stringify(value), { status, headers });
}

export function errorResponse(status, detail) {
  return jsonResponse({ detail }, status);
}

export async function parseJson(request, maxBytes = 32 * 1024) {
  const length = Number(request.headers.get("content-length") || 0);
  if (length > maxBytes) throw new ApiError(413, "request body is too large");
  let text;
  try {
    text = await request.text();
  } catch {
    throw new ApiError(400, "invalid request body");
  }
  if (new TextEncoder().encode(text).length > maxBytes) {
    throw new ApiError(413, "request body is too large");
  }
  try {
    const value = JSON.parse(text || "{}");
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new Error("expected object");
    }
    return value;
  } catch {
    throw new ApiError(400, "request body must be valid JSON");
  }
}

export function requireString(value, name, { min = 1, max = 200 } = {}) {
  if (typeof value !== "string" || value.length < min || value.length > max) {
    throw new ApiError(422, `${name} must be ${min}-${max} characters`);
  }
  return value;
}

export function positiveInteger(value, name, { min = 1, max = Number.MAX_SAFE_INTEGER } = {}) {
  const number = Number(value);
  if (!Number.isSafeInteger(number) || number < min || number > max) {
    throw new ApiError(422, `${name} must be between ${min} and ${max}`);
  }
  return number;
}

export function getBearerToken(request) {
  const value = request.headers.get("authorization") || "";
  const match = /^Bearer\s+([^\s]+)$/i.exec(value);
  return match ? match[1] : null;
}

export function requireDatabase(env) {
  if (!env?.klev || typeof env.klev.prepare !== "function") {
    throw new ApiError(503, "D1 database binding 'klev' is not configured");
  }
  return env.klev;
}

export function requireKV(env) {
  if (!env?.voom || typeof env.voom.get !== "function" || typeof env.voom.put !== "function") {
    throw new ApiError(503, "KV namespace binding 'voom' is not configured");
  }
  return env.voom;
}
