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
  adminCancelPayment,
  adminCreatePayment,
  adminCreateUser,
  adminDeleteUser,
  adminGetPayment,
  adminGetUser,
  adminListGrants,
  adminRevokeGrant,
  adminUpdateGrant,
  adminUpdateUser,
} from "./admin.js";
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
import { signalHealth, signalResponse } from "./signals.js";

const APP_NAME = "klevs FX trading App";
const VERSION = "1.0.0";

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

async function handleApi(request, env) {
  const url = new URL(request.url);
  const path = url.pathname;
  const method = request.method.toUpperCase();

  if (path === "/api/health" && method === "GET") {
    const db = requireDatabase(env);
    requireKV(env);
    await db.prepare("SELECT 1 AS ok").first();
    const signals = await signalHealth(env);
    return jsonResponse({
      status: signals.state === "fresh" ? "ok" : "degraded",
      app: APP_NAME,
      version: VERSION,
      data_source: signals.data_source || "no signal snapshot published yet",
      universe_size: UNIVERSE.length,
      signals,
      config: {
        persistence: "Cloudflare D1",
        session_cache: "Cloudflare KV",
        signal_source: "scheduled snapshot in D1",
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

  // Accounts: create, read, update, disable/enable (PATCH), delete (only without payment or access records).
  if (path === "/api/admin/users" && method === "POST") {
    const admin = await requireAdmin(env, request);
    return jsonResponse(await adminCreateUser(env, admin.username, await parseJson(request)));
  }
  const userMatch = /^\/api\/admin\/users\/(\d+)$/.exec(path);
  if (userMatch && ["GET", "PATCH", "DELETE"].includes(method)) {
    const admin = await requireAdmin(env, request);
    const userId = positiveInteger(userMatch[1], "user_id");
    if (method === "GET") return jsonResponse(await adminGetUser(env, userId));
    if (method === "PATCH") return jsonResponse(await adminUpdateUser(env, userId, admin, await parseJson(request)));
    return jsonResponse(await adminDeleteUser(env, userId, admin, await parseJson(request)));
  }

  // Access grants: list, change expiry, revoke one grant.
  if (path === "/api/admin/grants" && method === "GET") {
    await requireAdmin(env, request);
    return jsonResponse(await adminListGrants(env, url.searchParams));
  }
  const grantMatch = /^\/api\/admin\/grants\/(\d+)(\/revoke)?$/.exec(path);
  if (grantMatch) {
    const admin = await requireAdmin(env, request);
    const grantId = positiveInteger(grantMatch[1], "grant_id");
    if (!grantMatch[2] && method === "PATCH") {
      return jsonResponse(await adminUpdateGrant(env, grantId, admin, await parseJson(request)));
    }
    if (grantMatch[2] && method === "POST") {
      return jsonResponse(await adminRevokeGrant(env, grantId, admin, await parseJson(request)));
    }
  }

  // Payment records: create an invoice for a user, read one, cancel an open one.
  // Confirm and reject are handled above. Payment records are never deleted.
  if (path === "/api/admin/payments" && method === "POST") {
    const admin = await requireAdmin(env, request);
    return jsonResponse(await adminCreatePayment(env, admin, await parseJson(request)));
  }
  const paymentMatch = /^\/api\/admin\/payments\/(\d+)$/.exec(path);
  if (paymentMatch && method === "GET") {
    await requireAdmin(env, request);
    return jsonResponse(await adminGetPayment(env, positiveInteger(paymentMatch[1], "payment_id")));
  }
  const paymentCancelMatch = /^\/api\/admin\/payments\/(\d+)\/cancel$/.exec(path);
  if (paymentCancelMatch && method === "POST") {
    const admin = await requireAdmin(env, request);
    return jsonResponse(await adminCancelPayment(env, positiveInteger(paymentCancelMatch[1], "payment_id"), admin, await parseJson(request)));
  }

  if (path === "/api/universe" && method === "GET") {
    await requirePaidAccess(env, request);
    return jsonResponse(UNIVERSE);
  }
  if (path === "/api/scan" && method === "GET") {
    await requirePaidAccess(env, request);
    return signalResponse(env, "scan");
  }
  if (path === "/api/instructions" && method === "GET") {
    await requirePaidAccess(env, request);
    return signalResponse(env, "instructions");
  }
  const pairMatch = /^\/api\/pair\/([A-Za-z0-9]+)$/.exec(path);
  if (pairMatch && method === "GET") {
    await requirePaidAccess(env, request);
    const symbol = pairMatch[1].toUpperCase();
    if (!SYMBOLS.has(symbol)) throw new ApiError(404, `unknown symbol ${symbol}`);
    return signalResponse(env, `pair:${symbol}`);
  }
  if (path === "/api/backtest" && method === "POST") {
    await requirePaidAccess(env, request);
    throw new ApiError(503, "backtests are not served by the Cloudflare deployment; run scripts/backtest.py locally");
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
