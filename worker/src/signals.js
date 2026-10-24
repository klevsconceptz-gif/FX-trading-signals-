import { ApiError, requireDatabase } from "./http.js";

// The scheduled publisher writes one `signal_cache` row per key. Every row of one publish
// shares a run_id, and the `scan` row is written last. Readers trust the other rows only
// when their run_id matches the committed `scan` row, and they never serve a snapshot older
// than the age limit (old "BUY NOW" advice must not be shown as current).
export const SIGNAL_MAX_AGE_SECONDS = 2 * 60 * 60;
const SIGNAL_MAX_AGE_CEILING = 24 * 60 * 60;

export function signalMaxAge(env) {
  const configured = Number(env.KFX_SIGNAL_MAX_AGE_SECONDS || SIGNAL_MAX_AGE_SECONDS);
  if (!Number.isFinite(configured) || configured <= 0) return SIGNAL_MAX_AGE_SECONDS;
  return Math.min(Math.floor(configured), SIGNAL_MAX_AGE_CEILING);
}

export async function readSignalRow(env, key) {
  return requireDatabase(env)
    .prepare("SELECT key, run_id, generated_at, data_source, payload FROM signal_cache WHERE key = ?")
    .bind(key)
    .first();
}

export function snapshotState(row, maxAge, now = Date.now() / 1000) {
  if (!row) return "missing";
  return now - Number(row.generated_at) > maxAge ? "stale" : "fresh";
}

function jsonText(payload) {
  return new Response(payload, {
    status: 200,
    headers: { "Cache-Control": "no-store", "Content-Type": "application/json; charset=utf-8" },
  });
}

/**
 * Serve one signal key ("scan", "instructions", or "pair:SYMBOL") from the committed snapshot.
 * Throws ApiError(503) when no fresh, committed snapshot exists, and ApiError(404) when a pair
 * has no data.
 */
export async function signalResponse(env, key, now = Date.now() / 1000) {
  const maxAge = signalMaxAge(env);
  const committed = await readSignalRow(env, "scan");
  if (!committed) throw new ApiError(503, "no signal snapshot has been published yet");
  if (snapshotState(committed, maxAge, now) === "stale") {
    throw new ApiError(503, "the latest signal snapshot is too old to show; the scheduled publish has not completed");
  }
  if (key === "scan") return jsonText(committed.payload);

  const row = await readSignalRow(env, key);
  if (!row) {
    if (key.startsWith("pair:")) throw new ApiError(404, "no data");
    throw new ApiError(503, "that signal output has not been published yet");
  }
  if (Number(row.run_id) !== Number(committed.run_id)) {
    throw new ApiError(503, "the signal snapshot is being updated; try again shortly");
  }
  return jsonText(row.payload);
}

export async function signalHealth(env, now = Date.now() / 1000) {
  const maxAge = signalMaxAge(env);
  const row = await readSignalRow(env, "scan");
  return {
    state: snapshotState(row, maxAge, now),
    generated_at: row ? Number(row.generated_at) : null,
    data_source: row ? row.data_source : null,
    max_age_seconds: maxAge,
  };
}
