import { test } from "node:test";
import assert from "node:assert/strict";
import { containerEnv } from "../src/container-env.js";

const base = { INTERNAL_TOKEN: "tok", KLEVS_PUBLIC_ORIGIN: "https://klevs.example.com" };

test("builds the replica and e-mail URLs from the public origin", () => {
  const env = containerEnv(base);
  assert.equal(env.KLEVS_REPLICA_URL, "https://klevs.example.com/__internal/snapshot");
  assert.equal(env.KLEVS_EMAIL_URL, "https://klevs.example.com/__internal/email");
  assert.equal(env.KLEVS_INTERNAL_TOKEN, "tok");
});

test("a trailing slash on the origin does not double up", () => {
  const env = containerEnv({ ...base, KLEVS_PUBLIC_ORIGIN: "https://klevs.example.com//" });
  assert.equal(env.KLEVS_REPLICA_URL, "https://klevs.example.com/__internal/snapshot");
});

test("live data defaults to on, and empty values are dropped", () => {
  const env = containerEnv({ ...base, KFX_ADMIN_PHONE: "", KFX_ADMIN_EMAIL: undefined });
  assert.equal(env.KFX_LIVE_DATA, "1");
  assert.equal("KFX_ADMIN_PHONE" in env, false);
  assert.equal("KFX_ADMIN_EMAIL" in env, false);
});

test("admin settings pass through when set", () => {
  const env = containerEnv({ ...base, KFX_ADMIN_USERNAME: "ops", KFX_ADMIN_PASSWORD: "pw", KFX_LIVE_DATA: "0" });
  assert.equal(env.KFX_ADMIN_USERNAME, "ops");
  assert.equal(env.KFX_ADMIN_PASSWORD, "pw");
  assert.equal(env.KFX_LIVE_DATA, "0");
});

test("reset codes are never enabled in the container", () => {
  const env = containerEnv({ ...base, KFX_ADMIN_USERNAME: "ops" });
  assert.equal("KLEVS_DEV_RESET_CODES" in env, false);
});

test("without an origin no internal URLs are set, so the app falls back to its defaults", () => {
  const env = containerEnv({ INTERNAL_TOKEN: "tok", KLEVS_PUBLIC_ORIGIN: "" });
  assert.equal("KLEVS_REPLICA_URL" in env, false);
  assert.equal("KLEVS_EMAIL_URL" in env, false);
});
