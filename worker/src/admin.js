// Operator (admin) CRUD for accounts, access grants and payment records. Every change writes an
// audit event in the same D1 batch, and every mutation needs a note. Passwords are never stored
// in the audit trail or returned by any view.
import { ApiError, requireDatabase } from "./http.js";
import { publicUser } from "./auth.js";
import { createPayment, paymentView } from "./billing.js";
import {
  emailIsValid,
  hashPassword,
  passwordPolicyError,
  phoneIsValid,
  phoneKey,
  randomToken,
  usernameIsValid,
} from "./security.js";

const ROLES = new Set(["user", "admin"]);
const GRANT_STATUSES = new Set(["active", "revoked"]);
const MAX_GRANT_DAYS = 366;

function nowSeconds() {
  return Date.now() / 1000;
}

function requireNote(value) {
  const note = typeof value === "string" ? value.trim() : "";
  if (note.length < 3 || note.length > 300) {
    throw new ApiError(422, "give a note explaining this change (3-300 characters)");
  }
  return note;
}

function bootstrapUsername(env) {
  return String(env.KFX_ADMIN_USERNAME || "").trim().toLowerCase();
}

// The bootstrap admin is seeded from Worker secrets on every admin sign-in, so its identity and
// status cannot be changed from the console.
function isBootstrap(env, username) {
  const bootstrap = bootstrapUsername(env);
  return Boolean(bootstrap) && String(username).toLowerCase() === bootstrap;
}

function text(value) {
  return typeof value === "string" ? value.trim() : "";
}

function userView(env, row) {
  return {
    ...publicUser(row),
    email: row.email,
    phone: row.phone || "",
    disabled: Number(row.disabled_at || 0) > 0,
    created_at: Number(row.created_at),
    managed_by_env: isBootstrap(env, row.username),
  };
}

function grantView(row, now = nowSeconds()) {
  return {
    id: Number(row.id),
    user_id: Number(row.user_id),
    username: row.username ?? null,
    plan: row.plan,
    status: row.status,
    active: row.status === "active" && Number(row.expires_at) > now,
    starts_at: Number(row.starts_at),
    expires_at: Number(row.expires_at),
    granted_by: row.granted_by,
    payment_id: row.payment_id == null ? null : Number(row.payment_id),
    note: row.note || "",
    revoked_at: row.revoked_at == null ? null : Number(row.revoked_at),
    revoked_by: row.revoked_by || null,
    revoke_note: row.revoke_note || "",
  };
}

async function getUser(db, userId) {
  const row = await db.prepare("SELECT * FROM users WHERE id = ?").bind(userId).first();
  if (!row) throw new ApiError(404, "user not found");
  return row;
}

async function getGrant(db, grantId) {
  const row = await db.prepare(`
    SELECT g.*, u.username FROM access_grants g JOIN users u ON u.id = g.user_id WHERE g.id = ?
  `).bind(grantId).first();
  if (!row) throw new ApiError(404, "access grant not found");
  return row;
}

