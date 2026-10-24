import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.js";
import { effectiveRole, isBootstrapUsername } from "../src/roles.js";
import { publicUser } from "../src/auth.js";
import { adminCreateUser, adminUpdateUser } from "../src/admin.js";
import { ensureAdmin, registerAccount } from "../src/auth.js";
import { hashPassword } from "../src/security.js";

const env = { KFX_ADMIN_USERNAME: "Opsadmin" };
const operator = { id: 1, username: "ops_two" };

test("only the bootstrap account is an admin, whatever its stored role says", () => {
  assert.equal(isBootstrapUsername(env, "opsadmin"), true);
  assert.equal(isBootstrapUsername(env, "OPSADMIN"), true);
  assert.equal(effectiveRole(env, { username: "opsadmin", role: "admin" }), "admin");
  assert.equal(effectiveRole(env, { username: "buyer", role: "admin" }), "user");
  assert.equal(effectiveRole(env, { username: "opsadmin", role: "user" }), "user");
  assert.equal(effectiveRole({}, { username: "opsadmin", role: "admin" }), "user", "no bootstrap configured means no admin");
});

test("reported roles are the effective roles", () => {
  assert.equal(publicUser({ id: 2, username: "buyer", email: "b@x.test", role: "admin" }, env).role, "user");
  assert.equal(publicUser({ id: 1, username: "opsadmin", email: "o@x.test", role: "admin" }, env).role, "admin");
});

test("the console cannot create or promote an account to admin", async () => {
  const noDb = { KFX_ADMIN_USERNAME: "opsadmin", klev: { prepare: () => { throw new Error("must not touch the database"); } } };
  await assert.rejects(adminCreateUser(noDb, "opsadmin", { username: "new_op", email: "n@x.test", password: "Fresh!Secret77", role: "admin", note: "make op" }),
    (e) => e.status === 422 && /bootstrap admin/.test(e.message));
  const buyerDb = { prepare: () => ({ bind: () => ({ first: async () => ({ id: 2, username: "buyer", email: "b@x.test", phone: null, role: "user", disabled_at: 0, created_at: 1 }), run: async () => { throw new Error("must not write"); } }) }) };
  await assert.rejects(adminUpdateUser({ KFX_ADMIN_USERNAME: "opsadmin", klev: buyerDb }, 2, { id: 1, username: "opsadmin" }, { role: "admin", note: "promote" }),
    (e) => e.status === 422 && /bootstrap admin/.test(e.message));
});

test("CORS: the native app origins get a preflight answer and headers, other origins get none", async () => {
  const url = "https://api.test/api/health";
  const preflight = await worker.fetch(new Request(url, {
    method: "OPTIONS",
    headers: { Origin: "capacitor://localhost", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization" },
  }), {});
  assert.equal(preflight.status, 204);
  assert.equal(preflight.headers.get("Access-Control-Allow-Origin"), "capacitor://localhost");
  assert.match(preflight.headers.get("Access-Control-Allow-Headers"), /Authorization/);

  const android = await worker.fetch(new Request("https://api.test/api/nope", { headers: { Origin: "https://localhost" } }), {});
  assert.equal(android.headers.get("Access-Control-Allow-Origin"), "https://localhost");

  const other = await worker.fetch(new Request(url, { method: "OPTIONS", headers: { Origin: "https://evil.example" } }), {});
  assert.notEqual(other.status, 204, "unknown origins get no preflight answer");
  assert.equal(other.headers.get("Access-Control-Allow-Origin"), null);
});

test("sign-in demotes every other admin row, in one batch with its audit row", async () => {
  const password = "Bootstrap!Secret77";
  const bootstrapRow = {
    id: 1, username: "opsadmin", username_key: "opsadmin", email: "ops@example.test", phone: null, phone_key: "",
    password_hash: await hashPassword(password), role: "admin", password_version: 1, created_at: 1, disabled_at: 0,
  };
  const batches = [];
  const db = {
    prepare(sql) {
      return {
        bind(...args) {
          return {
            sql,
            args,
            first: async () => {
              if (/username_key = \?/.test(sql)) return args[0] === "opsadmin" ? bootstrapRow : null;
              if (/FROM users WHERE id = \?/.test(sql)) return bootstrapRow;
              return null;
            },
            all: async () => ({ results: /role = \? AND id <> \?/.test(sql) ? [{ id: 2, username: "buyer_two" }] : [] }),
            run: async () => ({ meta: { changes: 1, last_row_id: 1 } }),
          };
        },
      };
    },
    batch: async (statements) => {
      batches.push(statements.map((s) => ({ sql: s.sql, args: s.args })));
      return statements.map(() => ({ meta: { changes: 1 } }));
    },
  };
  const user = await ensureAdmin({
    KFX_ADMIN_USERNAME: "opsadmin", KFX_ADMIN_EMAIL: "ops@example.test", KFX_ADMIN_PASSWORD: password, klev: db,
  });
  assert.equal(user.id, 1);
  assert.equal(batches.length, 1, "one demotion batch for the one other admin row");
  const [demote, audit] = batches[0];
  assert.match(demote.sql, /UPDATE users SET role = \? WHERE id = \? AND role = \?/);
  assert.deepEqual(demote.args, ["user", 2, "admin"]);
  assert.match(audit.sql, /INSERT INTO admin_audit_events/);
  assert.equal(audit.args[3], "role_changed");
  assert.equal(audit.args[4], 2);
});

test("the bootstrap username and email are reserved from public sign-up and the console", async () => {
  const untouched = { prepare: () => { throw new Error("must not touch the database"); } };
  const env = { KFX_ADMIN_USERNAME: "opsadmin", KFX_ADMIN_EMAIL: "ops@example.test", klev: untouched };
  await assert.rejects(registerAccount(env, { username: "OpsAdmin", email: "x@example.test", phone: "+1 201 555 0100", password: "Fresh!Secret77" }),
    (e) => e.status === 422 && /reserved/.test(e.message));
  await assert.rejects(registerAccount(env, { username: "someone", email: "OPS@example.test", phone: "+1 201 555 0100", password: "Fresh!Secret77" }),
    (e) => e.status === 422 && /reserved/.test(e.message));
  await assert.rejects(adminCreateUser(env, operator, { username: "opsadmin", email: "x@example.test", password: "Fresh!Secret77", note: "reserved name check" }),
    (e) => e.status === 422 && /reserved/.test(e.message));
});

test("the console refuses an admin role even when no bootstrap is configured", async () => {
  const untouched = { prepare: () => { throw new Error("must not touch the database"); } };
  await assert.rejects(adminCreateUser({ klev: untouched }, operator, { username: "new_op", email: "n@x.test", password: "Fresh!Secret77", role: "admin", note: "make op" }),
    (e) => e.status === 422);
});
