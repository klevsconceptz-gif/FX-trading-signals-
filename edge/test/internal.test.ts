import { test } from "node:test";
import assert from "node:assert/strict";
import {
  SNAPSHOT_KEY,
  MAX_SNAPSHOT_BYTES,
  constantTimeEqual,
  handleInternal,
  parseEmailRequest,
  type InternalEnv,
} from "../src/internal.js";

const TOKEN = "test-internal-token-0123456789";

class FakeR2 {
  stored = new Map<string, Uint8Array>();
  puts: string[] = [];
  async get(key: string) {
    const bytes = this.stored.get(key);
    if (!bytes) return null;
    return { size: bytes.byteLength, body: new Response(bytes).body };
  }
  async put(key: string, value: ArrayBuffer) {
    this.puts.push(key);
    this.stored.set(key, new Uint8Array(value));
  }
}

class FakeEmail {
  sent: Array<{ from: string; to: string; subject: string; text: string }> = [];
  fail = false;
  async send(message: { from: string; to: string; subject: string; text: string }) {
    if (this.fail) throw new Error("relay refused: recipient trader@example.com, body secret 987654");
    this.sent.push(message);
    return { messageId: "m1" };
  }
}

function makeEnv(overrides: Partial<InternalEnv> = {}) {
  const env = {
    BACKUPS: new FakeR2(),
    EMAIL: new FakeEmail(),
    INTERNAL_TOKEN: TOKEN,
    KLEVS_MAIL_FROM: "noreply@klevs.example.com",
    ...overrides,
  };
  return env as InternalEnv & { BACKUPS: FakeR2; EMAIL: FakeEmail };
}

function req(method: string, path: string, opts: { token?: string | null; body?: string | Uint8Array; length?: number } = {}) {
  const headers = new Headers();
  if (opts.token !== null) headers.set("x-internal-token", opts.token ?? TOKEN);
  if (opts.length !== undefined) headers.set("content-length", String(opts.length));
  if (typeof opts.body === "string") headers.set("content-type", "application/json");
  return new Request(`https://klevs.example.com${path}`, {
    method,
    headers,
    body: method === "GET" || method === "HEAD" ? undefined : opts.body,
  });
}

test("constant-time comparison matches equal strings and rejects everything else", () => {
  assert.equal(constantTimeEqual("abc", "abc"), true);
  assert.equal(constantTimeEqual("abc", "abd"), false);
  assert.equal(constantTimeEqual("abc", "abcd"), false);
  assert.equal(constantTimeEqual("", "x"), false);
});

test("every internal route refuses a missing or wrong token", async () => {
  const env = makeEnv();
  for (const token of [null, "wrong"]) {
    const res = await handleInternal(req("GET", "/__internal/snapshot", { token }), env);
    assert.equal(res.status, 401);
    const mail = await handleInternal(req("POST", "/__internal/email", { token, body: "{}" }), env);
    assert.equal(mail.status, 401);
  }
  assert.equal(env.EMAIL.sent.length, 0);
});

test("an unset INTERNAL_TOKEN opens nothing, even to an empty header", async () => {
  const env = makeEnv({ INTERNAL_TOKEN: "" });
  const res = await handleInternal(req("GET", "/__internal/snapshot", { token: "" }), env);
  assert.equal(res.status, 401);
});

test("snapshot GET answers 404 until something has been stored", async () => {
  const env = makeEnv();
  const res = await handleInternal(req("GET", "/__internal/snapshot"), env);
  assert.equal(res.status, 404);
});

test("snapshot PUT stores the bytes under the fixed key, and GET returns them", async () => {
  const env = makeEnv();
  const bytes = new TextEncoder().encode("SQLite format 3\u0000 fake-db-bytes");
  const put = await handleInternal(req("PUT", "/__internal/snapshot", { body: bytes, length: bytes.byteLength }), env);
  assert.equal(put.status, 204);
  assert.deepEqual(env.BACKUPS.puts, [SNAPSHOT_KEY]);

  const get = await handleInternal(req("GET", "/__internal/snapshot"), env);
  assert.equal(get.status, 200);
  assert.equal(get.headers.get("content-type"), "application/octet-stream");
  assert.deepEqual(new Uint8Array(await get.arrayBuffer()), bytes);
});

