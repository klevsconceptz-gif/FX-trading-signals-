import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.js";
import { adminResetPassword } from "../src/billing.js";

const NEW_PW = "Fresh!Secret77";

// Minimal D1 stand-in: answers the user lookup and records every batched statement.
function fakeDb(users) {
  const batches = [];
  return {
    batches,
    prepare(sql) {
      return {
        bind(...args) {
          return {
            sql,
            args,
            first: async () => (sql.startsWith("SELECT id, username, role FROM users") ? users[args[0]] || null : null),
          };
        },
      };
    },
    batch: async (statements) => {
      batches.push(statements);
      return statements.map(() => ({ meta: { changes: 1 } }));
    },
  };
}

const USERS = {
  7: { id: 7, username: "buyer_one", role: "user" },
  1: { id: 1, username: "Admin@test.fx", role: "admin" },
};

test("reset rejects a weak password, a short note and an unknown account without writing", async () => {
  const db = fakeDb(USERS);
  const env = { klev: db };
  await assert.rejects(adminResetPassword(env, 7, "op", { new_password: "short", note: "customer asked" }),
    (error) => error.status === 422);
  await assert.rejects(adminResetPassword(env, 7, "op", { new_password: NEW_PW, note: "x" }),
    (error) => error.status === 422);
  await assert.rejects(adminResetPassword(env, 99, "op", { new_password: NEW_PW, note: "no such user" }),
    (error) => error.status === 404);
  assert.equal(db.batches.length, 0);
});

test("reset refuses the bootstrap admin account and points at the Worker secret", async () => {
  const db = fakeDb(USERS);
  const env = { klev: db, KFX_ADMIN_USERNAME: "admin@TEST.fx" };
  await assert.rejects(adminResetPassword(env, 1, "op", { new_password: NEW_PW, note: "try bootstrap" }),
    (error) => error.status === 409 && /KFX_ADMIN_PASSWORD/.test(error.message));
  assert.equal(db.batches.length, 0);
});

test("reset stores a PBKDF2 hash, ends sessions and writes an audit row without the password", async () => {
  const db = fakeDb(USERS);
  const result = await adminResetPassword({ klev: db }, 7, "Admin@test.fx", {
    new_password: NEW_PW,
    note: "customer lost device",
  });
  assert.deepEqual(result, { ok: true, user_id: 7 });
  assert.equal(db.batches.length, 1);
  const [update, deleteSessions, audit] = db.batches[0];

  assert.match(update.sql, /password_version = password_version \+ 1/);
  assert.match(update.args[0], /^pbkdf2-sha256\$100000\$/);
  assert.notEqual(update.args[0], NEW_PW);
  assert.deepEqual(update.args.slice(1), [7]);

  assert.match(deleteSessions.sql, /DELETE FROM user_sessions WHERE user_id = \?/);
  assert.deepEqual(deleteSessions.args, [7]);

  assert.match(audit.sql, /'password_reset'/);
  assert.deepEqual(audit.args.slice(2, 5), ["Admin@test.fx", 7, "buyer_one"]);
  assert.equal(audit.args[audit.args.length - 1], "customer lost device");
  assert.ok(!JSON.stringify(db.batches[0]).includes(NEW_PW));
});

test("the reset route rejects anonymous callers", async () => {
  const response = await worker.fetch(
    new Request("https://api.example/api/admin/users/7/reset-password", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ new_password: NEW_PW, note: "anonymous attempt" }),
    }),
    {},
  );
  assert.equal(response.status, 401);
});
