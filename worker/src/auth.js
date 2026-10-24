import { ApiError, getBearerToken, requireDatabase, requireKV } from "./http.js";
import { auditStatement } from "./audit.js";
import { ROLE_ADMIN, ROLE_USER, effectiveRole, isReservedIdentity, withEffectiveRole } from "./roles.js";
import {
  PASSWORD_RULES,
  adminUsernameIsValid,
  emailIsValid,
  hashPassword,
  passwordPolicyError,
  phoneIsValid,
  phoneKey,
  randomToken,
  sha256Hex,
  usernameIsValid,
  verifyPassword,
} from "./security.js";

export const SESSION_TTL_SECONDS = 14 * 24 * 60 * 60;
export const LOGIN_MAX_FAILURES = 10;
export const LOGIN_LOCK_SECONDS = 15 * 60;
export const RESET_CODE_TTL_SECONDS = 15 * 60;
export const RESET_MAX_ATTEMPTS = 5;
const SESSION_CACHE_SECONDS = 60;
const RESET_FAILURE = "reset code is incorrect or has expired — request a new code";

function nowSeconds() {
  return Date.now() / 1000;
}

// env is passed wherever a role is reported, so the role is always the effective one.
export function publicUser(user, env) {
  return {
    id: user.id,
    username: user.username,
    email: user.email,
    phone: user.phone || "",
    role: env ? effectiveRole(env, user) : user.role,
  };
}

async function getUserByIdentifier(db, identifier) {
  const ident = identifier.trim();
  const email = ident.toLowerCase();
  const phone = phoneKey(ident);
  return db.prepare(`
    SELECT id, username, username_key, email, phone, password_hash, role,
           created_at, reset_hash, reset_expires, reset_attempts,
           failed_logins, locked_until, password_version, disabled_at
      FROM users
     WHERE username_key = ? OR email = ? OR (phone_key <> '' AND phone_key = ?)
     LIMIT 1
  `).bind(ident.toLowerCase(), email, phone).first();
}

async function getUserById(db, userId) {
  return db.prepare(`
    SELECT id, username, username_key, email, phone, password_hash, role,
           created_at, reset_hash, reset_expires, reset_attempts,
           failed_logins, locked_until, password_version, disabled_at
      FROM users WHERE id = ?
  `).bind(userId).first();
}

export async function ensureAdmin(env) {
  const result = await ensureBootstrapAdmin(env);
  if (result) await demoteOtherAdmins(env, result);
  return result;
}

// Sole-admin rule: any stored admin row other than the bootstrap account becomes a user, in the
// same batch as an audit row, so the change is recorded.
async function demoteOtherAdmins(env, bootstrapUser) {
  const db = requireDatabase(env);
  const others = await db.prepare("SELECT id, username FROM users WHERE role = ? AND id <> ?")
    .bind(ROLE_ADMIN, bootstrapUser.id).all();
  for (const row of others.results || []) {
    await db.batch([
      db.prepare("UPDATE users SET role = ? WHERE id = ? AND role = ?").bind(ROLE_USER, row.id, ROLE_ADMIN),
      auditStatement(db, {
        operator: "system",
        action: "role_changed",
        target: { id: row.id, username: row.username },
        summary: "Role changed from admin to user: only the bootstrap admin may hold the admin role",
      }),
    ]);
  }
}