function auditStatement(db, { operator, action, target, summary, note = null, paymentId = null, grantId = null, reference = null }) {
  return db.prepare(`
    INSERT INTO admin_audit_events
      (event_key, occurred_at, actor_username, action, target_user_id, target_username,
       payment_id, grant_id, reference, summary, note)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).bind(
    `${action}:${randomToken(12)}`,
    nowSeconds(),
    operator,
    action,
    target ? Number(target.id) : null,
    target ? target.username : null,
    paymentId,
    grantId,
    reference,
    summary,
    note,
  );
}

async function assertUnique(db, column, value, exceptUserId) {
  const row = await db.prepare(`SELECT id FROM users WHERE ${column} = ? AND id <> ? LIMIT 1`)
    .bind(value, exceptUserId).first();
  return Boolean(row);
}

function isUniqueViolation(error) {
  return String(error?.message || error).includes("UNIQUE constraint failed");
}

// ---- Users ---------------------------------------------------------------------------------

export async function adminCreateUser(env, operator, body) {
  const username = text(body.username);
  const email = text(body.email).toLowerCase();
  const phone = text(body.phone);
  const password = typeof body.password === "string" ? body.password : "";
  const role = body.role === undefined ? "user" : String(body.role);
  const note = requireNote(body.note);

  if (!usernameIsValid(username)) throw new ApiError(422, "username must be 3-32 characters: letters, numbers, _ . - only");
  if (!emailIsValid(email)) throw new ApiError(422, "invalid email address");
  if (phone && !phoneIsValid(phone)) throw new ApiError(422, "invalid phone number (digits, +, -, spaces, parentheses; 7-20 chars)");
  if (!password || password.length > 128) throw new ApiError(422, "password must be 1-128 characters");
  const policyError = passwordPolicyError(password);
  if (policyError) throw new ApiError(422, policyError);
  if (!ROLES.has(role)) throw new ApiError(422, "role must be 'user' or 'admin'");

  const db = requireDatabase(env);
  const usernameKey = username.toLowerCase();
  const normalizedPhone = phone ? phoneKey(phone) : null;
  if (await db.prepare("SELECT id FROM users WHERE username_key = ?").bind(usernameKey).first()) {
    throw new ApiError(409, "username is already taken");
  }
  if (await db.prepare("SELECT id FROM users WHERE email = ?").bind(email).first()) {
    throw new ApiError(409, "an account with that email already exists");
  }
  if (normalizedPhone && await db.prepare("SELECT id FROM users WHERE phone_key = ?").bind(normalizedPhone).first()) {
    throw new ApiError(409, "an account with that phone number already exists");
  }

  const passwordHash = await hashPassword(password);
  const timestamp = nowSeconds();
  let created;
  try {
    created = await db.prepare(`
      INSERT INTO users
        (username, username_key, email, phone, phone_key, password_hash, role,
         created_at, reset_expires, reset_attempts, failed_logins, locked_until, password_version)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, 1)
    `).bind(username, usernameKey, email, phone || null, normalizedPhone, passwordHash, role, timestamp).run();
  } catch (error) {
    if (isUniqueViolation(error)) throw new ApiError(409, "an account with these details already exists");
    throw error;
  }
  const id = Number(created.meta.last_row_id);
  const row = await getUser(db, id);
  await auditStatement(db, {
    operator,
    action: "user_created",
    target: row,
    summary: `Created ${role} account`,
    note,
  }).run();
  return { ok: true, user: userView(env, row) };
}

export async function adminGetUser(env, userId) {
  const db = requireDatabase(env);
  const row = await getUser(db, userId);
  const [grants, payments, events] = await Promise.all([
    db.prepare(`
      SELECT g.*, u.username FROM access_grants g JOIN users u ON u.id = g.user_id
       WHERE g.user_id = ? ORDER BY g.id DESC
    `).bind(userId).all(),
    db.prepare("SELECT * FROM payments WHERE user_id = ? ORDER BY created_at DESC").bind(userId).all(),
    db.prepare(`
      SELECT id AS event_id, occurred_at, actor_username AS actor, action, target_username,
             reference, summary, note
        FROM admin_audit_events WHERE target_user_id = ?
       ORDER BY occurred_at DESC, id DESC LIMIT 50
    `).bind(userId).all(),
  ]);
  return {
    user: userView(env, row),
    grants: (grants.results || []).map((grant) => grantView(grant)),
    payments: (payments.results || []).map(paymentView),
    events: events.results || [],
  };
}

export async function adminUpdateUser(env, userId, actor, body) {
  const note = requireNote(body.note);
  const db = requireDatabase(env);
  const user = await getUser(db, userId);
  if (isBootstrap(env, user.username)) {
    throw new ApiError(409, "this is the bootstrap admin: its username, email, phone, role and status come from the Worker secrets — change them there and redeploy");
  }

  const next = {
    username: user.username,
    email: user.email,
    phone: user.phone || null,
    role: user.role,
    disabled_at: Number(user.disabled_at || 0),
  };
  const changes = [];

  if (body.username !== undefined) {
    const username = text(body.username);
    if (!usernameIsValid(username)) throw new ApiError(422, "username must be 3-32 characters: letters, numbers, _ . - only");
    if (username !== user.username) {
      if (await assertUnique(db, "username_key", username.toLowerCase(), userId)) throw new ApiError(409, "username is already taken");
      next.username = username;
      changes.push("username");
    }
  }
  if (body.email !== undefined) {
    const email = text(body.email).toLowerCase();
    if (!emailIsValid(email)) throw new ApiError(422, "invalid email address");
    if (email !== user.email) {
      if (await assertUnique(db, "email", email, userId)) throw new ApiError(409, "an account with that email already exists");
      next.email = email;
      changes.push("email");
    }
  }
  if (body.phone !== undefined) {
    const phone = text(body.phone);
    if (phone && !phoneIsValid(phone)) throw new ApiError(422, "invalid phone number (digits, +, -, spaces, parentheses; 7-20 chars)");
    const nextPhone = phone || null;
    if (nextPhone !== (user.phone || null)) {
      if (nextPhone && await db.prepare("SELECT id FROM users WHERE phone_key = ? AND id <> ?").bind(phoneKey(nextPhone), userId).first()) {
        throw new ApiError(409, "an account with that phone number already exists");
      }
      next.phone = nextPhone;
      changes.push("phone");
    }
  }
  if (body.role !== undefined) {
    const role = String(body.role);
    if (!ROLES.has(role)) throw new ApiError(422, "role must be 'user' or 'admin'");
    if (role !== user.role) {
      if (Number(actor.id) === userId && role === "user") throw new ApiError(409, "you cannot remove your own admin role");
      next.role = role;
      changes.push(`role ${user.role}→${role}`);
    }
  }
  if (body.disabled !== undefined) {
    if (typeof body.disabled !== "boolean") throw new ApiError(422, "disabled must be true or false");
    const wasDisabled = Number(user.disabled_at || 0) > 0;
    if (body.disabled !== wasDisabled) {
      if (body.disabled && Number(actor.id) === userId) throw new ApiError(409, "you cannot disable your own account");
      next.disabled_at = body.disabled ? nowSeconds() : 0;
      changes.push(body.disabled ? "disabled" : "enabled");
    }
  }
  if (changes.length === 0) throw new ApiError(422, "nothing to change");

  const statements = [
    db.prepare(`
      UPDATE users SET username = ?, username_key = ?, email = ?, phone = ?, phone_key = ?, role = ?, disabled_at = ?
       WHERE id = ?
    `).bind(
      next.username,
      next.username.toLowerCase(),
      next.email,
      next.phone,
      next.phone ? phoneKey(next.phone) : null,
      next.role,
      next.disabled_at,
      userId,
    ),
  ];
  // Losing admin rights or being disabled ends every session of the account at once.
  if (changes.some((c) => c.startsWith("role ") && next.role === "user") || changes.includes("disabled")) {
    statements.push(db.prepare("DELETE FROM user_sessions WHERE user_id = ?").bind(userId));
  }
  statements.push(auditStatement(db, {
    operator: actor.username,
    action: "user_updated",
    target: { id: userId, username: next.username },
    summary: `Updated ${changes.join(", ")}`,
    note,
  }));
  try {
    await db.batch(statements);
  } catch (error) {
    if (isUniqueViolation(error)) throw new ApiError(409, "that username, email or phone is already in use");
    throw error;
  }
  return { ok: true, user: userView(env, await getUser(db, userId)) };
}

export async function adminDeleteUser(env, userId, actor, body) {
  const note = requireNote(body.note);
  const db = requireDatabase(env);
  const user = await getUser(db, userId);
  if (isBootstrap(env, user.username)) throw new ApiError(409, "the bootstrap admin cannot be deleted");
  if (Number(actor.id) === userId) throw new ApiError(409, "you cannot delete your own account");
  const records = await db.prepare(`
    SELECT (SELECT COUNT(*) FROM payments WHERE user_id = ?) AS payments,
           (SELECT COUNT(*) FROM access_grants WHERE user_id = ?) AS grants
  `).bind(userId, userId).first();
  if (Number(records.payments) > 0 || Number(records.grants) > 0) {
    throw new ApiError(409, "this account has payment or access records, which are kept for audit — disable it instead");
  }
  await db.batch([
    db.prepare("DELETE FROM user_sessions WHERE user_id = ?").bind(userId),
    db.prepare("DELETE FROM users WHERE id = ?").bind(userId),
    auditStatement(db, {
      operator: actor.username,
      action: "user_deleted",
      target: user,
      summary: "Deleted account with no payment or access records",
      note,
    }),
  ]);
  return { ok: true, deleted_user_id: userId };
}

// ---- Access grants ------------------------------------------------------------------------

export async function adminListGrants(env, query) {
  const db = requireDatabase(env);
  const clauses = [];
  const values = [];
  if (query.get("user_id")) {
    const userId = Number(query.get("user_id"));
    if (!Number.isInteger(userId) || userId < 1) throw new ApiError(422, "user_id must be a positive integer");
    clauses.push("g.user_id = ?");
    values.push(userId);
  }
  if (query.get("status")) {
    const status = query.get("status");
    if (!GRANT_STATUSES.has(status)) throw new ApiError(422, "status must be 'active' or 'revoked'");
    clauses.push("g.status = ?");
    values.push(status);
  }
  const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
  const result = await db.prepare(`
    SELECT g.*, u.username FROM access_grants g JOIN users u ON u.id = g.user_id
    ${where} ORDER BY g.id DESC LIMIT 500
  `).bind(...values).all();
  return { grants: (result.results || []).map((row) => grantView(row)) };
}

export async function adminUpdateGrant(env, grantId, actor, body) {
  const note = requireNote(body.note);
  const days = Number(body.expires_in_days);
  if (!Number.isInteger(days) || days < 1 || days > MAX_GRANT_DAYS) {
    throw new ApiError(422, `expires_in_days must be between 1 and ${MAX_GRANT_DAYS}`);
  }
  const db = requireDatabase(env);
  const grant = await getGrant(db, grantId);
  if (grant.status !== "active") throw new ApiError(409, "only active grants can be changed");
  const timestamp = nowSeconds();
  const expiresAt = timestamp + days * 86400;
  const [updated] = await db.batch([
    db.prepare("UPDATE access_grants SET expires_at = ? WHERE id = ? AND status = 'active'").bind(expiresAt, grantId),
    auditStatement(db, {
      operator: actor.username,
      action: "access_changed",
      target: { id: grant.user_id, username: grant.username },
      summary: `Set grant #${grantId} to expire in ${days} days`,
      note,
      grantId,
      reference: `grant #${grantId}`,
    }),
  ]);
  if (!updated?.meta?.changes) throw new ApiError(409, "the grant changed while it was being updated; reload and try again");
  return { ok: true, grant: grantView(await getGrant(db, grantId)) };
}

