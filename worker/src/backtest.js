// Stored backtest results for the Backtest Lab. The backtest job (GitHub Actions, manual run) writes
// one row per symbol to D1 from live market data; paid members read them here. Nothing is computed
// in the Worker, and nothing is served without a paid account (the route checks that).

import { ApiError, requireDatabase } from "./http.js";
import { SYMBOLS } from "./universe.js";

function jsonText(payload) {
  return new Response(payload, {
    status: 200,
    headers: { "Cache-Control": "no-store", "Content-Type": "application/json; charset=utf-8" },
  });
}

/** One row per stored symbol: symbol, run time and data source (no payload), for the Lab's pair list. */
export async function listBacktests(env) {
  const result = await requireDatabase(env)
    .prepare("SELECT symbol, run_at, data_source FROM backtest_results ORDER BY symbol")
    .all();
  return { results: result?.results || [] };
}

/** The stored result for one symbol, exactly as the backtest job wrote it. */
export async function backtestResponse(env, symbol) {
  const normalised = String(symbol || "").toUpperCase();
  if (!SYMBOLS.has(normalised)) throw new ApiError(404, `unknown symbol ${normalised}`);
  const row = await requireDatabase(env)
    .prepare("SELECT payload FROM backtest_results WHERE symbol = ?")
    .bind(normalised)
    .first();
  if (!row) throw new ApiError(404, `no stored backtest for ${normalised} yet`);
  return jsonText(row.payload);
}
