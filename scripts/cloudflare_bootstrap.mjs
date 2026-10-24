#!/usr/bin/env node
// Finds or creates the Cloudflare resources the deployment depends on, through the REST API:
//
//   D1 database    binding `klev` in worker/wrangler.jsonc (created with the name in that file)
//   KV namespace   binding `voom` in worker/wrangler.jsonc (created with the title below)
//   Pages project  CLOUDFLARE_PAGES_PROJECT (default "klv-fx-trading-signals")
//
// It is idempotent and creates only what is missing. If the D1 or KV IDs in the Wrangler config do
// not match the account, it exits 1 and says what to change. Run it locally with --write-config to
// rewrite those IDs, then commit the file.
//
// Usage:
//   node scripts/cloudflare_bootstrap.mjs               find or create, then verify
//   node scripts/cloudflare_bootstrap.mjs --check       report only; create nothing
//   node scripts/cloudflare_bootstrap.mjs --write-config  also rewrite stale IDs in the config
//
// Environment:
//   CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID   required
//   CLOUDFLARE_PAGES_PROJECT                      default "klv-fx-trading-signals"
//   CLOUDFLARE_PAGES_BRANCH                       default "main"
//   CLOUDFLARE_WRANGLER_CONFIG, CLOUDFLARE_API_BASE   overrides (used by the tests)
// The token is sent only in the Authorization header and is never printed.

import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const KV_TITLE = "klv-fx-trading-signals-voom";
export const DEFAULT_PAGES_PROJECT = "klv-fx-trading-signals";
export const DEFAULT_API_BASE = "https://api.cloudflare.com/client/v4";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const DEFAULT_CONFIG = path.join(ROOT, "worker", "wrangler.jsonc");

// Wrangler config is JSON with optional full-line // comments.
export function parseWranglerConfig(text) {
  const withoutComments = text
    .split("\n")
    .map((line) => (line.trim().startsWith("//") ? "" : line))
    .join("\n");
  return JSON.parse(withoutComments);
}

export function readBindings(config) {
  const d1 = (config.d1_databases || []).find((db) => db.binding === "klev");
  const kv = (config.kv_namespaces || []).find((ns) => ns.binding === "voom");
  if (!d1 || !kv) {
    throw new Error("worker config must declare D1 binding 'klev' and KV binding 'voom'");
  }
  return {
    d1: { name: d1.database_name, configured: d1.database_id },
    kv: { configured: kv.id },
  };
}

