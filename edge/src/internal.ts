/**
 * Internal routes used by the KLEVS container. They are token-protected and never
 * reached by browsers: the container calls them over HTTPS on the public origin.
 *
 *   GET  /__internal/snapshot   200 snapshot | 404 none yet | 401 bad token
 *   PUT  /__internal/snapshot   204 stored | 411 no length | 413 too large | 401 bad token
 *   POST /__internal/email      202 accepted | 400 bad input | 502 send failed | 401 bad token
 *
 * Error responses carry no detail: a failed e-mail send never echoes the body.
 */

export const SNAPSHOT_KEY = "klevs/klevs.db";
export const MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024;
const MAX_SUBJECT = 200;
const MAX_TEXT = 10_000;
const MAX_ADDRESS = 254;
const ADDRESS_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export interface InternalEnv {
  BACKUPS: R2Bucket;
  EMAIL: SendEmail;
  INTERNAL_TOKEN: string;
  KLEVS_MAIL_FROM: string;
}

/** Constant-time comparison of two strings. Length differences are folded into the result. */
export function constantTimeEqual(a: string, b: string): boolean {
  const encoder = new TextEncoder();
  const x = encoder.encode(a);
  const y = encoder.encode(b);
  let diff = x.length ^ y.length;
  const length = Math.max(x.length, y.length);
  for (let i = 0; i < length; i++) {
    diff |= (x[i] ?? 0) ^ (y[i] ?? 0);
  }
  return diff === 0;
}

export function isAuthorised(request: Request, expected: string | undefined): boolean {
  if (!expected) return false; // an unset secret must never open the routes
  const sent = request.headers.get("x-internal-token") ?? "";
  return constantTimeEqual(sent, expected);
}

export async function handleInternal(request: Request, env: InternalEnv): Promise<Response> {
  if (!isAuthorised(request, env.INTERNAL_TOKEN)) {
    return new Response(null, { status: 401 });
  }
  const { pathname } = new URL(request.url);
  if (pathname === "/__internal/snapshot") {
    return handleSnapshot(request, env);
  }
  if (pathname === "/__internal/email") {
    return handleEmail(request, env);
  }
  return new Response(null, { status: 404 });
}

async function handleSnapshot(request: Request, env: InternalEnv): Promise<Response> {
  if (request.method === "GET") {
    const object = await env.BACKUPS.get(SNAPSHOT_KEY);
    if (object === null) {
      return new Response(null, { status: 404 });
    }
    return new Response(object.body, {
      status: 200,
      headers: { "content-type": "application/octet-stream", "content-length": String(object.size) },
    });
  }
  if (request.method === "PUT") {
    const declared = request.headers.get("content-length");
    if (declared === null || !/^\d+$/.test(declared)) {
      return new Response(null, { status: 411 });
    }
    if (Number(declared) > MAX_SNAPSHOT_BYTES) {
      return new Response(null, { status: 413 });
    }
    const body = await request.arrayBuffer();
    if (body.byteLength > MAX_SNAPSHOT_BYTES) {
      return new Response(null, { status: 413 });
    }
    await env.BACKUPS.put(SNAPSHOT_KEY, body, {
      httpMetadata: { contentType: "application/octet-stream" },
    });
    return new Response(null, { status: 204 });
  }
  return new Response(null, { status: 405, headers: { allow: "GET, PUT" } });
}

export interface EmailRequest {
  to: string;
  subject: string;
  text: string;
}

export function parseEmailRequest(value: unknown): EmailRequest | null {
  if (typeof value !== "object" || value === null) return null;
  const { to, subject, text } = value as Record<string, unknown>;
  if (typeof to !== "string" || to.length > MAX_ADDRESS || !ADDRESS_PATTERN.test(to)) return null;
  if (typeof subject !== "string" || subject.trim() === "" || subject.length > MAX_SUBJECT) return null;
  if (typeof text !== "string" || text.trim() === "" || text.length > MAX_TEXT) return null;
  return { to, subject, text };
}

async function handleEmail(request: Request, env: InternalEnv): Promise<Response> {
  if (request.method !== "POST") {
    return new Response(null, { status: 405, headers: { allow: "POST" } });
  }
  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return new Response(null, { status: 400 });
  }
  const message = parseEmailRequest(payload);
  if (message === null || !env.KLEVS_MAIL_FROM) {
    return new Response(null, { status: 400 });
  }
  try {
    await env.EMAIL.send({
      from: env.KLEVS_MAIL_FROM,
      to: message.to,
      subject: message.subject,
      text: message.text,
    });
  } catch {
    // Deliberately no detail: the relay error could echo the recipient or the body.
    return new Response(null, { status: 502 });
  }
  return new Response(null, { status: 202 });
}
