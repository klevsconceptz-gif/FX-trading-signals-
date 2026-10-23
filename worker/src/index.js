import {
  accessStatus,
  currentUser,
  forgotPassword,
  issueSession,
  loginAccount,
  passwordPolicyResponse,
  registerAccount,
  resetPassword,
  revokeSession,
} from "./auth.js";
import {
  ACCESS_DAYS,
  adminListPayments,
  adminListUsers,
  confirmPayment,
  createPayment,
  getMe,
  grantAccess,
  listAdminAudit,
  listMyPayments,
  paymentOptions,
  paymentSettings,
  rejectPayment,
  adminResetPassword,
  revokeAccess,
  submitPaymentReference,
  updatePaymentSettings,
} from "./billing.js";
import {
  ApiError,
  errorResponse,
  jsonResponse,
  parseJson,
  positiveInteger,
  requireDatabase,
  requireKV,
} from "./http.js";
import { SYMBOLS, UNIVERSE } from "./universe.js";

const APP_NAME = "klevs FX trading App";
const VERSION = "1.0.0";
const MARKET_CACHE_TTL = 60;
const MAX_BACKTEST_BODY_BYTES = 16 * 1024;

async function authenticated(env, request) {
  const user = await currentUser(env, request);
  if (!user) throw new ApiError(401, "log in to continue");
  return user;
}

async function requirePaidAccess(env, request) {
  const user = await authenticated(env, request);
  const access = await accessStatus(env, user);
  if (!access.active) {
    throw new ApiError(403, "payment required — complete payment to unlock the bot");
  }
  return { user, access };
}

async function requireAdmin(env, request) {
  const user = await authenticated(env, request);
  if (user.role !== "admin") throw new ApiError(403, "admin only");
  return user;
}

function boolQuery(value) {
  return ["1", "true", "yes", "on"].includes(String(value || "").toLowerCase());
}

function marketOrigin(env, request) {
  const configured = String(env.KFX_SIGNAL_ENGINE_ORIGIN || "").trim();
  if (!configured) {
    throw new ApiError(503, "signal engine is not connected; configure KFX_SIGNAL_ENGINE_ORIGIN on the API Worker");
  }
  let origin;
  try {
    origin = new URL(configured);
  } catch {
    throw new ApiError(500, "KFX_SIGNAL_ENGINE_ORIGIN must be an HTTPS origin URL");
  }
  if (
    origin.protocol !== "https:" || origin.username || origin.password ||
    origin.pathname !== "/" || origin.search || origin.hash
  ) {
    throw new ApiError(500, "KFX_SIGNAL_ENGINE_ORIGIN must be an HTTPS origin URL without a path");
  }
  const requestUrl = new URL(request.url);
  if (origin.host === requestUrl.host) {
    throw new ApiError(500, "KFX_SIGNAL_ENGINE_ORIGIN cannot point back to this API Worker");
  }
  return origin;
}

function copySafeUpstreamHeaders(response) {
  const headers = new Headers({ "Cache-Control": "no-store" });
  const contentType = response.headers.get("content-type");
  if (contentType) headers.set("Content-Type", contentType);
  return headers;
}

function marketCacheTtl(env) {
  const configured = Number(env.KFX_SCAN_CACHE_SECONDS || MARKET_CACHE_TTL);
  if (!Number.isFinite(configured)) return MARKET_CACHE_TTL;
  return Math.max(10, Math.min(300, Math.floor(configured)));
}

async function proxyMarketRequest(request, env, { body = undefined, cacheKey = null, bypassCache = false } = {}) {
  const origin = marketOrigin(env, request);
  const kv = requireKV(env);
  const engineToken = String(env.KFX_SIGNAL_ENGINE_TOKEN || "").trim();
  if (!engineToken) {
    throw new ApiError(503, "signal engine is not authenticated; configure KFX_SIGNAL_ENGINE_TOKEN on the API Worker");
  }
  if (cacheKey && !bypassCache) {
    const cached = await kv.get(cacheKey);
    if (cached) return new Response(cached, {
      status: 200,
      headers: { "Cache-Control": "no-store", "Content-Type": "application/json; charset=utf-8", "X-KLEVS-Cache": "HIT" },
    });
  }

  const incoming = new URL(request.url);
  const target = new URL(incoming.pathname, origin.origin);
  for (const [key, value] of incoming.searchParams) {
    if (key === "refresh") target.searchParams.set(key, value);
  }

  const headers = new Headers({
    Accept: "application/json",
    "X-KLEVS-ENGINE-TOKEN": engineToken,
  });
  const init = { method: request.method, headers, redirect: "manual" };
  if (request.method !== "GET" && request.method !== "HEAD") {
    headers.set("Content-Type", "application/json");
    const serialized = JSON.stringify(body ?? {});
    if (new TextEncoder().encode(serialized).length > MAX_BACKTEST_BODY_BYTES) {
      throw new ApiError(413, "request body is too large");
    }
    init.body = serialized;
  }

  let upstream;
  try {
    upstream = await fetch(new Request(target, init));
  } catch {
    throw new ApiError(502, "signal engine is unavailable");
  }
  const responseBody = await upstream.text();
  if (cacheKey && upstream.ok && responseBody.length < 2_000_000) {
    await kv.put(cacheKey, responseBody, { expirationTtl: marketCacheTtl(env) });
  }
  return new Response(responseBody, {
    status: upstream.status,
    headers: copySafeUpstreamHeaders(upstream),
  });
}