export function makeClient(base, token) {
  return async function call(method, pathname, body) {
    const res = await fetch(`${base}${pathname}`, {
      method,
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let json = null;
    try {
      json = await res.json();
    } catch {
      // A non-JSON body is reported through the HTTP status.
    }
    return {
      status: res.status,
      success: res.ok && json !== null && json.success !== false,
      result: json ? json.result : undefined,
      errors: json && Array.isArray(json.errors) ? json.errors : [],
    };
  };
}

function apiError(what, res) {
  const detail = res.errors.map((e) => e.message).filter(Boolean).join("; ") || "no detail";
  return new Error(`${what} failed (HTTP ${res.status}): ${detail}`);
}

async function findFirst(call, listPath, match) {
  for (let page = 1; page <= 100; page += 1) {
    const res = await call("GET", `${listPath}?per_page=100&page=${page}`);
    if (!res.success) throw apiError(`listing ${listPath}`, res);
    const items = Array.isArray(res.result) ? res.result : [];
    const hit = items.find(match);
    if (hit) return hit;
    if (items.length < 100) return null;
  }
  return null;
}

// Each ensure* returns { label, id, configured, state } where state is
// "ok" | "created" | "missing" (check mode) | "stale" (exists, but not the configured ID).
export async function ensureD1(call, accountId, want, check) {
  const label = `D1 database ${want.name}`;
  if (want.configured) {
    const byId = await call("GET", `/accounts/${accountId}/d1/database/${want.configured}`);
    if (byId.success) return { label, id: want.configured, configured: want.configured, state: "ok" };
  }
  const byName = await findFirst(call, `/accounts/${accountId}/d1/database`, (db) => db.name === want.name);
  if (byName) {
    const id = byName.uuid ?? byName.id;
    return { label, id, configured: want.configured, state: id === want.configured ? "ok" : "stale" };
  }
  if (check) return { label, id: null, configured: want.configured, state: "missing" };
  const created = await call("POST", `/accounts/${accountId}/d1/database`, { name: want.name });
  if (!created.success) throw apiError(`creating ${label}`, created);
  const id = created.result.uuid ?? created.result.id;
  return { label, id, configured: want.configured, state: "created" };
}

export async function ensureKV(call, accountId, want, check) {
  const label = `KV namespace ${KV_TITLE}`;
  if (want.configured) {
    const byId = await call("GET", `/accounts/${accountId}/storage/kv/namespaces/${want.configured}`);
    if (byId.success) return { label, id: want.configured, configured: want.configured, state: "ok" };
  }
  const byTitle = await findFirst(call, `/accounts/${accountId}/storage/kv/namespaces`, (ns) => ns.title === KV_TITLE);
  if (byTitle) {
    return { label, id: byTitle.id, configured: want.configured, state: byTitle.id === want.configured ? "ok" : "stale" };
  }
  if (check) return { label, id: null, configured: want.configured, state: "missing" };
  const created = await call("POST", `/accounts/${accountId}/storage/kv/namespaces`, { title: KV_TITLE });
  if (!created.success) throw apiError(`creating ${label}`, created);
  return { label, id: created.result.id, configured: want.configured, state: "created" };
}

export async function ensurePages(call, accountId, { name, branch }, check) {
  const label = `Pages project ${name}`;
  const got = await call("GET", `/accounts/${accountId}/pages/projects/${name}`);
  if (got.success) return { label, id: name, configured: undefined, state: "ok" };
  if (got.status !== 404) throw apiError(`reading ${label}`, got);
  if (check) return { label, id: null, configured: undefined, state: "missing" };
  const created = await call("POST", `/accounts/${accountId}/pages/projects`, { name, production_branch: branch });
  if (!created.success) throw apiError(`creating ${label}`, created);
  return { label, id: name, configured: undefined, state: "created" };
}

// Rewrites each stale configured ID, and only that exact quoted value, so the rest of the file is untouched.
export function rewriteConfigIds(text, stale) {
  let next = text;
  for (const { configured, id } of stale) {
    const quoted = `"${configured}"`;
    const count = next.split(quoted).length - 1;
    if (count !== 1) throw new Error(`expected exactly one ${quoted} in the Wrangler config, found ${count}`);
    next = next.replace(quoted, `"${id}"`);
  }
  return next;
}

async function main(argv, env) {
  const check = argv.includes("--check");
  const writeConfig = argv.includes("--write-config");
  const token = env.CLOUDFLARE_API_TOKEN;
  const accountId = env.CLOUDFLARE_ACCOUNT_ID;
  if (!token || !accountId) {
    console.error("CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID must be set.");
    return 1;
  }

  const configPath = env.CLOUDFLARE_WRANGLER_CONFIG || DEFAULT_CONFIG;
  const text = readFileSync(configPath, "utf8");
  const bindings = readBindings(parseWranglerConfig(text));
  const call = makeClient(env.CLOUDFLARE_API_BASE || DEFAULT_API_BASE, token);
  const pages = { name: env.CLOUDFLARE_PAGES_PROJECT || DEFAULT_PAGES_PROJECT, branch: env.CLOUDFLARE_PAGES_BRANCH || "main" };

  const results = [];
  results.push(await ensureD1(call, accountId, bindings.d1, check));
  results.push(await ensureKV(call, accountId, bindings.kv, check));
  results.push(await ensurePages(call, accountId, pages, check));

  for (const r of results) {
    console.log(`${r.label}: ${r.state}${r.id ? ` (${r.id})` : ""}`);
  }

  // A resource that is missing (check mode) or differs from the config needs an action.
  const stale = results.filter((r) => r.configured !== undefined && r.id !== r.configured);
  const pending = results.some((r) => r.state === "missing");
  if (check && (pending || stale.length > 0)) {
    console.error("Check failed: some resources are missing or the Wrangler config IDs differ.");
    return 1;
  }
  if (stale.length === 0) return 0;

  if (!writeConfig) {
    console.error(
      `worker/wrangler.jsonc does not match this account. Run "node scripts/cloudflare_bootstrap.mjs --write-config" locally, then commit the file.`,
    );
    return 1;
  }
  writeFileSync(configPath, rewriteConfigIds(text, stale));
  console.log(`Updated ${path.relative(ROOT, configPath)}. Review the diff and commit it.`);
  return 0;
}

const isMain = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  main(process.argv.slice(2), process.env)
    .then((code) => process.exit(code))
    .catch((err) => {
      console.error(err.message);
      process.exit(1);
    });
}
