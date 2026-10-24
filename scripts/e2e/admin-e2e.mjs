#!/usr/bin/env node
// End-to-end checks for the admin console and password flows, run against a live Worker
// (local: `npm run worker:dev`, or a staging Worker). It creates test accounts, invoices and grants
// with a unique suffix, so it can be re-run. Do not point it at production. It never prints
// passwords or tokens.
//
// Usage:
//   BASE_URL=http://localhost:8787 ADMIN_USERNAME=... ADMIN_PASSWORD=... node scripts/e2e/admin-e2e.mjs
// Exit code 0 when every check passes, 1 otherwise.

const BASE = (process.env.BASE_URL || "http://localhost:8787").replace(/\/$/, "");
const ADMIN_USER = process.env.ADMIN_USERNAME || "";
const ADMIN_PASS = process.env.ADMIN_PASSWORD || "";
if (!ADMIN_USER || !ADMIN_PASS) {
  console.error("Set ADMIN_USERNAME and ADMIN_PASSWORD (the bootstrap admin secret values).");
  process.exit(1);
}

const suffix = `${Date.now().toString(36)}${Math.floor(Math.random() * 1000)}`;
// Phone numbers are unique per account, so each run needs its own (7-20 characters).
const runPhone = `+1555${String(Date.now()).slice(-6)}${Math.floor(Math.random() * 10)}`;
const results = [];
let failures = 0;

function check(name, condition, detail = "") {
  results.push({ name, ok: Boolean(condition) });
  if (!condition) failures += 1;
  console.log(`${condition ? "PASS" : "FAIL"}  ${name}${!condition && detail ? `  -> ${detail}` : ""}`);
  return Boolean(condition);
}

async function call(method, path, { token, body } = {}) {
  const headers = { "content-type": "application/json" };
  if (token) headers.authorization = `Bearer ${token}`;
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: body === undefined || method === "GET" ? undefined : JSON.stringify(body),
  });
  let json = null;
  try { json = await res.json(); } catch { /* empty body */ }
  return { status: res.status, json };
}

async function login(identifier, password) {
  return call("POST", "/api/auth/login", { body: { identifier, password } });
}

function tokenOf(res) {
  return res.json && typeof res.json.token === "string" ? res.json.token : "";
}