function validateBacktest(body) {
  const symbol = String(body.symbol || "").toUpperCase();
  if (!SYMBOLS.has(symbol)) throw new ApiError(404, `unknown symbol ${symbol || ""}`);
  const result = { symbol };
  if (body.equity !== undefined && body.equity !== null) {
    const value = Number(body.equity);
    if (!Number.isFinite(value) || value <= 0) throw new ApiError(422, "equity must be greater than 0");
    result.equity = value;
  }
  if (body.risk_per_trade_pct !== undefined && body.risk_per_trade_pct !== null) {
    const value = Number(body.risk_per_trade_pct);
    if (!Number.isFinite(value) || value <= 0 || value > 5) {
      throw new ApiError(422, "risk_per_trade_pct must be greater than 0 and at most 5");
    }
    result.risk_per_trade_pct = value;
  }
  if (body.bars !== undefined && body.bars !== null) {
    const value = Number(body.bars);
    if (!Number.isInteger(value) || value < 250 || value > 20_000) {
      throw new ApiError(422, "bars must be between 250 and 20000");
    }
    result.bars = value;
  }
  return result;
}

async function handleApi(request, env) {
  const url = new URL(request.url);
  const path = url.pathname;
  const method = request.method.toUpperCase();

  if (path === "/api/health" && method === "GET") {
    const db = requireDatabase(env);
    requireKV(env);
    await db.prepare("SELECT 1 AS ok").first();
    const hasSignalEngine = Boolean(
      String(env.KFX_SIGNAL_ENGINE_ORIGIN || "").trim() &&
      String(env.KFX_SIGNAL_ENGINE_TOKEN || "").trim()
    );
    return jsonResponse({
      status: hasSignalEngine ? "ok" : "degraded",
      app: APP_NAME,
      version: VERSION,
      data_source: hasSignalEngine ? "connected signal-engine adapter" : "signal engine not configured",
      universe_size: UNIVERSE.length,
      config: {
        persistence: "Cloudflare D1",
        session_cache: "Cloudflare KV",
        signal_engine_configured: hasSignalEngine,
      },
    });
  }

  if (path === "/api/auth/password-policy" && method === "GET") {
    return jsonResponse(passwordPolicyResponse());
  }
  if (path === "/api/register" && method === "POST") {
    const body = await parseJson(request);
    return jsonResponse(await registerAccount(env, body));
  }
  if (path === "/api/auth/login" && method === "POST") {
    const body = await parseJson(request);
    return jsonResponse(await loginAccount(env, body));
  }
  if (path === "/api/auth/logout" && method === "POST") {
    await revokeSession(env, request);
    return jsonResponse({ ok: true });
  }
  if (path === "/api/auth/forgot" && method === "POST") {
    return jsonResponse(await forgotPassword(env, await parseJson(request)));
  }
  if (path === "/api/auth/reset" && method === "POST") {
    return jsonResponse(await resetPassword(env, await parseJson(request)));
  }

  if (path === "/api/me" && method === "GET") {
    const user = await authenticated(env, request);
    const access = await accessStatus(env, user);
    return jsonResponse(await getMe(env, user, access));
  }
  if (path === "/api/payment-options" && method === "GET") {
    await authenticated(env, request);
    return jsonResponse(await paymentOptions(env));
  }
  if (path === "/api/payments" && method === "POST") {
    const user = await authenticated(env, request);
    return jsonResponse(await createPayment(env, user.id, await parseJson(request)));
  }
  if (path === "/api/payments" && method === "GET") {
    const user = await authenticated(env, request);
    return jsonResponse(await listMyPayments(env, user.id));
  }
  const referenceMatch = /^\/api\/payments\/(\d+)\/reference$/.exec(path);
  if (referenceMatch && method === "POST") {
    const user = await authenticated(env, request);
    const paymentId = positiveInteger(referenceMatch[1], "payment_id");
    return jsonResponse(await submitPaymentReference(env, user.id, paymentId, await parseJson(request)));
  }

  if (path === "/api/admin/payments" && method === "GET") {
    await requireAdmin(env, request);
    return jsonResponse(await adminListPayments(env, url.searchParams.get("status") || ""));
  }
  const paymentDecisionMatch = /^\/api\/admin\/payments\/(\d+)\/(confirm|reject)$/.exec(path);
  if (paymentDecisionMatch && method === "POST") {
    const admin = await requireAdmin(env, request);
    const paymentId = positiveInteger(paymentDecisionMatch[1], "payment_id");
    const body = await parseJson(request);
    if (paymentDecisionMatch[2] === "confirm") {
      const note = typeof body.note === "string" ? body.note : "";
      if (note.length > 300) throw new ApiError(422, "note must be 300 characters or fewer");
      return jsonResponse(await confirmPayment(env, paymentId, admin.username, note));
    }
    return jsonResponse(await rejectPayment(env, paymentId, admin.username, body.note));
  }
  if (path === "/api/admin/users" && method === "GET") {
    await requireAdmin(env, request);
    return jsonResponse(await adminListUsers(env));
  }
  const accessMatch = /^\/api\/admin\/users\/(\d+)\/(grant|revoke)$/.exec(path);
  if (accessMatch && method === "POST") {
    const admin = await requireAdmin(env, request);
    const userId = positiveInteger(accessMatch[1], "user_id");
    const body = await parseJson(request);
    if (accessMatch[2] === "grant") return jsonResponse(await grantAccess(env, userId, admin.username, body));
    return jsonResponse(await revokeAccess(env, userId, admin.username, body));
  }
  const resetPasswordMatch = /^\/api\/admin\/users\/(\d+)\/reset-password$/.exec(path);
  if (resetPasswordMatch && method === "POST") {
    const admin = await requireAdmin(env, request);
    const userId = positiveInteger(resetPasswordMatch[1], "user_id");
    return jsonResponse(await adminResetPassword(env, userId, admin.username, await parseJson(request)));
  }
  if (path === "/api/admin/payment-settings" && method === "GET") {
    await requireAdmin(env, request);
    return jsonResponse(await paymentSettings(env));
  }
  if (path === "/api/admin/payment-settings" && method === "PUT") {
    const admin = await requireAdmin(env, request);
    return jsonResponse(await updatePaymentSettings(env, admin.username, await parseJson(request)));
  }
  if (path === "/api/admin/audit-log" && method === "GET") {
    await requireAdmin(env, request);
    return jsonResponse(await listAdminAudit(env, url.searchParams.get("limit") || "100"));
  }

  if (path === "/api/universe" && method === "GET") {
    await requirePaidAccess(env, request);
    return jsonResponse(UNIVERSE);
  }
  if (path === "/api/scan" && method === "GET") {
    await requirePaidAccess(env, request);
    const refresh = boolQuery(url.searchParams.get("refresh"));
    return proxyMarketRequest(request, env, {
      cacheKey: "market:scan:v1",
      bypassCache: refresh,
    });
  }
  if (path === "/api/instructions" && method === "GET") {
    await requirePaidAccess(env, request);
    const refresh = boolQuery(url.searchParams.get("refresh"));
    return proxyMarketRequest(request, env, {
      cacheKey: "market:instructions:v1",
      bypassCache: refresh,
    });
  }
  const pairMatch = /^\/api\/pair\/([A-Za-z0-9]+)$/.exec(path);
  if (pairMatch && method === "GET") {
    await requirePaidAccess(env, request);
    const symbol = pairMatch[1].toUpperCase();
    if (!SYMBOLS.has(symbol)) throw new ApiError(404, `unknown symbol ${symbol}`);
    return proxyMarketRequest(request, env);
  }
  if (path === "/api/backtest" && method === "POST") {
    await requirePaidAccess(env, request);
    const body = validateBacktest(await parseJson(request, MAX_BACKTEST_BODY_BYTES));
    return proxyMarketRequest(request, env, { body });
  }

  if (path.startsWith("/api/")) throw new ApiError(404, "not found");
  return errorResponse(404, "not found");
}

export default {
  async fetch(request, env) {
    try {
      return await handleApi(request, env);
    } catch (error) {
      if (error instanceof ApiError) return errorResponse(error.status, error.detail);
      console.error("Worker API request failed", error?.name || "Error");
      return errorResponse(500, "internal server error");
    }
  },
};
