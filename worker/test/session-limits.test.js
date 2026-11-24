import test from "node:test";
import assert from "node:assert/strict";
import { currentUser, issueSession, SESSION_IDLE_SECONDS, SESSION_TTL_SECONDS } from "../src/auth.js";

// Fake D1 that answers the session lookup from `session`, records every write, and serves the user row.
function sessionDb(session, user = { id: 2, username: "buyer_two", role: "user", password_version: 1, disabled_at: 0 }) {
  const writes = [];
  return {
    writes,
    prepare(sql) {
      return {
        bind(...args) {
          return {
            sql,
            args,
            first: async () => {
              if (sql.includes("FROM user_sessions AS s")) return session;
              if (sql.includes("FROM users WHERE id")) return user;
              return null;
            },
            run: async () => { writes.push({ sql, args }); return { meta: { changes: 1 } }; },
          };
        },
      };
    },
  };
}

function fakeKV() {
  const store = new Map();
  return {
    get: async (key) => (store.has(key) ? JSON.parse(store.get(key)) : null),
    put: async (key, value) => { store.set(key, value); },
    delete: async (key) => { store.delete(key); },
  };
}

const request = () => new Request("https://example.test/api/me", { headers: { Authorization: "Bearer test-token" } });
const now = () => Math.floor(Date.now() / 1000);

function liveSession(overrides = {}) {
  const t = now();
  return {
    user_id: 2,
    created_at: t - 60,
    last_seen_at: t - 60,
    expires_at: t - 60 + SESSION_TTL_SECONDS,
    password_version: 1,
    current_password_version: 1,
    current_disabled_at: 0,
    ...overrides,
  };
}

test("a session used within both limits returns the user", async () => {
  const t = now();
  const db = sessionDb(liveSession({ created_at: t - 600, last_seen_at: t - 10, expires_at: t + 3600 }));
  const user = await currentUser({ DB: db, KV: fakeKV() }, request());
  assert.equal(user?.username, "buyer_two");
  assert.equal(db.writes.length, 0, "a session used 10 seconds ago is not written again");
});

test("a session idle longer than the idle limit ends and is deleted", async () => {
  const t = now();
  const db = sessionDb(liveSession({ created_at: t - 3600, last_seen_at: t - SESSION_IDLE_SECONDS - 1, expires_at: t + 3600 }));
  const user = await currentUser({ DB: db, KV: fakeKV() }, request());
  assert.equal(user, null);
  assert.ok(db.writes.some((w) => /DELETE FROM user_sessions WHERE token_hash/.test(w.sql)), "the expired session is deleted");
});

test("a session older than the maximum length ends even when it was just used", async () => {
  const t = now();
  const db = sessionDb(liveSession({ created_at: t - SESSION_TTL_SECONDS - 1, last_seen_at: t - 5, expires_at: t + 3600 }));
  const user = await currentUser({ DB: db, KV: fakeKV() }, request());
  assert.equal(user, null, "the absolute limit applies regardless of activity");
});

test("a session inside both limits stays alive and its last-seen time is refreshed", async () => {
  const t = now();
  const db = sessionDb(liveSession({ created_at: t - 23 * 3600, last_seen_at: t - 14 * 60, expires_at: t + 3600 }));
  const user = await currentUser({ DB: db, KV: fakeKV() }, request());
  assert.equal(user?.username, "buyer_two");
  const touch = db.writes.find((w) => /UPDATE user_sessions SET last_seen_at/.test(w.sql));
  assert.ok(touch, "last_seen_at is written once it is more than a minute old");
  assert.ok(touch.args[0] >= t, "it is written with the current time");
});

test("a session issued before last_seen_at existed counts its idle time from created_at", async () => {
  const t = now();
  const db = sessionDb(liveSession({ created_at: t - SESSION_IDLE_SECONDS - 1, last_seen_at: null, expires_at: t + 3600 }));
  const user = await currentUser({ DB: db, KV: fakeKV() }, request());
  assert.equal(user, null);
});

test("a disabled or password-changed session is still rejected", async () => {
  const db = sessionDb(liveSession({ current_disabled_at: now() }));
  assert.equal(await currentUser({ DB: db, KV: fakeKV() }, request()), null);
  const db2 = sessionDb(liveSession({ current_password_version: 2 }));
  assert.equal(await currentUser({ DB: db2, KV: fakeKV() }, request()), null);
});

test("sign-in stores a one-day expiry and sets last_seen_at to the sign-in time", async () => {
  const db = sessionDb(null);
  await issueSession({ DB: db, KV: fakeKV() }, { id: 2, username: "buyer_two", password_version: 1 });
  const insert = db.writes.find((w) => /INSERT INTO user_sessions/.test(w.sql));
  assert.ok(insert, "the session row is inserted");
  const [, , , createdAt, expiresAt, lastSeenAt] = insert.args;
  assert.equal(expiresAt - createdAt, SESSION_TTL_SECONDS);
  assert.equal(SESSION_TTL_SECONDS, 24 * 60 * 60);
  assert.equal(lastSeenAt, createdAt);
  assert.equal(SESSION_IDLE_SECONDS, 15 * 60);
});
