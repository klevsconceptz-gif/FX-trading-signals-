import test from "node:test";
import assert from "node:assert/strict";
import { signalHealth, signalMaxAge, signalResponse, snapshotState, SIGNAL_MAX_AGE_SECONDS } from "../src/signals.js";
import { ApiError } from "../src/http.js";

const NOW = 1_800_000_000;

// Minimal D1 stand-in for `SELECT ... FROM signal_cache WHERE key = ?`.
function envWith(rows, extra = {}) {
  return {
    ...extra,
    klev: {
      prepare: () => ({
        bind: (key) => ({ first: async () => rows[key] || null }),
      }),
    },
  };
}

function row(key, runId, generatedAt, payload = {}) {
  return { key, run_id: runId, generated_at: generatedAt, data_source: "live (yfinance, 1h)", payload: JSON.stringify(payload) };
}

async function expectApiError(promise, status, pattern) {
  await assert.rejects(promise, (error) => {
    assert.ok(error instanceof ApiError, `expected ApiError, got ${error}`);
    assert.equal(error.status, status);
    assert.match(error.message, pattern);
    return true;
  });
}

test("no committed snapshot yet means 503, never an empty 'no advice' answer", async () => {
  await expectApiError(signalResponse(envWith({}), "scan", NOW), 503, /no signal snapshot/);
  await expectApiError(signalResponse(envWith({}), "instructions", NOW), 503, /no signal snapshot/);
});

test("a fresh committed scan is served as stored", async () => {
  const scan = row("scan", 5, NOW - 60, { summary: { pairs_scanned: 32 } });
  const response = await signalResponse(envWith({ scan }), "scan", NOW);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("Cache-Control"), "no-store");
  assert.deepEqual(JSON.parse(await response.text()), { summary: { pairs_scanned: 32 } });
});

test("a snapshot older than the age limit is refused, so old advice is never shown as current", async () => {
  const scan = row("scan", 5, NOW - SIGNAL_MAX_AGE_SECONDS - 1);
  await expectApiError(signalResponse(envWith({ scan }), "scan", NOW), 503, /too old/);
  await expectApiError(signalResponse(envWith({ scan }), "pair:EURUSD", NOW), 503, /too old/);
});

test("pair and instruction rows from an older run are not mixed with the committed scan", async () => {
  const rows = {
    scan: row("scan", 9, NOW - 30),
    "pair:EURUSD": row("pair:EURUSD", 8, NOW - 90, { symbol: "EURUSD" }),
    instructions: row("instructions", 9, NOW - 30, { instructions: [] }),
  };
  await expectApiError(signalResponse(envWith(rows), "pair:EURUSD", NOW), 503, /being updated/);
  const current = await signalResponse(envWith({ ...rows, "pair:EURUSD": row("pair:EURUSD", 9, NOW - 30, { symbol: "EURUSD" }) }), "pair:EURUSD", NOW);
  assert.equal(JSON.parse(await current.text()).symbol, "EURUSD");
  const instructions = await signalResponse(envWith(rows), "instructions", NOW);
  assert.deepEqual(JSON.parse(await instructions.text()), { instructions: [] });
});

test("an unknown pair is 404 and unpublished instructions are 503", async () => {
  const scan = row("scan", 3, NOW - 10);
  await expectApiError(signalResponse(envWith({ scan }), "pair:EURUSD", NOW), 404, /no data/);
  await expectApiError(signalResponse(envWith({ scan }), "instructions", NOW), 503, /not been published/);
});

test("snapshot state: missing, fresh and stale", () => {
  assert.equal(snapshotState(undefined, 7200, NOW), "missing");
  assert.equal(snapshotState({ generated_at: NOW - 100 }, 7200, NOW), "fresh");
  assert.equal(snapshotState({ generated_at: NOW - 7201 }, 7200, NOW), "stale");
});

test("the age limit defaults to 2 hours and is capped at 24 hours", () => {
  assert.equal(signalMaxAge({}), 7200);
  assert.equal(signalMaxAge({ KFX_SIGNAL_MAX_AGE_SECONDS: "0" }), 7200);
  assert.equal(signalMaxAge({ KFX_SIGNAL_MAX_AGE_SECONDS: "nonsense" }), 7200);
  assert.equal(signalMaxAge({ KFX_SIGNAL_MAX_AGE_SECONDS: "3600" }), 3600);
  assert.equal(signalMaxAge({ KFX_SIGNAL_MAX_AGE_SECONDS: "999999" }), 86400);
});

test("health reports the snapshot state and its source", async () => {
  const fresh = await signalHealth(envWith({ scan: row("scan", 1, NOW - 5) }), NOW);
  assert.equal(fresh.state, "fresh");
  assert.equal(fresh.data_source, "live (yfinance, 1h)");
  const missing = await signalHealth(envWith({}), NOW);
  assert.equal(missing.state, "missing");
  assert.equal(missing.generated_at, null);
});
