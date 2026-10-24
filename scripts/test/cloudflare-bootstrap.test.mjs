// Offline tests for scripts/cloudflare_bootstrap.mjs. A local stub stands in for the Cloudflare API.
import { test } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { execFile } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const SCRIPT = path.join(ROOT, "scripts", "cloudflare_bootstrap.mjs");
const ACCOUNT = "acc123";
const CONFIG_D1 = "1a4ea5fa-6c99-4f43-b895-e80ecf0b63de";
const CONFIG_KV = "4d28a5811a4d47de938a2a34ce22af60";

function sampleConfig() {
  return `{
  "name": "klv-fx-trading-signals",
  "main": "src/index.js",
  "d1_databases": [
    {
      "binding": "klev",
      "database_name": "klev",
      "database_id": "${CONFIG_D1}",
      "migrations_dir": "migrations"
    }
  ],
  "kv_namespaces": [
    {
      "binding": "voom",
      "id": "${CONFIG_KV}"
    }
  ]
}
`;
}

// state: which resources exist. Returns the handler table and a log of requests.
function startStub(state) {
  const log = [];
  const send = (res, status, body) => {
    res.writeHead(status, { "Content-Type": "application/json" });
    res.end(JSON.stringify(body));
  };
  const ok = (result) => ({ success: true, errors: [], result });
  const fail = { success: false, errors: [{ code: 7000, message: "Not found" }], result: null };
  const server = http.createServer((req, res) => {
    let raw = "";
    req.on("data", (c) => (raw += c));
    req.on("end", () => {
      const url = new URL(req.url, "http://stub");
      const key = `${req.method} ${url.pathname}`;
      log.push(key);
      const base = `/client/v4/accounts/${ACCOUNT}`;
      if (req.headers.authorization !== "Bearer test-token") return send(res, 403, fail);
      if (key === `GET ${base}/d1/database/${CONFIG_D1}`) return state.d1ById ? send(res, 200, ok({ uuid: CONFIG_D1, name: "klev" })) : send(res, 404, fail);
      if (key === `GET ${base}/d1/database`) return send(res, 200, ok(state.d1List));
      if (key === `POST ${base}/d1/database`) return send(res, 200, ok({ uuid: "new-d1-uuid", name: JSON.parse(raw).name }));
      if (key === `GET ${base}/storage/kv/namespaces/${CONFIG_KV}`) return state.kvById ? send(res, 200, ok({ id: CONFIG_KV })) : send(res, 404, fail);
      if (key === `GET ${base}/storage/kv/namespaces`) return send(res, 200, ok(state.kvList));
      if (key === `POST ${base}/storage/kv/namespaces`) return send(res, 200, ok({ id: "new-kv-id", title: JSON.parse(raw).title }));
      if (key === `GET ${base}/pages/projects/klv-fx-trading-signals`) return state.pages ? send(res, 200, ok({ name: "klv-fx-trading-signals" })) : send(res, 404, fail);
      if (key === `POST ${base}/pages/projects`) return send(res, 200, ok({ name: JSON.parse(raw).name }));
      return send(res, 404, fail);
    });
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => resolve({ server, log, base: `http://127.0.0.1:${server.address().port}/client/v4` }));
  });
}

// Async on purpose: the stub server runs in this process, so a blocking spawn would never be answered.
function runScript(args, { base, configPath }) {
  return new Promise((resolve) => {
    execFile(process.execPath, [SCRIPT, ...args], {
      env: {
        PATH: process.env.PATH,
        CLOUDFLARE_API_TOKEN: "test-token",
        CLOUDFLARE_ACCOUNT_ID: ACCOUNT,
        CLOUDFLARE_API_BASE: base,
        CLOUDFLARE_WRANGLER_CONFIG: configPath,
      },
    }, (error, stdout, stderr) => {
      resolve({ status: error ? error.code : 0, stdout, stderr });
    });
  });
}

function closeStub(stub) {
  stub.server.closeAllConnections();
  stub.server.close();
}

function tempConfig() {
  const dir = mkdtempSync(path.join(tmpdir(), "bootstrap-"));
  const file = path.join(dir, "wrangler.jsonc");
  writeFileSync(file, sampleConfig());
  return file;
}

test("everything exists and matches the config: exit 0, no writes", async () => {
  const stub = await startStub({ d1ById: true, kvById: true, pages: true, d1List: [], kvList: [] });
  try {
    const configPath = tempConfig();
    const r = await runScript([], { base: stub.base, configPath });
    assert.equal(r.status, 0, r.stderr);
    assert.ok(!stub.log.some((k) => k.startsWith("POST")), `unexpected writes: ${stub.log}`);
    assert.equal(readFileSync(configPath, "utf8"), sampleConfig());
    assert.ok(!r.stdout.includes("test-token"));
  } finally {
    closeStub(stub);
  }
});

test("missing resources are created, and the config is rewritten with --write-config", async () => {
  const stub = await startStub({ d1ById: false, kvById: false, pages: false, d1List: [], kvList: [] });
  try {
    const configPath = tempConfig();
    const fail = await runScript([], { base: stub.base, configPath });
    assert.equal(fail.status, 1, "stale config must fail without --write-config");
    assert.match(fail.stderr, /--write-config/);
    assert.equal(readFileSync(configPath, "utf8"), sampleConfig(), "config untouched without --write-config");

    const ok = await runScript(["--write-config"], { base: stub.base, configPath });
    assert.equal(ok.status, 0, ok.stderr);
    const written = readFileSync(configPath, "utf8");
    assert.match(written, /"database_id": "new-d1-uuid"/);
    assert.match(written, /"id": "new-kv-id"/);
    assert.ok(stub.log.includes(`POST /client/v4/accounts/${ACCOUNT}/pages/projects`));
  } finally {
    closeStub(stub);
  }
});

test("a D1 database with the configured name but another ID is reported as stale", async () => {
  const stub = await startStub({
    d1ById: false,
    kvById: true,
    pages: true,
    d1List: [{ uuid: "other-d1", name: "klev" }],
    kvList: [],
  });
  try {
    const configPath = tempConfig();
    const r = await runScript([], { base: stub.base, configPath });
    assert.equal(r.status, 1);
    assert.match(r.stdout, /D1 database klev: stale \(other-d1\)/);
    assert.ok(!stub.log.some((k) => k === `POST /client/v4/accounts/${ACCOUNT}/d1/database`), "must not create a second 'klev'");
  } finally {
    closeStub(stub);
  }
});

test("--check creates nothing and fails when something is missing", async () => {
  const stub = await startStub({ d1ById: true, kvById: true, pages: false, d1List: [], kvList: [] });
  try {
    const r = await runScript(["--check"], { base: stub.base, configPath: tempConfig() });
    assert.equal(r.status, 1);
    assert.match(r.stdout, /Pages project klv-fx-trading-signals: missing/);
    assert.ok(!stub.log.some((k) => k.startsWith("POST")), `check mode must not write: ${stub.log}`);
  } finally {
    closeStub(stub);
  }
});

test("missing credentials fail before any request", async () => {
  const r = await new Promise((resolve) =>
    execFile(process.execPath, [SCRIPT], { env: { PATH: process.env.PATH } }, (error, stdout, stderr) =>
      resolve({ status: error ? error.code : 0, stderr }),
    ),
  );
  assert.equal(r.status, 1);
  assert.match(r.stderr, /CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID must be set/);
});
