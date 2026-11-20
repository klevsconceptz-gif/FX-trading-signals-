import test from "node:test";
import assert from "node:assert/strict";
import { adminUsernameIsValid, usernameIsValid } from "../src/security.js";

test("bootstrap admin username follows the same rule as user usernames", () => {
  for (const name of ["Abcdefg", "opsadmin", "ops_admin.1", "abc", "a".repeat(32)]) {
    assert.equal(adminUsernameIsValid(name), true, name);
    assert.equal(usernameIsValid(name), true, name);
  }
  for (const name of ["", "ab", "a".repeat(33), "admin@klev.fx", "has space", "bad$name"]) {
    assert.equal(adminUsernameIsValid(name), false, name);
  }
});

test("renaming KFX_ADMIN_USERNAME moves the lookup key, so the new name can sign in", async () => {
  const { ensureAdmin } = await import("../src/auth.js");
  const { hashPassword } = await import("../src/security.js");
  const password = "Bootstrap!Secret77";
  const row = {
    id: 1, username: "oldadmin", username_key: "oldadmin", email: "ops@example.test", phone: null, phone_key: null,
    password_hash: await hashPassword(password), role: "admin", password_version: 1, created_at: 1,
    disabled_at: 0, failed_logins: 0, locked_until: 0, reset_attempts: 0, reset_expires: 0,
  };
  const writes = [];
  const db = {
    prepare(sql) {
      return {
        bind(...args) {
          return {
            sql, args,
            first: async () => {
              if (/username_key = \?/.test(sql)) return args[0] === "newadmin" ? null : row;
              if (/email = \?/.test(sql)) return args[0] === "ops@example.test" ? row : null;
              if (/FROM users WHERE id = \?/.test(sql)) return row;
              return null;
            },
            all: async () => ({ results: [] }),
            run: async () => {
              writes.push({ sql, args });
              return { meta: { changes: 1, last_row_id: 1 } };
            },
          };
        },
      };
    },
    batch: async (statements) => statements.map(() => ({ meta: { changes: 1 } })),
  };
  await ensureAdmin({
    KFX_ADMIN_USERNAME: "newadmin", KFX_ADMIN_EMAIL: "ops@example.test", KFX_ADMIN_PASSWORD: password, DB: db,
  });
  const rename = writes.find((w) => /UPDATE users SET username = \?, username_key = \? WHERE id = \?/.test(w.sql));
  assert.ok(rename, "the rename must write the lookup key as well as the display name");
  assert.deepEqual(rename.args, ["newadmin", "newadmin", 1]);
});
