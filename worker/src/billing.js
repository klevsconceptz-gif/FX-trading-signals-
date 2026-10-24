import { ApiError, requireDatabase } from "./http.js";
import { effectiveRole } from "./roles.js";
import { hashPassword, passwordPolicyError, randomToken } from "./security.js";

export const PLANS = Object.freeze({
  starter: { name: "Starter", price_usd: 29 },
  pro: { name: "Pro", price_usd: 79 },
  elite: { name: "Elite", price_usd: 199 },
});
export const ACCESS_DAYS = 30;
const SETTINGS_KEY = "payment_settings";
const WIRE_FIELDS = ["bank_name", "account_name", "account_number", "routing_or_swift", "iban", "instructions"];
const VALID_PAYMENT_STATUSES = new Set(["awaiting_payment", "submitted", "confirmed", "rejected", "cancelled"]);

function nowSeconds() {
  return Date.now() / 1000;
}

function emptySettings() {
  return { crypto: [], wire: Object.fromEntries(WIRE_FIELDS.map((field) => [field, ""])) };
}

function normalizeSettings(value) {
  const settings = emptySettings();
  if (!value || typeof value !== "object") return settings;
  if (Array.isArray(value.crypto)) settings.crypto = value.crypto.filter((item) => item && typeof item === "object").map((item) => ({
    network: String(item.network || ""),
    address: String(item.address || ""),
  }));
  if (value.wire && typeof value.wire === "object") {
    for (const field of WIRE_FIELDS) {
      if (value.wire[field] !== undefined) settings.wire[field] = String(value.wire[field] ?? "");
    }
  }
  return settings;
}

async function readSettings(env) {
  const row = await requireDatabase(env).prepare("SELECT value FROM settings WHERE key = ?").bind(SETTINGS_KEY).first();
  if (!row?.value) return emptySettings();
  try {
    return normalizeSettings(JSON.parse(row.value));
  } catch {
    throw new ApiError(500, "saved payment settings are invalid");
  }
}

function availableMethods(settings) {
  return {
    crypto: {
      available: settings.crypto.length > 0,
      networks: settings.crypto.map((entry) => entry.network),
    },
    wire: { available: Boolean(settings.wire.account_number || settings.wire.iban) },
  };
}

function planPayload() {
  return Object.fromEntries(Object.entries(PLANS).map(([key, plan]) => [key, {
    name: plan.name,
    price_usd: plan.price_usd,
    days: ACCESS_DAYS,
  }]));
}

export async function paymentOptions(env) {
  const settings = await readSettings(env);
  return { plans: planPayload(), methods: availableMethods(settings), access_days: ACCESS_DAYS };
}

export async function paymentSettings(env) {
  return readSettings(env);
}

export function paymentView(payment) {
  let payTo = {};
  try {
    payTo = payment.pay_to ? JSON.parse(payment.pay_to) : {};
  } catch {
    payTo = {};
  }
  const amount = Number(payment.amount_usd);
  return {
    id: Number(payment.id),
    invoice: payment.invoice,
    plan: payment.plan,
    plan_name: PLANS[payment.plan]?.name || payment.plan,
    amount_usd: amount,
    method: payment.method,
    network: payment.network,
    pay_to: payTo,
    status: payment.status,
    reference: payment.reference,
    sender_note: payment.sender_note,
    created_at: Number(payment.created_at),
    submitted_at: payment.submitted_at == null ? null : Number(payment.submitted_at),
    decided_at: payment.decided_at == null ? null : Number(payment.decided_at),
    decided_by: payment.decided_by,
    decision_note: payment.decision_note,
    payment_instruction: payment.method === "wire"
      ? `Quote invoice ${payment.invoice} in the payment note / memo.`
      : `Send exactly $${amount.toFixed(2)} worth of ${payment.network} to the address shown, then submit the transaction hash below.`,
  };
}

async function paymentById(env, paymentId) {
  return requireDatabase(env).prepare("SELECT * FROM payments WHERE id = ?").bind(paymentId).first();
}

export async function myPayments(env, userId, limit = 100) {
  const result = await requireDatabase(env).prepare(`
    SELECT * FROM payments WHERE user_id = ? ORDER BY created_at DESC LIMIT ?
  `).bind(userId, limit).all();
  return (result.results || []).map(paymentView);
}

