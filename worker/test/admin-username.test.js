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