async function ensureBootstrapAdmin(env) {
  const username = String(env.KFX_ADMIN_USERNAME || "").trim();
  const password = String(env.KFX_ADMIN_PASSWORD || "");
  if (!username && !password) return null;
  if (!username || !password) {
    throw new ApiError(503, "both KFX_ADMIN_USERNAME and KFX_ADMIN_PASSWORD must be configured on the API Worker");
  }
  if (!adminUsernameIsValid(username)) {
    throw new ApiError(503, "KFX_ADMIN_USERNAME must be 1-64 characters with no spaces");
  }
  const email = String(env.KFX_ADMIN_EMAIL || username).trim().toLowerCase();
  if (!emailIsValid(email)) {
    throw new ApiError(503, "configure a valid KFX_ADMIN_EMAIL for the admin account");
  }
  const phone = String(env.KFX_ADMIN_PHONE || "").trim();
  if (phone && !phoneIsValid(phone)) {
    throw new ApiError(503, "KFX_ADMIN_PHONE is invalid");
  }
  const passwordError = passwordPolicyError(password);
  if (passwordError) throw new ApiError(503, `KFX_ADMIN_PASSWORD ${passwordError}`);

  const db = requireDatabase(env);
  const usernameKey = username.toLowerCase();
  const phoneNormalized = phone ? phoneKey(phone) : null;
  const byName = await db.prepare("SELECT * FROM users WHERE username_key = ? LIMIT 1").bind(usernameKey).first();
  const byEmail = await db.prepare("SELECT * FROM users WHERE email = ? LIMIT 1").bind(email).first();
  const byPhone = phoneNormalized
    ? await db.prepare("SELECT id, role FROM users WHERE phone_key = ? LIMIT 1").bind(phoneNormalized).first()
    : null;

  if (byName && byEmail && byName.id !== byEmail.id) {
    throw new ApiError(503, "admin username and email belong to different accounts");
  }
  const user = byName || byEmail;
  if (user && user.role !== "admin") {
    throw new ApiError(503, "admin bootstrap conflicts with an existing user account");
  }
  if (byPhone && (!user || byPhone.id !== user.id)) {
    throw new ApiError(503, "admin phone number belongs to another account");
  }

  const timestamp = nowSeconds();
  if (!user) {
    const passwordHash = await hashPassword(password);
    try {
      const result = await db.prepare(`
        INSERT INTO users
          (username, username_key, email, phone, phone_key, password_hash, role,
           created_at, reset_expires, reset_attempts, failed_logins, locked_until, password_version)
        VALUES (?, ?, ?, ?, ?, ?, 'admin', ?, 0, 0, 0, 0, 1)
      `).bind(username, usernameKey, email, phone || null, phoneNormalized, passwordHash, timestamp).run();
      return getUserById(db, result.meta.last_row_id);
    } catch (error) {
      if (String(error?.message || error).includes("UNIQUE constraint failed")) {
        throw new ApiError(503, "admin bootstrap conflicts with an existing account");
      }
      throw error;
    }
  }

  const passwordMatches = await verifyPassword(password, user.password_hash);
  if (!passwordMatches) {
    const newHash = await hashPassword(password);
    await db.prepare(`
      UPDATE users
         SET password_hash = ?, password_version = password_version + 1,
             email = ?, phone = ?, phone_key = ?, reset_hash = NULL,
             reset_expires = 0, reset_attempts = 0, failed_logins = 0, locked_until = 0
       WHERE id = ?
    `).bind(newHash, email, phone || user.phone || null, phoneNormalized || user.phone_key || null, user.id).run();
    await db.prepare("DELETE FROM user_sessions WHERE user_id = ?").bind(user.id).run();
  } else {
    await db.prepare(`
      UPDATE users
         SET email = ?, phone = ?, phone_key = ?, reset_hash = NULL,
             reset_expires = 0, reset_attempts = 0, failed_logins = 0, locked_until = 0
       WHERE id = ?
    `).bind(email, phone || user.phone || null, phoneNormalized || user.phone_key || null, user.id).run();
  }
  return getUserById(db, user.id);
}

export async function issueSession(env, user) {
  const db = requireDatabase(env);
  const kv = requireKV(env);
  const token = randomToken(32);
  const tokenHash = await sha256Hex(token);
  const createdAt = nowSeconds();
  const expiresAt = createdAt + SESSION_TTL_SECONDS;
  const passwordVersion = Number(user.password_version || 1);
  await db.prepare(`
    INSERT INTO user_sessions(token_hash, user_id, password_version, created_at, expires_at)
    VALUES (?, ?, ?, ?, ?)
  `).bind(tokenHash, user.id, passwordVersion, createdAt, expiresAt).run();
  const cached = { user: publicUser(user, env), password_version: passwordVersion };
  await kv.put(`session:${tokenHash}`, JSON.stringify(cached), { expirationTtl: SESSION_CACHE_SECONDS });
  return token;
}