export async function getMe(env, user, access) {
  const [payments, settings] = await Promise.all([
    myPayments(env, user.id, 10),
    readSettings(env),
  ]);
  return { user, access, payments, methods: availableMethods(settings) };
}

function createInvoice() {
  const bytes = crypto.getRandomValues(new Uint8Array(3));
  return `KFX-${Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("").toUpperCase()}`;
}

export async function createPayment(env, userId, body) {
  const plan = String(body.plan || "");
  const method = String(body.method || "");
  if (!Object.hasOwn(PLANS, plan)) {
    throw new ApiError(422, `unknown plan '${plan}' — choose starter, pro or elite`);
  }
  if (method !== "crypto" && method !== "wire") {
    throw new ApiError(422, "method must be 'crypto' or 'wire'");
  }
  let network = body.network == null ? "" : String(body.network);
  if (network.length > 16) throw new ApiError(422, "network must be 16 characters or fewer");

  const settings = await readSettings(env);
  let payTo;
  if (method === "crypto") {
    const wallets = new Map(settings.crypto.map((item) => [item.network.toUpperCase(), item.address]));
    if (!wallets.size) {
      throw new ApiError(422, "crypto payments are not set up yet — the operator has not added a wallet");
    }
    network = network.trim().toUpperCase();
    if (!wallets.has(network)) throw new ApiError(422, `choose a network: ${Array.from(wallets.keys()).join(", ")}`);
    payTo = { network, address: wallets.get(network) };
  } else {
    if (!settings.wire.account_number && !settings.wire.iban) {
      throw new ApiError(422, "wire transfer is not set up yet — the operator has not added bank details");
    }
    network = null;
    payTo = Object.fromEntries(Object.entries(settings.wire).filter(([, value]) => Boolean(value)));
  }

  const db = requireDatabase(env);
  const open = await db.prepare(`
    SELECT invoice, status FROM payments
     WHERE user_id = ? AND status IN ('awaiting_payment', 'submitted')
     ORDER BY created_at DESC
  `).bind(userId).all();
  const submitted = (open.results || []).find((row) => row.status === "submitted");
  if (submitted) {
    throw new ApiError(422, `invoice ${submitted.invoice} is awaiting review — wait for the operator's decision before starting another payment`);
  }

  let invoice;
  for (let attempt = 0; attempt < 5; attempt += 1) {
    invoice = createInvoice();
    const exists = await db.prepare("SELECT id FROM payments WHERE invoice = ?").bind(invoice).first();
    if (!exists) break;
    invoice = null;
  }
  if (!invoice) throw new ApiError(503, "could not allocate an invoice; try again");

  const timestamp = nowSeconds();
  let result;
  try {
    result = await db.batch([
      db.prepare(`
        UPDATE payments SET status = 'cancelled'
         WHERE user_id = ? AND status = 'awaiting_payment'
      `).bind(userId),
      db.prepare(`
        INSERT INTO payments
          (user_id, invoice, plan, amount_usd, method, network, pay_to, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'awaiting_payment', ?)
      `).bind(userId, invoice, plan, PLANS[plan].price_usd, method, network, JSON.stringify(payTo), timestamp),
    ]);
  } catch (error) {
    if (String(error?.message || error).includes("UNIQUE constraint failed")) {
      const pending = await db.prepare(`
        SELECT invoice, status FROM payments
         WHERE user_id = ? AND status IN ('awaiting_payment', 'submitted')
         ORDER BY created_at DESC LIMIT 1
      `).bind(userId).first();
      if (pending?.status === "submitted") {
        throw new ApiError(422, `invoice ${pending.invoice} is awaiting review — wait for the operator's decision before starting another payment`);
      }
      throw new ApiError(409, "another invoice is being created; try again");
    }
    throw error;
  }
  const payment = await db.prepare("SELECT * FROM payments WHERE invoice = ?").bind(invoice).first();
  if (!payment) throw new ApiError(500, "invoice creation failed");
  return { ok: true, payment: paymentView(payment) };
}

export async function listMyPayments(env, userId) {
  return { payments: await myPayments(env, userId) };
}