export async function adminRevokeGrant(env, grantId, actor, body) {
  const note = requireNote(body.note);
  const db = requireDatabase(env);
  const grant = await getGrant(db, grantId);
  if (grant.status !== "active") throw new ApiError(409, "this grant is already revoked");
  const timestamp = nowSeconds();
  const [updated] = await db.batch([
    db.prepare(`
      UPDATE access_grants SET status = 'revoked', revoked_at = ?, revoked_by = ?, revoke_note = ?
       WHERE id = ? AND status = 'active'
    `).bind(timestamp, actor.username, note.slice(0, 300), grantId),
    auditStatement(db, {
      operator: actor.username,
      action: "access_revoked",
      target: { id: grant.user_id, username: grant.username },
      summary: `Revoked grant #${grantId} (${grant.plan})`,
      note,
      grantId,
      reference: `grant #${grantId}`,
    }),
  ]);
  if (!updated?.meta?.changes) throw new ApiError(409, "this grant is already revoked");
  return { ok: true, grant: grantView(await getGrant(db, grantId)) };
}

// ---- Payment records ----------------------------------------------------------------------

export async function adminCreatePayment(env, actor, body) {
  const userId = Number(body.user_id);
  if (!Number.isInteger(userId) || userId < 1) throw new ApiError(422, "user_id must be a positive integer");
  const note = requireNote(body.note);
  const db = requireDatabase(env);
  const user = await getUser(db, userId);
  const result = await createPayment(env, userId, body);
  await auditStatement(db, {
    operator: actor.username,
    action: "payment_created",
    target: user,
    summary: `Created ${result.payment.plan} invoice (${result.payment.method})`,
    note,
    paymentId: result.payment.id,
    reference: result.payment.invoice,
  }).run();
  return result;
}