export async function currentUser(env, request) {
  const token = getBearerToken(request);
  if (!token) return null;
  const db = requireDatabase(env);
  const kv = requireKV(env);
  const tokenHash = await sha256Hex(token);
  const key = `session:${tokenHash}`;
  const timestamp = nowSeconds();
  const session = await db.prepare(`
    SELECT s.user_id, s.expires_at, s.password_version, u.password_version AS current_password_version,
           u.disabled_at AS current_disabled_at
      FROM user_sessions AS s
      JOIN users AS u ON u.id = s.user_id
     WHERE s.token_hash = ?
  `).bind(tokenHash).first();
  if (!session || session.expires_at <= timestamp || session.password_version !== session.current_password_version
      || Number(session.current_disabled_at || 0) > 0) {
    if (session) await db.prepare("DELETE FROM user_sessions WHERE token_hash = ?").bind(tokenHash).run();
    await kv.delete(key);
    return null;
  }

  const cached = await kv.get(key, { type: "json" });
  if (cached?.user?.id === session.user_id && cached.password_version === session.current_password_version) {
    return withEffectiveRole(env, cached.user);
  }
  const user = await getUserById(db, session.user_id);
  if (!user) {
    await db.prepare("DELETE FROM user_sessions WHERE token_hash = ?").bind(tokenHash).run();
    await kv.delete(key);
    return null;
  }
  await kv.put(key, JSON.stringify({ user: publicUser(user, env), password_version: user.password_version }), {
    expirationTtl: Math.min(SESSION_CACHE_SECONDS, Math.max(1, Math.floor(session.expires_at - timestamp))),
  });
  return publicUser(user, env);
}

export async function revokeSession(env, request) {
  const token = getBearerToken(request);
  if (!token) return;
  const db = requireDatabase(env);
  const kv = requireKV(env);
  const tokenHash = await sha256Hex(token);
  await db.prepare("DELETE FROM user_sessions WHERE token_hash = ?").bind(tokenHash).run();
  await kv.delete(`session:${tokenHash}`);
}

export async function accessStatus(env, user) {
  if (user.role === "admin") {
    return { active: true, plan: "admin", plan_name: "Admin", expires_at: null, access: "unrestricted" };
  }
  const grant = await requireDatabase(env).prepare(`
    SELECT plan, expires_at FROM access_grants
     WHERE user_id = ? AND status = 'active' AND expires_at > ?
     ORDER BY expires_at DESC LIMIT 1
  `).bind(user.id, nowSeconds()).first();
  if (!grant) return { active: false, plan: null, plan_name: null, expires_at: null, access: null };
  return {
    active: true,
    plan: grant.plan,
    plan_name: PLAN_NAMES[grant.plan] || grant.plan,
    expires_at: grant.expires_at,
    access: "subscriber",
  };
}

const PLAN_NAMES = Object.freeze({ starter: "Starter", pro: "Pro", elite: "Elite" });

export async function sessionPayload(env, user) {
  return { user: publicUser(user, env), access: await accessStatus(env, user) };
}

