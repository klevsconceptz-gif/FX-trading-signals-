import test from "node:test";
import assert from "node:assert/strict";
import { requireAccess, verifyAccessJwt, clearAccessKeyCache } from "../src/access.js";
import worker from "../src/index.js";

const TEAM = "example-team.cloudflareaccess.com";
const AUD = "test-app-aud-0123456789abcdef";
const KID = "test-kid-1";

const enc = new TextEncoder();
function b64url(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
function jsonB64(value) {
  return b64url(enc.encode(JSON.stringify(value)));
}

async function makeKeyPair() {
  const pair = await crypto.subtle.generateKey(
    { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
    true,
    ["sign", "verify"],
  );
  const jwk = await crypto.subtle.exportKey("jwk", pair.publicKey);
  return { privateKey: pair.privateKey, jwk: { ...jwk, kid: KID, alg: "RS256", use: "sig" } };
}

async function signToken(privateKey, payload, header = { alg: "RS256", kid: KID, typ: "JWT" }) {
  const signingInput = `${jsonB64(header)}.${jsonB64(payload)}`;
  const signature = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", privateKey, enc.encode(signingInput));
  return `${signingInput}.${b64url(new Uint8Array(signature))}`;
}

function certsFetch(jwk) {
  return async (url) => {
    assert.equal(url, `https://${TEAM}/cdn-cgi/access/certs`);
    return new Response(JSON.stringify({ keys: [jwk] }), { status: 200 });
  };
}

function claims(overrides = {}) {
  const now = Math.floor(Date.now() / 1000);
  return { aud: [AUD], iss: `https://${TEAM}`, email: "admin@example.test", iat: now, nbf: now, exp: now + 300, ...overrides };
}

const ENV = { CF_ACCESS_TEAM_DOMAIN: TEAM, CF_ACCESS_AUD: AUD };

test("a correctly signed Access token verifies and returns the identity", async () => {
  clearAccessKeyCache();
  const { privateKey, jwk } = await makeKeyPair();
  const token = await signToken(privateKey, claims());
  const payload = await verifyAccessJwt(token, { teamDomain: TEAM, aud: AUD }, { fetch: certsFetch(jwk) });
  assert.equal(payload.email, "admin@example.test");
});

test("tokens for another application audience are rejected", async () => {
  clearAccessKeyCache();
  const { privateKey, jwk } = await makeKeyPair();
  const token = await signToken(privateKey, claims({ aud: ["some-other-app"] }));
  await assert.rejects(verifyAccessJwt(token, { teamDomain: TEAM, aud: AUD }, { fetch: certsFetch(jwk) }), /audience/);
});

test("expired tokens are rejected", async () => {
  clearAccessKeyCache();
  const { privateKey, jwk } = await makeKeyPair();
  const token = await signToken(privateKey, claims({ exp: Math.floor(Date.now() / 1000) - 600 }));
  await assert.rejects(verifyAccessJwt(token, { teamDomain: TEAM, aud: AUD }, { fetch: certsFetch(jwk) }), /expired/);
});

test("a tampered payload fails the signature check", async () => {
  clearAccessKeyCache();
  const { privateKey, jwk } = await makeKeyPair();
  const token = await signToken(privateKey, claims({ email: "someone@example.test" }));
  const [h, , s] = token.split(".");
  const forged = `${h}.${jsonB64(claims({ email: "admin@example.test" }))}.${s}`;
  await assert.rejects(verifyAccessJwt(forged, { teamDomain: TEAM, aud: AUD }, { fetch: certsFetch(jwk) }), /signature/);
});

test("a token from a different issuer is rejected", async () => {
  clearAccessKeyCache();
  const { privateKey, jwk } = await makeKeyPair();
  const token = await signToken(privateKey, claims({ iss: "https://attacker.cloudflareaccess.com" }));
  await assert.rejects(verifyAccessJwt(token, { teamDomain: TEAM, aud: AUD }, { fetch: certsFetch(jwk) }), /issuer/);
});

test("non-RS256 headers are rejected", async () => {
  clearAccessKeyCache();
  const { privateKey, jwk } = await makeKeyPair();
  const token = await signToken(privateKey, claims(), { alg: "none", kid: KID });
  await assert.rejects(verifyAccessJwt(token, { teamDomain: TEAM, aud: AUD }, { fetch: certsFetch(jwk) }), /header/);
});

test("requests without Access configuration fail closed", async () => {
  await assert.rejects(requireAccess({}, new Request("https://api.example/api/admin/users")), (error) => {
    assert.equal(error.status, 503);
    return true;
  });
});

test("requests without the Access header are refused", async () => {
  await assert.rejects(requireAccess(ENV, new Request("https://api.example/api/admin/users")), (error) => {
    assert.equal(error.status, 403);
    return true;
  });
});

test("a forged header is refused with 403, not a server error", async () => {
  clearAccessKeyCache();
  const request = new Request("https://api.example/api/admin/users", {
    headers: { "CF-Access-Jwt-Assertion": "not.a.jwt" },
  });
  await assert.rejects(requireAccess(ENV, request), (error) => {
    assert.equal(error.status, 403);
    return true;
  });
});

test("admin routes need the app login before Access is checked", async () => {
  const response = await worker.fetch(new Request("https://api.example/api/admin/users"), {});
  assert.equal(response.status, 401);
});
