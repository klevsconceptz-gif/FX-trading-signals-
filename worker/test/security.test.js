import test from "node:test";
import assert from "node:assert/strict";
import {
  hashPassword,
  passwordPolicy,
  passwordPolicyError,
  phoneKey,
  randomToken,
  verifyPassword,
} from "../src/security.js";

test("password policy matches the app requirements", () => {
  assert.equal(passwordPolicy("Short!1").valid, false);
  assert.equal(passwordPolicy("onlylowercase1!").valid, false);
  assert.equal(passwordPolicy("Strong-Pass9!").valid, true);
  assert.equal(passwordPolicyError("Strong-Pass9!"), null);
  assert.match(passwordPolicyError("weak"), /at least 10 characters/);
});

test("password hashes are salted and verify without storing plaintext", async () => {
  const first = await hashPassword("Strong-Pass9!");
  const second = await hashPassword("Strong-Pass9!");
  assert.notEqual(first, second);
  assert.equal(first.includes("Strong-Pass9!"), false);
  assert.equal(await verifyPassword("Strong-Pass9!", first), true);
  assert.equal(await verifyPassword("wrong-password", first), false);
  assert.equal(await verifyPassword("Strong-Pass9!", "invalid"), false);
});

test("bearer tokens use URL-safe random text and phone identifiers normalize", () => {
  const token = randomToken();
  assert.match(token, /^[A-Za-z0-9_-]{43}$/);
  assert.equal(phoneKey("+1 (555) 010-2345"), "15550102345");
});
