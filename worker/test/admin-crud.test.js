import test from "node:test";
import assert from "node:assert/strict";
import {
  adminCancelPayment,
  adminCreateUser,
  adminDeleteUser,
  adminRevokeGrant,
  adminUpdateUser,
} from "../src/admin.js";

// Fake D1: `answer(sql, args)` returns the row for a first() lookup, or a list for all().
// Every batch and run is recorded so each test can assert that a refused call wrote nothing.
function fakeDb(answer = () => null) {
  const writes = [];
  return {
    writes,
    prepare(sql) {
      return {
        bind(...args) {
          return {
            sql,
            args,
            first: async () => answer(sql, args),
            all: async () => ({ results: [] }),
            run: async () => { writes.push({ sql, args }); return { meta: { changes: 1, last_row_id: 1 } }; },
          };
        },
      };
    },
    batch: async (statements) => {
      writes.push(...statements.map((s) => ({ sql: s.sql, args: s.args })));
      return statements.map(() => ({ meta: { changes: 1 } }));
    },
  };
}

const BOOTSTRAP = "opsadmin";
const env = { KFX_ADMIN_USERNAME: BOOTSTRAP };
const operator = { id: 1, username: "ops_two" };

function usersAnswer(users) {
  return (sql, args) => {
    if (/^SELECT \* FROM users WHERE id = \?/.test(sql)) return users[args[0]] || null;
    if (/FROM payments p JOIN users/.test(sql)) return users.payments?.[args[0]] || null;
    if (/SELECT \(SELECT COUNT\(\*\) FROM payments/.test(sql)) return users.counts || { payments: 0, grants: 0 };
    if (/^SELECT g\.\*/.test(sql) || /FROM access_grants WHERE id/.test(sql)) return users.grants?.[args[0]] || null;
    return null;
  };
}

const USERS = {
  1: { id: 1, username: BOOTSTRAP, email: "ops@example.test", phone: null, role: "admin", disabled_at: 0, created_at: 1 },
  2: { id: 2, username: "buyer_two", email: "buyer@example.test", phone: null, role: "user", disabled_at: 0, created_at: 2 },
};

test("creating an account without a reason writes nothing", async () => {
  const db = fakeDb(usersAnswer(USERS));
  await assert.rejects(
    adminCreateUser({ ...env, klev: db }, operator.username, { username: "new_user", email: "n@example.test", password: "Fresh!Secret77", note: "x" }),
    (error) => error.status === 422,
  );
  assert.equal(db.writes.length, 0);
});

test("the bootstrap admin cannot be edited, disabled, demoted or deleted", async () => {
  const db = fakeDb(usersAnswer(USERS));
  const context = { ...env, klev: db };
  await assert.rejects(adminUpdateUser(context, 1, operator, { disabled: true, note: "try it" }), (e) => e.status === 409);
  await assert.rejects(adminUpdateUser(context, 1, operator, { role: "user", note: "try it" }), (e) => e.status === 409);
  await assert.rejects(adminDeleteUser(context, 1, operator, { note: "try it" }), (e) => e.status === 409);
  assert.equal(db.writes.length, 0);
});

test("an operator cannot disable or delete their own account", async () => {
  const db = fakeDb(usersAnswer({ ...USERS, 1: { ...USERS[1], username: "ops_two" } }));
  const context = { ...env, klev: db };
  await assert.rejects(adminUpdateUser(context, 1, operator, { disabled: true, note: "self" }), (e) => e.status === 409);
  await assert.rejects(adminDeleteUser(context, 1, operator, { note: "self" }), (e) => e.status === 409);
  assert.equal(db.writes.length, 0);
});

test("an account with payment or access records cannot be hard-deleted", async () => {
  const db = fakeDb(usersAnswer({ ...USERS, counts: { payments: 1, grants: 0 } }));
  await assert.rejects(adminDeleteUser({ ...env, klev: db }, 2, operator, { note: "clean up" }),
    (e) => e.status === 409 && /disable it instead/.test(e.message));
  assert.equal(db.writes.length, 0);
});

test("an account with no records is deleted with its sessions and an audit row in one batch", async () => {
  const db = fakeDb(usersAnswer({ ...USERS, counts: { payments: 0, grants: 0 } }));
  const result = await adminDeleteUser({ ...env, klev: db }, 2, operator, { note: "test account" });
  assert.deepEqual(result, { ok: true, deleted_user_id: 2 });
  const sql = db.writes.map((w) => w.sql).join("\n");
  assert.match(sql, /DELETE FROM user_sessions/);
  assert.match(sql, /DELETE FROM users/);
  assert.match(sql, /INSERT INTO admin_audit_events/);
});

test("a confirmed payment cannot be cancelled, and the refusal writes no audit row", async () => {
  const payment = { id: 9, user_id: 2, username: "buyer_two", status: "confirmed", invoice: "INV-9" };
  const db = fakeDb((sql, args) => (/FROM payments p JOIN users/.test(sql) && args[0] === 9 ? payment : null));
  await assert.rejects(adminCancelPayment({ ...env, klev: db }, 9, operator, { note: "too late" }),
    (e) => e.status === 409 && /this one is confirmed/.test(e.message));
  assert.equal(db.writes.length, 0);
});

test("a revoked grant cannot be revoked twice", async () => {
  const grant = { id: 5, user_id: 2, username: "buyer_two", plan: "pro", status: "revoked", expires_at: 9e9, starts_at: 1, granted_by: "ops_two" };
  const db = fakeDb((sql, args) => (/FROM access_grants g JOIN users/.test(sql) && args[0] === 5 ? grant : null));
  await assert.rejects(adminRevokeGrant({ ...env, klev: db }, 5, operator, { note: "again" }), (e) => e.status === 409);
  assert.equal(db.writes.length, 0);
});
