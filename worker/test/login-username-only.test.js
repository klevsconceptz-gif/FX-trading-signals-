import test from "node:test";
import assert from "node:assert/strict";
import { loginAccount } from "../src/auth.js";

// Fake D1 that records every query and finds no user, so the test only checks what login searches on.
function recordingDb() {
  const queries = [];
  return {
    queries,
    prepare(sql) {
      return {
        bind(...args) {
          queries.push({ sql, args });
          return { first: async () => null, run: async () => ({}), all: async () => ({ results: [] }) };
        },
      };
    },
  };
}
const kv = { get: async () => null, put: async () => {}, delete: async () => {} };

test("login searches by username only, never by email or phone", async () => {
  const db = recordingDb();
  await assert.rejects(
    loginAccount({ DB: db, KV: kv }, { identifier: "Trader@Example.com", password: "Wrong!Pass99" }),
    (error) => error.status === 401 && /username or password/.test(error.message),
  );
  const lookup = db.queries.find((q) => /FROM users/.test(q.sql));
  assert.ok(lookup, "a user lookup ran");
  const where = lookup.sql.slice(lookup.sql.indexOf("WHERE"));
  assert.match(where, /WHERE username_key = \?/);
  assert.doesNotMatch(where, /email|phone/);
  assert.deepEqual(lookup.args, ["trader@example.com"]);
});

test("login with a phone number is refused as an unknown account", async () => {
  const db = recordingDb();
  await assert.rejects(
    loginAccount({ DB: db, KV: kv }, { identifier: "+15551234567", password: "Wrong!Pass99" }),
    (error) => error.status === 401,
  );
  const lookup = db.queries.find((q) => /FROM users/.test(q.sql));
  assert.deepEqual(lookup.args, ["+15551234567"]);
  assert.doesNotMatch(lookup.sql.slice(lookup.sql.indexOf("WHERE")), /phone/);
});
