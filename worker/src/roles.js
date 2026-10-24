// Role rules shared by the auth and admin modules.
//
// There is one administrator: the bootstrap account named by the KFX_ADMIN_USERNAME secret.
// Any other stored row with role 'admin' is treated as a plain user, and the bootstrap sign-in
// demotes such rows (see ensureAdmin in auth.js). Nothing in the API can grant the admin role.

export const ROLE_ADMIN = "admin";
export const ROLE_USER = "user";

// The bootstrap admin's email (KFX_ADMIN_EMAIL, or the username when no email is set).
export function bootstrapEmail(env) {
  return String(env?.KFX_ADMIN_EMAIL || env?.KFX_ADMIN_USERNAME || "").trim().toLowerCase();
}

// The bootstrap username and email are reserved: an ordinary account holding them would block the
// admin's sign-in (ensureBootstrapAdmin refuses to adopt a non-admin row).
export function isReservedIdentity(env, username, email) {
  if (!bootstrapUsername(env)) return false;
  if (isBootstrapUsername(env, username)) return true;
  return Boolean(bootstrapEmail(env)) && String(email || "").trim().toLowerCase() === bootstrapEmail(env);
}

export function bootstrapUsername(env) {
  return String(env?.KFX_ADMIN_USERNAME || "").trim().toLowerCase();
}

export function isBootstrapUsername(env, username) {
  const bootstrap = bootstrapUsername(env);
  return Boolean(bootstrap) && String(username || "").toLowerCase() === bootstrap;
}

// The role a user really has: admin only for the bootstrap account, user for everyone else.
export function effectiveRole(env, user) {
  return user && user.role === ROLE_ADMIN && isBootstrapUsername(env, user.username) ? ROLE_ADMIN : ROLE_USER;
}

export function withEffectiveRole(env, user) {
  return user ? { ...user, role: effectiveRole(env, user) } : user;
}