export async function submitPaymentReference(env, userId, paymentId, body) {
  const reference = typeof body.reference === "string" ? body.reference.trim() : "";
  const senderNote = typeof body.sender_note === "string" ? body.sender_note.trim().slice(0, 300) : "";
  if (reference.length < 4 || reference.length > 200) {
    throw new ApiError(422, "enter the transaction hash or wire reference (4-200 characters)");
  }
  const timestamp = nowSeconds();
  const result = await requireDatabase(env).prepare(`
    UPDATE payments
       SET reference = ?, sender_note = ?, status = 'submitted', submitted_at = ?
     WHERE id = ? AND user_id = ? AND status = 'awaiting_payment'
  `).bind(reference, senderNote || null, timestamp, paymentId, userId).run();
  if (!result.meta.changes) {
    const payment = await requireDatabase(env).prepare("SELECT id, user_id, status FROM payments WHERE id = ?").bind(paymentId).first();
    if (!payment || payment.user_id !== userId) throw new ApiError(404, "payment not found");
    throw new ApiError(422, `this invoice is ${String(payment.status).replaceAll("_", " ")} — nothing to submit`);
  }
  const payment = await paymentById(env, paymentId);
  return {
    ok: true,
    payment: paymentView(payment),
    message: "Submitted. The operator will verify the payment and unlock your access.",
  };
}

export async function adminListPayments(env, status) {
  if (status && !VALID_PAYMENT_STATUSES.has(status)) throw new ApiError(422, "invalid payment status");
  const db = requireDatabase(env);
  const result = status
    ? await db.prepare(`
        SELECT p.*, u.username AS user_username, u.email AS user_email, u.phone AS user_phone, u.id AS user_id
          FROM payments p JOIN users u ON u.id = p.user_id
         WHERE p.status = ? ORDER BY p.created_at DESC
      `).bind(status).all()
    : await db.prepare(`
        SELECT p.*, u.username AS user_username, u.email AS user_email, u.phone AS user_phone, u.id AS user_id
          FROM payments p JOIN users u ON u.id = p.user_id
         ORDER BY p.created_at DESC
      `).all();
  return { payments: (result.results || []).map((row) => ({
    ...paymentView(row),
    user: { id: Number(row.user_id), username: row.user_username, email: row.user_email, phone: row.user_phone },
  })) };
}

export async function confirmPayment(env, paymentId, operator, note = "") {
  const db = requireDatabase(env);
  const timestamp = nowSeconds();
  const decisionNote = String(note || "").trim().slice(0, 300) || null;
  const grantKey = `payment:${paymentId}`;
  const updated = await db.batch([
    db.prepare(`
      UPDATE payments
         SET status = 'confirmed', decided_at = ?, decided_by = ?, decision_note = ?
       WHERE id = ? AND status = 'submitted'
    `).bind(timestamp, operator, decisionNote, paymentId),
    db.prepare(`
      INSERT OR IGNORE INTO access_grants
        (grant_key, user_id, plan, status, starts_at, expires_at, granted_by, payment_id, note)
      SELECT ?, p.user_id, p.plan, 'active',
             MAX(?, COALESCE((SELECT MAX(g.expires_at) FROM access_grants g
                               WHERE g.user_id = p.user_id AND g.status = 'active' AND g.expires_at > ?), ?)),
             MAX(?, COALESCE((SELECT MAX(g.expires_at) FROM access_grants g
                               WHERE g.user_id = p.user_id AND g.status = 'active' AND g.expires_at > ?), ?)) + ?,
             ?, p.id, p.decision_note
        FROM payments p
       WHERE p.id = ? AND p.status = 'confirmed' AND p.decided_by = ?
         AND NOT EXISTS (SELECT 1 FROM access_grants g WHERE g.payment_id = p.id)
    `).bind(grantKey, timestamp, timestamp, timestamp, timestamp, timestamp, timestamp, ACCESS_DAYS * 86400,
      operator, paymentId, operator),
    db.prepare(`
      INSERT OR IGNORE INTO admin_audit_events
        (event_key, occurred_at, actor_username, action, target_user_id, target_username,
         payment_id, grant_id, reference, summary, note)
      SELECT 'payment_confirmed:' || p.id, ?, ?, 'payment_confirmed', u.id, u.username,
             p.id, g.id, p.invoice, 'Confirmed payment; granted ${ACCESS_DAYS} days of ' || p.plan || ' access', p.decision_note
        FROM payments p
        JOIN users u ON u.id = p.user_id
        JOIN access_grants g ON g.payment_id = p.id
       WHERE p.id = ? AND p.status = 'confirmed' AND p.decided_by = ?
    `).bind(timestamp, operator, paymentId, operator),
  ]);
  if (!updated[0]?.meta?.changes) {
    const existing = await paymentById(env, paymentId);
    if (!existing) throw new ApiError(404, "payment not found");
    throw new ApiError(409, `only submitted payments can be confirmed (this one is ${String(existing.status).replaceAll("_", " ")})`);
  }
  const payment = await paymentById(env, paymentId);
  return { ok: true, payment: paymentView(payment) };
}

