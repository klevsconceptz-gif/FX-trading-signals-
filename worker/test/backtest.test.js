import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.js";
import { backtestResponse, listBacktests } from "../src/backtest.js";

const STORED = JSON.stringify({
  symbol: "EURUSD",
  run_at: "2026-10-10T06:00:00Z",
  data_source: "live (yfinance, 1h)",
  params: { bars: 3000, bars_used: 3000, equity: 10000, risk_per_trade_pct: 0.75 },
  metrics: { trades: 3, win_rate: 0.66 },
  equity_curve: { t: [1, 2], v: [10000, 10050] },
  trades: [],
});

// Fake D1 for backtest_results. `rows` maps symbol -> row.
function fakeEnv(rows = {}) {
  return {
    DB: {
      prepare(sql) {
        return {
          bind(...args) {
            return {
              first: async () => {
                assert.match(sql, /FROM backtest_results WHERE symbol = \?/);
                return rows[args[0]] ? { payload: rows[args[0]].payload } : null;
              },
            };
          },
          all: async () => ({
            results: Object.values(rows).map(({ symbol, run_at, data_source }) => ({ symbol, run_at, data_source })),
          }),
        };
      },
    },
  };
}

test("a stored result is served exactly as the job wrote it, with no-store caching", async () => {
  const env = fakeEnv({ EURUSD: { symbol: "EURUSD", payload: STORED } });
  const response = await backtestResponse(env, "eurusd");
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("Cache-Control"), "no-store");
  assert.equal(await response.text(), STORED);
});

test("an unknown symbol is a 404 and never touches the database", async () => {
  const env = { DB: { prepare: () => { throw new Error("must not query"); } } };
  await assert.rejects(backtestResponse(env, "NOTAPAIR"), (e) => e.status === 404 && /unknown symbol/.test(e.detail));
});

test("a known symbol with no stored result says so", async () => {
  await assert.rejects(backtestResponse(fakeEnv({}), "GBPUSD"), (e) => e.status === 404 && /no stored backtest for GBPUSD/.test(e.detail));
});

test("the list shows which pairs have results, without their payloads", async () => {
  const env = fakeEnv({ EURUSD: { symbol: "EURUSD", payload: STORED, run_at: "2026-10-10T06:00:00Z", data_source: "live (yfinance, 1h)" } });
  const body = await listBacktests(env);
  assert.deepEqual(body.results, [{ symbol: "EURUSD", run_at: "2026-10-10T06:00:00Z", data_source: "live (yfinance, 1h)" }]);
});

test("backtest results need a signed-in paid account", async () => {
  const env = { DB: fakeEnv({}).DB, KV: { get: async () => null, put: async () => {}, delete: async () => {} } };
  for (const path of ["/api/backtest", "/api/backtest/EURUSD"]) {
    const response = await worker.fetch(new Request(`https://api.example${path}`), env);
    assert.equal(response.status, 401, path);
  }
});

test("the old POST route is gone", async () => {
  const response = await worker.fetch(new Request("https://api.example/api/backtest", { method: "POST" }), {});
  assert.equal(response.status, 404);
});