export async function registerAccount(env, body) {
  const username = typeof body.username === "string" ? body.username.trim() : "";
  const email = typeof body.email === "string" ? body.email.trim().toLowerCase() : "";
  const phone = typeof body.phone === "string" ? body.phone.trim() : "";
  const password = typeof body.password === "string" ? body.password : "";
  if (!usernameIsValid(username)) {
    throw new ApiError(422, "username must be 3-32 characters: letters, numbers, _ . - only");
  }
  if (!emailIsValid(email)) throw new ApiError(422, "invalid email address");
  if (!phoneIsValid(phone)) throw new ApiError(422, "invalid phone number (digits, +, -, spaces, parentheses; 7-20 chars)");
  if (!password || password.length > 128) throw new ApiError(422, "password must be 1-128 characters");
  const policyError = passwordPolicyError(password);
  if (policyError) throw new ApiError(422, policyError);
  if (isReservedIdentity(env, username, email)) throw new ApiError(422, "this username or email is reserved");

  const db = requireDatabase(env);
  requireKV(env);
  const usernameKey = username.toLowerCase();
  const normalizedPhone = phoneKey(phone);
  const [sameUsername, sameEmail, samePhone] = await Promise.all([
    db.prepare("SELECT id FROM users WHERE username_key = ?").bind(usernameKey).first(),
    db.prepare("SELECT id FROM users WHERE email = ?").bind(email).first(),
    db.prepare("SELECT id FROM users WHERE phone_key = ?").bind(normalizedPhone).first(),
  ]);
  if (sameUsername) throw new ApiError(422, "username is already taken");
  if (sameEmail) throw new ApiError(422, "an account with that email already exists");
  if (samePhone) throw new ApiError(422, "an account with that phone number already exists");

  const passwordHash = await hashPassword(password);
  const timestamp = nowSeconds();
  let result;
  try {
    result = await db.prepare(`
      INSERT INTO users
        (username, username_key, email, phone, phone_key, password_hash, role,
         created_at, reset_expires, reset_attempts, failed_logins, locked_until, password_version)
      VALUES (?, ?, ?, ?, ?, ?, 'user', ?, 0, 0, 0, 0, 1)
    `).bind(username, usernameKey, email, phone, normalizedPhone, passwordHash, timestamp).run();
  } catch (error) {
    if (String(error?.message || error).includes("UNIQUE constraint failed")) {
      throw new ApiError(422, "an account with these details already exists");
    }
    throw error;
  }
  const user = await getUserById(db, result.meta.last_row_id);
  const token = await issueSession(env, user);
  return {
    ok: true,
    token,
    ...(await sessionPayload(env, user)),
    message: "Account created. Choose a plan and complete payment to unlock the bot.",
  };
}

export async function loginAccount(env, body) {
  const identifier = typeof body.identifier === "string" ? body.identifier.trim() : "";
  const password = typeof body.password === "string" ? body.password : "";
  if (identifier.length < 3 || identifier.length > 200 || !password || password.length > 128) {
    throw new ApiError(422, "identifier and password are required");
  }
  requireKV(env);
  await ensureAdmin(env);
  const db = requireDatabase(env);
  const user = await getUserByIdentifier(db, identifier);
  if (!user) {
    // Keep unknown-account and wrong-password responses indistinguishable and similar in cost.
    await hashPassword(password);
    throw new ApiError(401, "incorrect username, email, phone or password");
  }

  const timestamp = nowSeconds();
  if (Number(user.locked_until) > timestamp) {
    const minutes = Math.max(1, Math.floor((user.locked_until - timestamp) / 60) + 1);
    throw new ApiError(429, `too many failed attempts — try again in about ${minutes} minutes`);
  }
  const passwordMatches = await verifyPassword(password, user.password_hash);
  if (!passwordMatches) {
    const failures = Number(user.failed_logins || 0) + 1;
    const lockedUntil = failures >= LOGIN_MAX_FAILURES ? timestamp + LOGIN_LOCK_SECONDS : Number(user.locked_until || 0);
    await db.prepare(`
      UPDATE users SET failed_logins = ?, locked_until = ? WHERE id = ?
    `).bind(failures >= LOGIN_MAX_FAILURES ? 0 : failures, lockedUntil, user.id).run();
    throw new ApiError(401, "incorrect username, email, phone or password");
  }

  if (Number(user.disabled_at || 0) > 0) {
    throw new ApiError(403, "this account has been disabled — contact the operator");
  }
  await db.prepare("UPDATE users SET failed_logins = 0, locked_until = 0 WHERE id = ?").bind(user.id).run();
  const refreshedUser = await getUserById(db, user.id);
  const token = await issueSession(env, refreshedUser);
  return { ok: true, token, ...(await sessionPayload(env, refreshedUser)), access_url: "/app" };
}

