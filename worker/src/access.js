// Cloudflare Access verification for the admin API.
//
// When Access fronts a request, Cloudflare adds a signed JWT in the CF-Access-Jwt-Assertion header.
// The Worker verifies it against the team's public keys (RS256), the application audience (AUD),
// the issuer and the expiry. Verification is offline apart from the cached key set, and it uses
// WebCrypto only, so the Worker needs no extra dependency.

import { ApiError } from "./http.js";

const JWKS_TTL_MS = 60 * 60 * 1000;
const CLOCK_SKEW_SECONDS = 60;
const TEAM_DOMAIN_PATTERN = /^[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$/;

const keyCache = new Map(); // team domain -> { fetchedAt, keys: Map<kid, CryptoKey> }

function base64UrlToBytes(value) {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

function decodeJsonPart(part) {
  return JSON.parse(new TextDecoder().decode(base64UrlToBytes(part)));
}

async function loadKeys(teamDomain, fetchImpl) {
  const response = await fetchImpl(`https://${teamDomain}/cdn-cgi/access/certs`);
  if (!response.ok) throw new Error(`Access certificate endpoint returned ${response.status}`);
  const body = await response.json();
  const keys = new Map();
  for (const jwk of body.keys || []) {
    if (jwk.kty !== "RSA" || !jwk.kid) continue;
    const key = await crypto.subtle.importKey(
      "jwk",
      { kty: jwk.kty, n: jwk.n, e: jwk.e, alg: "RS256", ext: true },
      { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
      false,
      ["verify"],
    );
    keys.set(jwk.kid, key);
  }
  return keys;
}

async function keyFor(teamDomain, kid, fetchImpl, now) {
  const cached = keyCache.get(teamDomain);
  if (cached && now - cached.fetchedAt < JWKS_TTL_MS && cached.keys.has(kid)) return cached.keys.get(kid);
  // Refresh when the key is unknown, which happens after Access rotates its keys.
  const keys = await loadKeys(teamDomain, fetchImpl);
  keyCache.set(teamDomain, { fetchedAt: now, keys });
  return keys.get(kid) || null;
}

// Returns the verified payload, or throws. Expects the team domain and audience from configuration.
export async function verifyAccessJwt(token, { teamDomain, aud }, options = {}) {
  const fetchImpl = options.fetch || fetch;
  const now = options.now ?? Date.now();
  const nowSec = Math.floor(now / 1000);

  const parts = String(token || "").split(".");
  if (parts.length !== 3) throw new Error("malformed token");
  const [headerPart, payloadPart, signaturePart] = parts;
  const header = decodeJsonPart(headerPart);
  if (header.alg !== "RS256" || !header.kid) throw new Error("unsupported token header");
  const payload = decodeJsonPart(payloadPart);

  const key = await keyFor(teamDomain, header.kid, fetchImpl, now);
  if (!key) throw new Error("unknown signing key");
  const signed = new TextEncoder().encode(`${headerPart}.${payloadPart}`);
  const valid = await crypto.subtle.verify(
    "RSASSA-PKCS1-v1_5",
    key,
    base64UrlToBytes(signaturePart),
    signed,
  );
  if (!valid) throw new Error("bad signature");

  const audiences = Array.isArray(payload.aud) ? payload.aud : [payload.aud];
  if (!audiences.includes(aud)) throw new Error("audience mismatch");
  if (payload.iss !== `https://${teamDomain}`) throw new Error("issuer mismatch");
  if (typeof payload.exp !== "number" || payload.exp + CLOCK_SKEW_SECONDS < nowSec) throw new Error("token expired");
  if (typeof payload.nbf === "number" && payload.nbf - CLOCK_SKEW_SECONDS > nowSec) throw new Error("token not yet valid");
  return payload;
}

// Enforces Access on a request. Fails closed when Access is not configured.
export async function requireAccess(env, request, options = {}) {
  const teamDomain = String(env?.CF_ACCESS_TEAM_DOMAIN || "").trim().toLowerCase();
  const aud = String(env?.CF_ACCESS_AUD || "").trim();
  if (!teamDomain || !aud) {
    throw new ApiError(503, "Cloudflare Access is not configured for the admin API (CF_ACCESS_TEAM_DOMAIN and CF_ACCESS_AUD)");
  }
  if (!TEAM_DOMAIN_PATTERN.test(teamDomain)) {
    throw new ApiError(503, "CF_ACCESS_TEAM_DOMAIN must be a host name such as your-team.cloudflareaccess.com");
  }
  const token = request.headers.get("CF-Access-Jwt-Assertion");
  if (!token) throw new ApiError(403, "Cloudflare Access sign-in is required for the admin API");
  try {
    return await verifyAccessJwt(token, { teamDomain, aud }, options);
  } catch (error) {
    if (error instanceof ApiError) throw error;
    console.warn("Access token rejected", error?.message || "error");
    throw new ApiError(403, "Cloudflare Access sign-in is required for the admin API");
  }
}

// Test hook: forget cached keys between tests.
export function clearAccessKeyCache() {
  keyCache.clear();
}