async function main() {
  // ---- Admin sign-in through the normal flow ------------------------------------------
  const badLogin = await login(ADMIN_USER, "definitely-wrong-1");
  check("wrong admin password is refused (401)", badLogin.status === 401, `status ${badLogin.status}`);

  const adminLogin = await login(ADMIN_USER, ADMIN_PASS);
  check("admin signs in with KFX_ADMIN_PASSWORD (200)", adminLogin.status === 200, `status ${adminLogin.status}`);
  const admin = tokenOf(adminLogin);
  check("admin session has role admin", adminLogin.json?.user?.role === "admin");

  const anon = await call("GET", "/api/admin/users");
  check("admin API refuses anonymous callers (401)", anon.status === 401, `status ${anon.status}`);

  // ---- A plain user for the non-admin and password checks -----------------------------
  const userName = `e2e_${suffix}`;
  const userEmail = `${userName}@example.test`;
  const userPass = "Start-Pass#2026";
  const created = await call("POST", "/api/admin/users", {
    token: admin,
    body: { username: userName, email: userEmail, password: userPass, role: "user", note: "e2e account" },
  });
  check("admin creates a user account (200/201)", created.status === 200 || created.status === 201, `status ${created.status} ${JSON.stringify(created.json)}`);
  const userId = Number(created.json?.user?.id ?? created.json?.id);
  check("created user has an id", Number.isInteger(userId) && userId > 1, `id ${userId}`);

  const userLogin = await login(userName, userPass);
  check("created user signs in with the admin-chosen password", userLogin.status === 200, `status ${userLogin.status}`);
  const userToken = tokenOf(userLogin);

  const userAsAdmin = await call("GET", "/api/admin/users", { token: userToken });
  check("non-admin is refused admin API (403)", userAsAdmin.status === 403, `status ${userAsAdmin.status}`);

  const dup = await call("POST", "/api/admin/users", {
    token: admin,
    body: { username: userName, email: `other_${suffix}@example.test`, password: userPass, role: "user", note: "dup" },
  });
  check("duplicate username is refused (409 or 422)", dup.status === 409 || dup.status === 422, `status ${dup.status}`);

  const weak = await call("POST", "/api/admin/users", {
    token: admin,
    body: { username: `weak_${suffix}`, email: `weak_${suffix}@example.test`, password: "short", role: "user", note: "weak" },
  });
  check("weak password is refused at creation (422)", weak.status === 422, `status ${weak.status}`);

  // ---- Password reset by the admin ---------------------------------------------------------
  const newPass = "Reset-Pass#2027";
  const weakReset = await call("POST", `/api/admin/users/${userId}/reset-password`, {
    token: admin, body: { new_password: "weakpass", note: "policy check" },
  });
  check("reset refuses a weak password (422)", weakReset.status === 422, `status ${weakReset.status}`);

  const reset = await call("POST", `/api/admin/users/${userId}/reset-password`, {
    token: admin, body: { new_password: newPass, note: "e2e reset" },
  });
  check("admin resets the user's password (200)", reset.status === 200, `status ${reset.status} ${JSON.stringify(reset.json)}`);

  const oldSession = await call("GET", "/api/me", { token: userToken });
  check("reset ends the user's existing session (401)", oldSession.status === 401, `status ${oldSession.status}`);
  check("old password no longer works (401)", (await login(userName, userPass)).status === 401);
  const newLogin = await login(userName, newPass);
  check("new password works immediately (200)", newLogin.status === 200, `status ${newLogin.status}`);
  check("bootstrap admin password cannot be reset from the console (409)",
    (await call("POST", "/api/admin/users/1/reset-password", { token: admin, body: { new_password: "Other-Pass#2028", note: "guard" } })).status === 409);

  // ---- Lockout after repeated failures -----------------------------------------------------
  const lockName = `lock_${suffix}`;
  await call("POST", "/api/admin/users", {
    token: admin, body: { username: lockName, email: `${lockName}@example.test`, password: userPass, role: "user", note: "lockout" },
  });
  let lastStatus = 0;
  for (let i = 0; i < 10; i += 1) lastStatus = (await login(lockName, "wrong-password-9")).status;
  const locked = await login(lockName, userPass);
  check("ten failed logins lock the account (429)", lastStatus === 401 && locked.status === 429, `last ${lastStatus}, correct-password ${locked.status}`);

  // ---- User CRUD: read, update, disable/enable, delete --------------------------------------
  const detail = await call("GET", `/api/admin/users/${userId}`, { token: admin });
  check("admin reads one user with grants and payments", detail.status === 200 && Array.isArray(detail.json?.grants) && Array.isArray(detail.json?.payments), `status ${detail.status}`);

  const renamed = `e2e_${suffix}_b`;
  const patched = await call("PATCH", `/api/admin/users/${userId}`, {
    token: admin, body: { username: renamed, email: `${renamed}@example.test`, phone: runPhone, note: "e2e edit" },
  });
  check("admin updates username, email and phone (200)", patched.status === 200 && patched.json?.user?.username === renamed, `status ${patched.status} ${JSON.stringify(patched.json)}`);
  check("updated username signs in", (await login(renamed, newPass)).status === 200);

  const promote = await call("PATCH", `/api/admin/users/${userId}`, { token: admin, body: { role: "admin", note: "promote" } });
  check("admin promotes a user to admin", promote.status === 200 && promote.json?.user?.role === "admin", `status ${promote.status}`);
  const promotedToken = tokenOf(await login(renamed, newPass));
  check("a promoted account keeps its session after promotion", (await call("GET", "/api/me", { token: promotedToken })).status === 200);
  const selfDemote = await call("PATCH", `/api/admin/users/${userId}`, { token: promotedToken, body: { role: "user", note: "self" } });
  check("an admin cannot remove their own admin role (409)", selfDemote.status === 409, `status ${selfDemote.status}`);
  const demote = await call("PATCH", `/api/admin/users/${userId}`, { token: admin, body: { role: "user", note: "demote" } });
  check("admin demotes back to user", demote.status === 200 && demote.json?.user?.role === "user", `status ${demote.status}`);
  check("demotion ends the old admin session (401)", (await call("GET", "/api/admin/users", { token: promotedToken })).status === 401);

  check("bootstrap admin cannot be demoted (409)",
    (await call("PATCH", "/api/admin/users/1", { token: admin, body: { role: "user", note: "guard" } })).status === 409);
  check("bootstrap admin cannot be disabled (409)",
    (await call("PATCH", "/api/admin/users/1", { token: admin, body: { disabled: true, note: "guard" } })).status === 409);
  check("bootstrap admin cannot be deleted (409)",
    (await call("DELETE", "/api/admin/users/1", { token: admin, body: { note: "guard" } })).status === 409);

  const liveSession = tokenOf(await login(renamed, newPass));
  const disabled = await call("PATCH", `/api/admin/users/${userId}`, { token: admin, body: { disabled: true, note: "e2e disable" } });
  check("admin disables an account (200)", disabled.status === 200 && disabled.json?.user?.disabled === true, `status ${disabled.status}`);
  check("disabling ends the account's live session (401)", (await call("GET", "/api/me", { token: liveSession })).status === 401);
  check("disabled account cannot sign in (403)", (await login(renamed, newPass)).status === 403);
  const enabled = await call("PATCH", `/api/admin/users/${userId}`, { token: admin, body: { disabled: false, note: "e2e enable" } });
  check("admin re-enables the account", enabled.status === 200 && enabled.json?.user?.disabled === false);
  check("re-enabled account signs in", (await login(renamed, newPass)).status === 200);

  const deletable = await call("POST", "/api/admin/users", {
    token: admin, body: { username: `del_${suffix}`, email: `del_${suffix}@example.test`, password: userPass, role: "user", note: "delete me" },
  });
  const deletableId = Number(deletable.json?.user?.id ?? deletable.json?.id);
  const removed = await call("DELETE", `/api/admin/users/${deletableId}`, { token: admin, body: { note: "e2e delete" } });
  check("admin deletes an account with no payments or grants (200)", removed.status === 200, `status ${removed.status}`);
  check("deleted account is gone (404)", (await call("GET", `/api/admin/users/${deletableId}`, { token: admin })).status === 404);

  // ---- Every admin route refuses a non-admin session ---------------------------------------
  const plainToken = tokenOf(await login(renamed, newPass));
  const adminRoutes = [
    ["GET", "/api/admin/users"], ["POST", "/api/admin/users"], ["GET", `/api/admin/users/${userId}`],
    ["PATCH", `/api/admin/users/${userId}`], ["DELETE", `/api/admin/users/${userId}`],
    ["GET", "/api/admin/grants"], ["PATCH", "/api/admin/grants/1"], ["POST", "/api/admin/grants/1/revoke"],
    ["GET", "/api/admin/payments"], ["POST", "/api/admin/payments"], ["GET", "/api/admin/payments/1"],
    ["POST", "/api/admin/payments/1/cancel"], ["POST", "/api/admin/payments/1/confirm"],
    ["POST", "/api/admin/payments/1/reject"], ["GET", "/api/admin/payment-settings"],
    ["PUT", "/api/admin/payment-settings"], ["GET", "/api/admin/audit-log"],
    ["POST", `/api/admin/users/${userId}/reset-password`],
  ];
  for (const [method, route] of adminRoutes) {
    const refused = await call(method, route, { token: plainToken, body: {} });
    check(`non-admin is refused ${method} ${route.replace(/\d+/g, "{id}")} (403)`, refused.status === 403, `status ${refused.status}`);
  }

  // ---- Payment settings and payment records -----------------------------------------------
  const settings = await call("PUT", "/api/admin/payment-settings", {
    token: admin,
    body: { crypto: [{ network: "USDT-TRC20", address: "TTestWalletAddress123456" }], wire: { bank_name: "E2E Bank", account_number: "12345678" } },
  });
  check("admin updates payment details (200)", settings.status === 200, `status ${settings.status}`);
  check("payment details read back", (await call("GET", "/api/admin/payment-settings", { token: admin })).json?.crypto?.length === 1);

  const invoice = await call("POST", "/api/admin/payments", {
    token: admin, body: { user_id: userId, plan: "pro", method: "crypto", network: "USDT-TRC20", note: "e2e invoice" },
  });
  const paymentId = Number(invoice.json?.payment?.id ?? invoice.json?.id);
  check("admin creates a payment record for a user (200/201)", (invoice.status === 200 || invoice.status === 201) && Number.isInteger(paymentId), `status ${invoice.status} ${JSON.stringify(invoice.json)}`);

  const payDetail = await call("GET", `/api/admin/payments/${paymentId}`, { token: admin });
  check("admin reads one payment", payDetail.status === 200 && payDetail.json?.payment?.id === paymentId, `status ${payDetail.status}`);

  const cancel = await call("POST", `/api/admin/payments/${paymentId}/cancel`, { token: admin, body: { note: "e2e cancel" } });
  check("admin cancels an open payment (200)", cancel.status === 200, `status ${cancel.status}`);
  const cancelledInvoice = invoice.json?.payment?.invoice;
  check("cancelling the same invoice again is refused (409)",
    (await call("POST", `/api/admin/payments/${paymentId}/cancel`, { token: admin, body: { note: "e2e again" } })).status === 409);
  check("cancelled payment cannot be confirmed (409)",
    (await call("POST", `/api/admin/payments/${paymentId}/confirm`, { token: admin, body: { note: "x" } })).status === 409);

  // ---- Access grants: create, list, extend, revoke one, revoke all -------------------------
  const grant = await call("POST", `/api/admin/users/${userId}/grant`, {
    token: admin, body: { plan: "starter", days: 30, note: "e2e grant" },
  });
  const grantId = Number(grant.json?.grant?.id);
  check("admin grants access (200)", grant.status === 200 && Number.isInteger(grantId), `status ${grant.status}`);
  const me = await call("GET", "/api/me", { token: (await login(renamed, newPass)).json?.token });
  check("granted user has active access", me.json?.access?.active === true, JSON.stringify(me.json?.access));

  const grants = await call("GET", `/api/admin/grants?user_id=${userId}`, { token: admin });
  check("admin lists grants for a user", grants.status === 200 && Array.isArray(grants.json?.grants) && grants.json.grants.some((g) => g.id === grantId), `status ${grants.status}`);

  const extended = await call("PATCH", `/api/admin/grants/${grantId}`, { token: admin, body: { expires_in_days: 60, note: "e2e extend" } });
  check("admin extends a grant to 60 days (200)", extended.status === 200, `status ${extended.status} ${JSON.stringify(extended.json)}`);

  const revokeOne = await call("POST", `/api/admin/grants/${grantId}/revoke`, { token: admin, body: { note: "e2e revoke one" } });
  check("admin revokes one grant (200)", revokeOne.status === 200, `status ${revokeOne.status}`);
  const me2 = await call("GET", "/api/me", { token: (await login(renamed, newPass)).json?.token });
  check("revoked grant removes access", me2.json?.access?.active === false, JSON.stringify(me2.json?.access));

  await call("POST", `/api/admin/users/${userId}/grant`, { token: admin, body: { plan: "elite", days: 5, note: "e2e grant 2" } });
  const revokeAll = await call("POST", `/api/admin/users/${userId}/revoke`, { token: admin, body: { note: "e2e revoke all" } });
  check("revoke-all for a user still works (200)", revokeAll.status === 200 && revokeAll.json?.revoked >= 1, `status ${revokeAll.status}`);

  // ---- Payment review: user submits, admin confirms; second one is rejected ----------------
  const pay1 = await call("POST", "/api/payments", { token: (await login(renamed, newPass)).json?.token, body: { plan: "starter", method: "crypto", network: "USDT-TRC20" } });
  const pay1Id = Number(pay1.json?.payment?.id ?? pay1.json?.id);
  check("user opens a payment invoice (200/201)", (pay1.status === 200 || pay1.status === 201) && Number.isInteger(pay1Id), `status ${pay1.status}`);
  await call("POST", `/api/payments/${pay1Id}/reference`, { token: (await login(renamed, newPass)).json?.token, body: { reference: "0xE2E-TX-1", note: "sent" } });
  const confirm = await call("POST", `/api/admin/payments/${pay1Id}/confirm`, { token: admin, body: { note: "verified on chain" } });
  check("admin confirms a submitted payment (200)", confirm.status === 200, `status ${confirm.status} ${JSON.stringify(confirm.json)}`);
  const pay2 = await call("POST", "/api/payments", { token: (await login(renamed, newPass)).json?.token, body: { plan: "pro", method: "crypto", network: "USDT-TRC20" } });
  const pay2Id = Number(pay2.json?.payment?.id ?? pay2.json?.id);
  await call("POST", `/api/payments/${pay2Id}/reference`, { token: (await login(renamed, newPass)).json?.token, body: { reference: "0xE2E-TX-2", note: "sent" } });
  const reject = await call("POST", `/api/admin/payments/${pay2Id}/reject`, { token: admin, body: { note: "amount too low" } });
  check("admin rejects a submitted payment with a reason (200)", reject.status === 200, `status ${reject.status}`);

  const paymentsList = await call("GET", "/api/admin/payments?status=confirmed", { token: admin });
  check("admin filters payments by status", paymentsList.status === 200 && (paymentsList.json?.payments || []).some((p) => p.id === pay1Id));

  // ---- Activity log records the operator actions -----------------------------------------
  const audit = await call("GET", "/api/admin/audit-log?limit=500", { token: admin });
  const actions = new Set((audit.json?.events || []).map((e) => e.action));
  for (const action of ["user_created", "user_updated", "user_deleted", "password_reset", "access_granted", "access_changed", "access_revoked", "payment_created", "payment_cancelled", "payment_confirmed", "payment_rejected", "payment_settings_updated"]) {
    check(`activity log records ${action}`, actions.has(action), `missing ${action}`);
  }
  const cancelRows = (audit.json?.events || []).filter((e) => e.action === "payment_cancelled" && e.reference === cancelledInvoice);
  check("a refused second cancel wrote no audit row (exactly one payment_cancelled)", cancelRows.length === 1, `found ${cancelRows.length}`);

  check("bootstrap admin still signs in after all changes (200)", (await login(ADMIN_USER, ADMIN_PASS)).status === 200);

  // ---- Cleanup: remove the test accounts so re-runs stay clean ----------------------------
  for (const id of [userId]) {
    await call("PATCH", `/api/admin/users/${id}`, { token: admin, body: { disabled: true, note: "e2e cleanup" } });
  }

  console.log(`\n${results.length - failures} passed, ${failures} failed`);
  return failures === 0 ? 0 : 1;
}

main().then((code) => process.exit(code)).catch((error) => {
  console.error("E2E run crashed:", error.message);
  process.exit(1);
});