export async function forgotPassword(env, body) {
  const email = typeof body.email === "string" ? body.email.trim().toLowerCase() : "";
  if (!emailIsValid(email)) throw new ApiError(422, "invalid email address");
  const message = "If an account exists for that email, a reset code has been sent. It expires in 15 minutes.";
  const db = requireDatabase(env);
  const user = await db.prepare("SELECT id, email FROM users WHERE email = ? LIMIT 1").bind(email).first();
  if (!user) return { ok: true, message };

  const code = String(crypto.getRandomValues(new Uint32Array(1))[0] % 10_000_000).padStart(7, "0");
  const hash = await hashPassword(code);
  const timestamp = nowSeconds();
  await db.prepare(`
    UPDATE users SET reset_hash = ?, reset_expires = ?, reset_attempts = 0 WHERE id = ?
  `).bind(hash, timestamp + RESET_CODE_TTL_SECONDS, user.id).run();

  if (!await sendResetEmail(env, user.email, code)) {
    console.warn("Password reset email could not be delivered; mail service is not configured or returned an error.");
  }
  return { ok: true, message };
}

async function sendResetEmail(env, email, code) {
  const endpoint = String(env.KLEVS_RESET_EMAIL_URL || "").trim();
  const token = String(env.KLEVS_RESET_EMAIL_TOKEN || "");
  if (!endpoint || !token) return false;
  let url;
  try {
    url = new URL(endpoint);
  } catch {
    return false;
  }
  if (url.protocol !== "https:" || url.username || url.password) return false;
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${token}`,
        "Content-Type": "application/json",
        "Accept": "application/json",
      },
      body: JSON.stringify({
        to: email,
        subject: "klevs FX trading App — password reset code",
        text: `Your password reset code is ${code}. It expires in 15 minutes. If you did not request this, ignore this email.`,
      }),
    });
    return response.ok;
  } catch {
    return false;
  }
}

export async function resetPassword(env, body) {
  const email = typeof body.email === "string" ? body.email.trim().toLowerCase() : "";
  const code = typeof body.code === "string" ? body.code.trim() : "";
  const newPassword = typeof body.new_password === "string" ? body.new_password : "";
  if (!emailIsValid(email)) throw new ApiError(422, "invalid email address");
  if (code.length < 6 || code.length > 10) throw new ApiError(422, "reset code must be 6-10 characters");
  if (!newPassword || newPassword.length > 128) throw new ApiError(422, "password must be 1-128 characters");
  const policyError = passwordPolicyError(newPassword);
  if (policyError) throw new ApiError(422, policyError);

  const db = requireDatabase(env);
  requireKV(env);
  const user = await db.prepare("SELECT * FROM users WHERE email = ? LIMIT 1").bind(email).first();
  const timestamp = nowSeconds();
  if (!user || !user.reset_hash) throw new ApiError(422, RESET_FAILURE);
  if (timestamp > Number(user.reset_expires) || Number(user.reset_attempts) >= RESET_MAX_ATTEMPTS) {
    await db.prepare(`UPDATE users SET reset_hash = NULL, reset_attempts = 0 WHERE id = ?`).bind(user.id).run();
    throw new ApiError(422, RESET_FAILURE);
  }
  if (!await verifyPassword(code, user.reset_hash)) {
    const attempts = Number(user.reset_attempts) + 1;
    await db.prepare(`
      UPDATE users SET reset_attempts = ?, reset_hash = ? WHERE id = ?
    `).bind(attempts, attempts >= RESET_MAX_ATTEMPTS ? null : user.reset_hash, user.id).run();
    throw new ApiError(422, RESET_FAILURE);
  }

  const passwordHash = await hashPassword(newPassword);
  await db.prepare(`
    UPDATE users
       SET password_hash = ?, reset_hash = NULL, reset_expires = 0, reset_attempts = 0,
           failed_logins = 0, locked_until = 0, password_version = password_version + 1
     WHERE id = ?
  `).bind(passwordHash, user.id).run();
  await db.prepare("DELETE FROM user_sessions WHERE user_id = ?").bind(user.id).run();
  const updated = await getUserById(db, user.id);
  const token = await issueSession(env, updated);
  return {
    ok: true,
    message: "Password updated. You are logged in with the new password.",
    token,
    ...(await sessionPayload(env, updated)),
    access_url: "/app",
  };
}

export function passwordPolicyResponse() {
  return {
    min_length: 10,
    rules: PASSWORD_RULES.map((rule) => rule.key),
    labels: Object.fromEntries(PASSWORD_RULES.map((rule) => [rule.key, rule.label.replace(/^An /, "An ")])),
  };
}