export async function rejectPayment(env, paymentId, operator, note) {
  const reason = typeof note === "string" ? note.trim() : "";
  if (reason.length < 3 || reason.length > 300) throw new ApiError(422, "give the reason for rejecting this payment");
  const db = requireDatabase(env);
  const timestamp = nowSeconds();
  const updated = await db.batch([
    db.prepare(`
      UPDATE payments
         SET status = 'rejected', decided_at = ?, decided_by = ?, decision_note = ?
       WHERE id = ? AND status = 'submitted'
    `).bind(timestamp, operator, reason, paymentId),
    db.prepare(`
      INSERT OR IGNORE INTO admin_audit_events
        (event_key, occurred_at, actor_username, action, target_user_id, target_username,
         payment_id, reference, summary, note)
      SELECT 'payment_rejected:' || p.id, ?, ?, 'payment_rejected', u.id, u.username,
             p.id, p.invoice, 'Rejected payment', p.decision_note
        FROM payments p JOIN users u ON u.id = p.user_id
       WHERE p.id = ? AND p.status = 'rejected' AND p.decided_by = ?
    `).bind(timestamp, operator, paymentId, operator),
  ]);
  if (!updated[0]?.meta?.changes) {
    const existing = await paymentById(env, paymentId);
    if (!existing) throw new ApiError(404, "payment not found");
    throw new ApiError(409, `only submitted payments can be rejected (this one is ${String(existing.status).replaceAll("_", " ")})`);
  }
  const payment = await paymentById(env, paymentId);
  return { ok: true, payment: paymentView(payment) };
}

export async function adminListUsers(env) {
  const result = await requireDatabase(env).prepare(`
    SELECT u.id, u.username, u.email, u.phone, u.role, u.created_at, u.disabled_at,
           g.plan AS grant_plan, g.expires_at AS grant_expires_at
      FROM users u
      LEFT JOIN access_grants g ON g.id = (
        SELECT active.id FROM access_grants active
         WHERE active.user_id = u.id AND active.status = 'active' AND active.expires_at > ?
         ORDER BY active.expires_at DESC LIMIT 1
      )
     ORDER BY u.created_at DESC
  `).bind(nowSeconds()).all();
  return { users: (result.results || []).map((user) => ({
    id: Number(user.id),
    username: user.username,
    email: user.email,
    phone: user.phone,
    role: effectiveRole(env, user),
    disabled: Number(user.disabled_at || 0) > 0,
    created_at: Number(user.created_at),
    access: {
      active: effectiveRole(env, user) === "admin" || user.grant_plan !== null,
      plan: effectiveRole(env, user) === "admin" ? "admin" : user.grant_plan,
      expires_at: user.grant_expires_at == null ? null : Number(user.grant_expires_at),
    },
  })) };
}