test("snapshot PUT needs a declared length", async () => {
  const env = makeEnv();
  const res = await handleInternal(req("PUT", "/__internal/snapshot", { body: "abc" }), env);
  assert.equal(res.status, 411);
  assert.equal(env.BACKUPS.puts.length, 0);
});

test("snapshot PUT refuses a declared length over the cap before reading the body", async () => {
  const env = makeEnv();
  const res = await handleInternal(req("PUT", "/__internal/snapshot", { body: "x", length: MAX_SNAPSHOT_BYTES + 1 }), env);
  assert.equal(res.status, 413);
  assert.equal(env.BACKUPS.puts.length, 0);
});

test("unsupported methods are refused with an Allow header", async () => {
  const env = makeEnv();
  const snap = await handleInternal(req("POST", "/__internal/snapshot", { body: "x", length: 1 }), env);
  assert.equal(snap.status, 405);
  assert.equal(snap.headers.get("allow"), "GET, PUT");
  const mail = await handleInternal(req("GET", "/__internal/email"), env);
  assert.equal(mail.status, 405);
  assert.equal(mail.headers.get("allow"), "POST");
});

test("unknown internal paths are 404", async () => {
  const res = await handleInternal(req("GET", "/__internal/nope"), makeEnv());
  assert.equal(res.status, 404);
});

test("e-mail is sent from the configured sender and answers 202", async () => {
  const env = makeEnv();
  const body = JSON.stringify({ to: "trader@example.com", subject: "Reset", text: "Your code is 123456." });
  const res = await handleInternal(req("POST", "/__internal/email", { body }), env);
  assert.equal(res.status, 202);
  assert.deepEqual(env.EMAIL.sent, [
    { from: "noreply@klevs.example.com", to: "trader@example.com", subject: "Reset", text: "Your code is 123456." },
  ]);
});

test("e-mail with bad input is refused with 400 and nothing is sent", async () => {
  const bad = [
    "not json",
    JSON.stringify({ to: "no-at-sign", subject: "s", text: "t" }),
    JSON.stringify({ to: "a@b.co", subject: "", text: "t" }),
    JSON.stringify({ to: "a@b.co", subject: "s", text: "" }),
    JSON.stringify({ to: "a@b.co", subject: "x".repeat(201), text: "t" }),
    JSON.stringify({ to: "a@b.co", subject: "s", text: "x".repeat(10_001) }),
    JSON.stringify(["array"]),
  ];
  for (const body of bad) {
    const env = makeEnv();
    const res = await handleInternal(req("POST", "/__internal/email", { body }), env);
    assert.equal(res.status, 400, body.slice(0, 40));
    assert.equal(env.EMAIL.sent.length, 0);
  }
});

test("e-mail is refused with 400 when no sender is configured", async () => {
  const env = makeEnv({ KLEVS_MAIL_FROM: "" });
  const body = JSON.stringify({ to: "a@b.co", subject: "s", text: "t" });
  const res = await handleInternal(req("POST", "/__internal/email", { body }), env);
  assert.equal(res.status, 400);
});

test("a failed send answers 502 with no body, so nothing leaks back to the app", async () => {
  const env = makeEnv();
  env.EMAIL.fail = true;
  const body = JSON.stringify({ to: "trader@example.com", subject: "Reset", text: "code 987654" });
  const res = await handleInternal(req("POST", "/__internal/email", { body }), env);
  assert.equal(res.status, 502);
  assert.equal(await res.text(), "");
});

test("parseEmailRequest accepts a well-formed message and rejects other shapes", () => {
  assert.deepEqual(parseEmailRequest({ to: "a@b.co", subject: "s", text: "t" }), { to: "a@b.co", subject: "s", text: "t" });
  assert.equal(parseEmailRequest(null), null);
  assert.equal(parseEmailRequest({ to: 5, subject: "s", text: "t" }), null);
});
