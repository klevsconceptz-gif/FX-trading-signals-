const encoder = new TextEncoder();

export const PBKDF2_ITERATIONS = 200_000;
export const MIN_PASSWORD_LENGTH = 10;

export const PASSWORD_RULES = Object.freeze([
  { key: "length_10", label: "At least 10 characters" },
  { key: "uppercase", label: "An uppercase letter (A-Z)" },
  { key: "lowercase", label: "A lowercase letter (a-z)" },
  { key: "number", label: "A number (0-9)" },
  { key: "symbol", label: "A symbol (!@#$%…)" },
]);

export function passwordPolicy(password) {
  const value = typeof password === "string" ? password : "";
  const checks = {
    length_10: value.length >= MIN_PASSWORD_LENGTH,
    uppercase: /[A-Z]/.test(value),
    lowercase: /[a-z]/.test(value),
    number: /[0-9]/.test(value),
    symbol: /[^A-Za-z0-9]/.test(value),
  };
  return { valid: Object.values(checks).every(Boolean), checks, min_length: MIN_PASSWORD_LENGTH };
}

export function passwordPolicyError(password) {
  const { checks } = passwordPolicy(password);
  const labels = {
    length_10: `at least ${MIN_PASSWORD_LENGTH} characters`,
    uppercase: "an uppercase letter (A-Z)",
    lowercase: "a lowercase letter (a-z)",
    number: "a number (0-9)",
    symbol: "a symbol (!@#$%…)",
  };
  const missing = Object.entries(checks).filter(([, passed]) => !passed).map(([key]) => labels[key]);
  return missing.length ? `password must include ${missing.join(", ")}` : null;
}

function bytesToBase64Url(bytes) {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function base64UrlToBytes(value) {
  const base64 = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
  const binary = atob(padded);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

async function deriveBits(password, salt, iterations = PBKDF2_ITERATIONS) {
  const material = await crypto.subtle.importKey(
    "raw",
    encoder.encode(password),
    "PBKDF2",
    false,
    ["deriveBits"],
  );
  return new Uint8Array(await crypto.subtle.deriveBits(
    { name: "PBKDF2", hash: "SHA-256", salt, iterations },
    material,
    256,
  ));
}

function constantTimeEqual(left, right) {
  if (left.length !== right.length) return false;
  let difference = 0;
  for (let i = 0; i < left.length; i += 1) difference |= left[i] ^ right[i];
  return difference === 0;
}

export async function hashPassword(password) {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const digest = await deriveBits(password, salt);
  return `pbkdf2-sha256$${PBKDF2_ITERATIONS}$${bytesToBase64Url(salt)}$${bytesToBase64Url(digest)}`;
}

export async function verifyPassword(password, stored) {
  if (typeof stored !== "string") return false;
  const [algorithm, rawIterations, saltText, digestText, extra] = stored.split("$");
  const iterations = Number(rawIterations);
  if (
    algorithm !== "pbkdf2-sha256" ||
    !Number.isInteger(iterations) || iterations < 100_000 || iterations > 1_000_000 ||
    !saltText || !digestText || extra !== undefined
  ) return false;

  try {
    const salt = base64UrlToBytes(saltText);
    const expected = base64UrlToBytes(digestText);
    if (salt.length !== 16 || expected.length !== 32) return false;
    const candidate = await deriveBits(password, salt, iterations);
    return constantTimeEqual(candidate, expected);
  } catch {
    return false;
  }
}

export function randomToken(byteLength = 32) {
  return bytesToBase64Url(crypto.getRandomValues(new Uint8Array(byteLength)));
}

export async function sha256Hex(value) {
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", encoder.encode(value)));
  return Array.from(digest, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function emailIsValid(value) {
  return typeof value === "string" && value.length <= 200 && /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(value);
}

export function phoneIsValid(value) {
  return typeof value === "string" && value.length >= 7 && value.length <= 20 && /^\+?[0-9][0-9\s\-().]{6,19}$/.test(value);
}

export function phoneKey(value) {
  return String(value ?? "").replace(/\D/g, "");
}

export function usernameIsValid(value) {
  return typeof value === "string" && /^[A-Za-z0-9_.-]{3,32}$/.test(value);
}

export function adminUsernameIsValid(value) {
  return typeof value === "string" && value.length >= 1 && value.length <= 64 && !/\s/.test(value);
}