export async function grantAccess(env, userId, operator, body) {
  const plan = String(body.plan || "");
  const days = Number(body.days === undefined ? ACCESS_DAYS : body.days);
  const note = typeof body.note === "string" ? body.note.trim() : "";
  if (!Object.hasOwn(PLANS, plan)) throw new ApiError(422, `unknown plan '${plan}'`);
  if (!Number.isInteger(days) || days < 1 || days > 366) throw new ApiError(422, "days must be between 1 and 366");
  if (note.length < 3 || note.length > 300) throw new ApiError(422, "give a note explaining the manual grant");
  const db = requireDatabase(env);
  const user = await db.prepare("SELECT id FROM users WHERE id = ?").bind(userId).first();
  if (!user) throw new ApiError(404, "user not found");
  const key = `manual:${randomToken(18)}`;
  const timestamp = nowSeconds();
  await db.batch([
    db.prepare(`
      INSERT INTO access_grants
        (grant_key, user_id, plan, status, starts_at, expires_at, granted_by, payment_id, note)
      SELECT ?, ?, ?, 'active',
             MAX(?, COALESCE((SELECT MAX(expires_at) FROM access_grants
                               WHERE user_id = ? AND status = 'active' AND expires_at > ?), ?)),
             MAX(?, COALESCE((SELECT MAX(expires_at) FROM access_grants
                               WHERE user_id = ? AND status = 'active' AND expires_at > ?), ?)) + ?,
             ?, NULL, ?
       WHERE EXISTS (SELECT 1 FROM users WHERE id = ?)
    `).bind(key, userId, plan, timestamp, userId, timestamp, timestamp,
      timestamp, userId, timestamp, timestamp, days * 86400, operator, note.slice(0, 300), userId),
    db.prepare(`
      INSERT OR IGNORE INTO admin_audit_events
        (event_key, occurred_at, actor_username, action, target_user_id, target_username,
         grant_id, reference, summary, note)
      SELECT ?, ?, ?, 'access_granted', u.id, u.username, g.id, 'grant #' || g.id,
             'Granted ' || g.plan || ' access for ' || ? || ' days', g.note
        FROM access_grants g JOIN users u ON u.id = g.user_id
       WHERE g.grant_key = ?
    `).bind(`grant:${key}`, timestamp, operator, days, key),
  ]);
  const grant = await db.prepare("SELECT id, plan, expires_at FROM access_grants WHERE grant_key = ?").bind(key).first();
  if (!grant) throw new ApiError(404, "user not found");
  return { ok: true, grant: { id: Number(grant.id), plan: grant.plan, expires_at: Number(grant.expires_at) } };
}

/**
 * Operator sets a new password for an account. The operator chooses the password; nothing
 * is generated. Bumping password_version ends every session of the account (currentUser
 * compares it on each request, including KV-cached sessions). The password is never written
 * to the audit trail. The bootstrap admin (KFX_ADMIN_USERNAME) is refused, because its
 * password is re-seeded from the Worker secret on each admin sign-in.
 */
export async function adminResetPassword(env, userId, operator, body) {
  const newPassword = typeof body.new_password === "string" ? body.new_password : "";
  const note = typeof body.note === "string" ? body.note.trim() : "";
  if (!newPassword || newPassword.length > 128) throw new ApiError(422, "password must be 1-128 characters");
  const policyError = passwordPolicyError(newPassword);
  if (policyError) throw new ApiError(422, policyError);
  if (note.length < 3 || note.length > 300) throw new ApiError(422, "give a note explaining the password reset");

  const db = requireDatabase(env);
  const user = await db.prepare("SELECT id, username, role FROM users WHERE id = ?").bind(userId).first();
  if (!user) throw new ApiError(404, "user not found");
  const bootstrap = String(env.KFX_ADMIN_USERNAME || "").trim().toLowerCase();
  if (bootstrap && user.username.toLowerCase() === bootstrap) {
    throw new ApiError(409, "this is the bootstrap admin: change KFX_ADMIN_PASSWORD on the Worker and redeploy instead");
  }

  const passwordHash = await hashPassword(newPassword);
  const timestamp = nowSeconds();
  await db.batch([
    db.prepare(`
      UPDATE users
         SET password_hash = ?, password_version = password_version + 1, reset_hash = NULL,
             reset_expires = 0, reset_attempts = 0, failed_logins = 0, locked_until = 0
       WHERE id = ?
    `).bind(passwordHash, userId),
    db.prepare("DELETE FROM user_sessions WHERE user_id = ?").bind(userId),
    db.prepare(`
      INSERT INTO admin_audit_events
        (event_key, occurred_at, actor_username, action, target_user_id, target_username, summary, note)
      VALUES (?, ?, ?, 'password_reset', ?, ?, 'Set a new password; existing sessions ended', ?)
    `).bind(`password-reset:${randomToken(12)}`, timestamp, operator, user.id, user.username, note.slice(0, 300)),
  ]);
  return { ok: true, user_id: Number(user.id) };
}