export async function adminGetPayment(env, paymentId) {
  const db = requireDatabase(env);
  const row = await db.prepare(`
    SELECT p.*, u.username AS user_username, u.email AS user_email, u.phone AS user_phone, u.id AS user_id
      FROM payments p JOIN users u ON u.id = p.user_id WHERE p.id = ?
  `).bind(paymentId).first();
  if (!row) throw new ApiError(404, "payment not found");
  return {
    payment: {
      ...paymentView(row),
      user: { id: Number(row.user_id), username: row.user_username, email: row.user_email, phone: row.user_phone },
    },
  };
}

export async function adminCancelPayment(env, paymentId, actor, body) {
  const note = requireNote(body.note);
  const db = requireDatabase(env);
  const existing = await db.prepare(`
    SELECT p.*, u.username FROM payments p JOIN users u ON u.id = p.user_id WHERE p.id = ?
  `).bind(paymentId).first();
  if (!existing) throw new ApiError(404, "payment not found");
  // Check before writing anything: a refused cancel must not leave an audit row behind.
  if (existing.status !== "awaiting_payment" && existing.status !== "submitted") {
    throw new ApiError(409, `only open invoices can be cancelled (this one is ${existing.status.replaceAll("_", " ")})`);
  }
  const timestamp = nowSeconds();
  const [updated] = await db.batch([
    db.prepare(`
      UPDATE payments SET status = 'cancelled', decided_at = ?, decided_by = ?, decision_note = ?
       WHERE id = ? AND status IN ('awaiting_payment', 'submitted')
    `).bind(timestamp, actor.username, note.slice(0, 300), paymentId),
    auditStatement(db, {
      operator: actor.username,
      action: "payment_cancelled",
      target: { id: existing.user_id, username: existing.username },
      summary: `Cancelled ${existing.status.replaceAll("_", " ")} invoice`,
      note,
      paymentId,
      reference: existing.invoice,
    }),
  ]);
  if (!updated?.meta?.changes) {
    throw new ApiError(409, `only open invoices can be cancelled (this one is ${existing.status.replaceAll("_", " ")})`);
  }
  const row = await db.prepare("SELECT * FROM payments WHERE id = ?").bind(paymentId).first();
  return { ok: true, payment: paymentView(row) };
}
