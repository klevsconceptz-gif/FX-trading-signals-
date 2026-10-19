import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.js";

const json = async (response) => response.json();

function localEnv({ database = true, kv = true } = {}) {
  return {
    klev: database ? { prepare: (sql) => ({ first: async () => sql.includes("SELECT 1") ? { ok: 1 } : null }) } : undefined,
    voom: kv ? {
      get: async () => null,
      put: async () => {},
      delete: async () => {},
    } : undefined,
  };
}

test("health checks D1 and KV and reports the absent signal adapter as degraded", async () => {
  const response = await worker.fetch(new Request("https://api.example/api/health"), localEnv());
  const body = await json(response);
  assert.equal(response.status, 200);
  assert.equal(body.status, "degraded");
  assert.equal(body.config.persistence, "Cloudflare D1");
  assert.equal(body.config.session_cache, "Cloudflare KV");
  assert.equal(body.universe_size, 32);
});

test("health reports missing required bindings", async () => {
  const response = await worker.fetch(new Request("https://api.example/api/health"), localEnv({ kv: false }));
  assert.equal(response.status, 503);
  assert.match((await json(response)).detail, /KV namespace binding/);
});

test("password policy is public and stable", async () => {
  const response = await worker.fetch(new Request("https://api.example/api/auth/password-policy"), {});
  const body = await json(response);
  assert.equal(response.status, 200);
  assert.equal(body.min_length, 10);
  assert.deepEqual(body.rules, ["length_10", "uppercase", "lowercase", "number", "symbol"]);
});

test("admin and paid endpoints reject anonymous callers", async () => {
  for (const path of ["/api/admin/users", "/api/scan", "/api/universe"]) {
    const response = await worker.fetch(new Request(`https://api.example${path}`), {});
    assert.equal(response.status, 401, path);
    assert.equal((await json(response)).detail, "log in to continue");
  }
});

test("unknown API paths return JSON 404", async () => {
  const response = await worker.fetch(new Request("https://api.example/api/not-real"), {});
  assert.equal(response.status, 404);
  assert.deepEqual(await json(response), { detail: "not found" });
});
