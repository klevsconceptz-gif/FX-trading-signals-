-- Cloudflare D1 schema for durable accounts, payments, access, settings, and audit records.
-- Session rows are authoritative in D1; the Worker also caches short-lived session metadata in KV.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    username_key TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    phone TEXT UNIQUE,
    phone_key TEXT UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
    created_at REAL NOT NULL,
    reset_hash TEXT,
    reset_expires REAL NOT NULL DEFAULT 0,
    reset_attempts INTEGER NOT NULL DEFAULT 0,
    failed_logins INTEGER NOT NULL DEFAULT 0,
    locked_until REAL NOT NULL DEFAULT 0,
    password_version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_users_created_at ON users(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);

CREATE TABLE IF NOT EXISTS user_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    password_version INTEGER NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_sessions_user_id ON user_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_user_sessions_expires_at ON user_sessions(expires_at);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    invoice TEXT NOT NULL UNIQUE,
    plan TEXT NOT NULL CHECK (plan IN ('starter', 'pro', 'elite')),
    amount_usd REAL NOT NULL,
    method TEXT NOT NULL CHECK (method IN ('crypto', 'wire')),
    network TEXT,
    pay_to TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('awaiting_payment', 'submitted', 'confirmed', 'rejected', 'cancelled')),
    reference TEXT,
    sender_note TEXT,
    created_at REAL NOT NULL,
    submitted_at REAL,
    decided_at REAL,
    decided_by TEXT,
    decision_note TEXT
);

CREATE INDEX IF NOT EXISTS idx_payments_user_created ON payments(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_payments_status_created ON payments(status, created_at DESC);
-- A user may have only one open invoice at a time. An awaiting invoice is replaced;
-- a submitted invoice must be decided by an operator before another can be created.
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_open_payment_per_user
    ON payments(user_id) WHERE status IN ('awaiting_payment', 'submitted');

CREATE TABLE IF NOT EXISTS access_grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grant_key TEXT UNIQUE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan TEXT NOT NULL CHECK (plan IN ('starter', 'pro', 'elite')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
    starts_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    granted_by TEXT NOT NULL,
    payment_id INTEGER UNIQUE REFERENCES payments(id),
    note TEXT,
    revoked_at REAL,
    revoked_by TEXT,
    revoke_note TEXT
);

CREATE INDEX IF NOT EXISTS idx_access_grants_user_expiry
    ON access_grants(user_id, status, expires_at DESC);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT UNIQUE,
    occurred_at REAL,
    actor_username TEXT NOT NULL,
    action TEXT NOT NULL,
    target_user_id INTEGER,
    target_username TEXT,
    payment_id INTEGER,
    grant_id INTEGER,
    reference TEXT,
    summary TEXT NOT NULL,
    note TEXT
);

CREATE INDEX IF NOT EXISTS idx_admin_audit_occurred
    ON admin_audit_events(occurred_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_admin_audit_payment ON admin_audit_events(payment_id);
CREATE INDEX IF NOT EXISTS idx_admin_audit_grant ON admin_audit_events(grant_id);