export async function revokeAccess(env, userId, operator, body) {
  const note = typeof body.note === "string" ? body.note.trim() : "";
  if (note.length < 3 || note.length > 300) throw new ApiError(422, "give a note explaining the revocation");
  const db = requireDatabase(env);
  const user = await db.prepare("SELECT id FROM users WHERE id = ?").bind(userId).first();
  if (!user) throw new ApiError(404, "user not found");
  const timestamp = nowSeconds();
  const updated = await db.batch([
    db.prepare(`
      UPDATE access_grants
         SET status = 'revoked', revoked_at = ?, revoked_by = ?, revoke_note = ?
       WHERE user_id = ? AND status = 'active'
    `).bind(timestamp, operator, note.slice(0, 300), userId),
    db.prepare(`
      INSERT OR IGNORE INTO admin_audit_events
        (event_key, occurred_at, actor_username, action, target_user_id, target_username,
         grant_id, reference, summary, note)
      SELECT 'revoked:' || g.id || ':' || ?, ?, ?, 'access_revoked', u.id, u.username,
             g.id, 'grant #' || g.id, 'Revoked ' || g.plan || ' access grant', g.revoke_note
        FROM access_grants g JOIN users u ON u.id = g.user_id
       WHERE g.user_id = ? AND g.status = 'revoked' AND g.revoked_at = ? AND g.revoked_by = ?
    `).bind(timestamp, timestamp, operator, userId, timestamp, operator),
  ]);
  return { ok: true, revoked: Number(updated[0]?.meta?.changes || 0) };
}

function validatePaymentSettings(body) {
  const cryptoInput = Array.isArray(body.crypto) ? body.crypto : [];
  if (cryptoInput.length > 20) throw new ApiError(422, "too many crypto payment networks");
  const crypto = [];
  const seen = new Set();
  for (const entry of cryptoInput) {
    const network = String(entry?.network || "").trim().toUpperCase();
    const address = String(entry?.address || "").trim();
    if (!network && !address) continue;
    if (!network || !address) throw new ApiError(422, "each crypto entry needs a network and a wallet address");
    if (!/^[A-Z0-9.\-]{2,16}$/.test(network)) {
      throw new ApiError(422, `invalid network name '${network}' (e.g. BTC, ETH, USDT-TRC20)`);
    }
    if (!/^[A-Za-z0-9]{8,120}$/.test(address)) throw new ApiError(422, `invalid ${network} wallet address`);
    if (seen.has(network)) throw new ApiError(422, `network ${network} is listed twice`);
    seen.add(network);
    crypto.push({ network, address });
  }

  const wireInput = body.wire && typeof body.wire === "object" ? body.wire : {};
  const wire = Object.fromEntries(WIRE_FIELDS.map((field) => [field, String(wireInput[field] || "").trim().slice(0, 300)]));
  if (Object.values(wire).some(Boolean) && !wire.account_number && !wire.iban) {
    throw new ApiError(422, "wire details need an account number or an IBAN");
  }
  return { crypto, wire };
}

export async function updatePaymentSettings(env, operator, body) {
  const db = requireDatabase(env);
  const previous = await readSettings(env);
  const data = validatePaymentSettings(body);
  if (JSON.stringify(previous) !== JSON.stringify(data)) {
    const timestamp = nowSeconds();
    const wireEnabled = Boolean(data.wire.account_number || data.wire.iban);
    const auditNote = `Crypto networks: ${data.crypto.length}; bank wire: ${wireEnabled ? "enabled" : "disabled"}`;
    const eventKey = `payment-settings:${randomToken(12)}`;
    await db.batch([
      db.prepare(`
        INSERT INTO settings(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
      `).bind(SETTINGS_KEY, JSON.stringify(data)),
      db.prepare(`
        INSERT INTO admin_audit_events
          (event_key, occurred_at, actor_username, action, summary, note)
        VALUES (?, ?, ?, 'payment_settings_updated', 'Updated payment settings', ?)
      `).bind(eventKey, timestamp, operator, auditNote),
    ]);
  }
  return { ok: true, settings: data };
}

export async function listAdminAudit(env, limitValue = 100) {
  const limit = Number(limitValue);
  if (!Number.isInteger(limit) || limit < 1 || limit > 500) throw new ApiError(422, "limit must be between 1 and 500");
  const result = await requireDatabase(env).prepare(`
    SELECT id AS event_id, occurred_at, actor_username AS actor, action,
           target_username, reference, summary, note
      FROM admin_audit_events
     ORDER BY occurred_at DESC, id DESC
     LIMIT ?
  `).bind(limit).all();
  return {
    events: (result.results || []).map((event) => ({ ...event, legacy: false })),
  };
}
