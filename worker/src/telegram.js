// Posts new or changed trade signals to a private Telegram channel through the Bot API.
//
// Two triggers call postSignalUpdates(): the Worker's hourly cron, and the signal publish job in
// GitHub Actions (through POST /api/internal/telegram/post). Both read the same committed D1
// snapshot and keep the same dedupe state in D1, so a signal is posted once per change.
//
// Only a fresh, committed, live snapshot is posted. Stale advice is never sent.
// Secrets: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (the private channel id, e.g. -100...),
// TELEGRAM_POST_SECRET (shared with the publish job). None of them is ever logged or returned.

import { ApiError, requireDatabase } from "./http.js";
import { readSignalRow, signalMaxAge, snapshotState } from "./signals.js";

const encoder = new TextEncoder();
const STATE_KEY = "posted";
const ACTIVE_VERDICTS = new Set(["BUY NOW", "SELL NOW"]);
const MAX_TEXT = 4000; // Telegram's limit is 4096 characters per message.
export const DISCLAIMER = "Signals only. Do not trade: this channel generates signals and a risk matrix.";

export function telegramConfigured(env) {
  return Boolean(env?.TELEGRAM_BOT_TOKEN && env?.TELEGRAM_CHAT_ID);
}

function safeEqual(a, b) {
  const x = encoder.encode(String(a));
  const y = encoder.encode(String(b));
  if (x.length !== y.length) return false;
  let diff = 0;
  for (let i = 0; i < x.length; i += 1) diff |= x[i] ^ y[i];
  return diff === 0;
}

/** The endpoint is for the publish job only: it needs the shared secret as a bearer token. */
export function telegramRequestAuthorized(request, env) {
  if (!env?.TELEGRAM_POST_SECRET) throw new ApiError(503, "TELEGRAM_POST_SECRET is not configured on the Worker");
  const header = request.headers.get("Authorization") || "";
  const match = /^Bearer (.+)$/.exec(header);
  return Boolean(match) && safeEqual(match[1], env.TELEGRAM_POST_SECRET);
}

/** The current instructions, but only from a fresh, committed, live snapshot; otherwise null. */
export async function freshInstructions(env, now = Date.now() / 1000) {
  const committed = await readSignalRow(env, "scan");
  if (!committed || snapshotState(committed, signalMaxAge(env), now) !== "fresh") return null;
  if (!String(committed.data_source || "").startsWith("live")) return null;
  const row = await readSignalRow(env, "instructions");
  if (!row || Number(row.run_id) !== Number(committed.run_id)) return null;
  const payload = JSON.parse(row.payload);
  return Array.isArray(payload.instructions) ? payload.instructions : [];
}

/** Fingerprint of what a subscriber needs to know: the verdict, the entry and the exit orders. */
export function fingerprint(item) {
  const exits = (item.exit_orders || []).map((o) => `${o.label}:${o.price ?? ""}`).join(",");
  return [item.verdict, item.entry, exits].join("|");
}

/** Pick the active signals whose fingerprint differs from the last posted state. */
export function selectChanges(instructions, previous) {
  const active = instructions.filter((item) => ACTIVE_VERDICTS.has(item.verdict));
  const next = {};
  const toPost = [];
  for (const item of active) {
    const uid = String(item.uid || item.symbol);
    const fp = fingerprint(item);
    next[uid] = fp;
    if (previous[uid] !== fp) toPost.push(item);
  }
  return { toPost, next };
}

function clip(text, limit = MAX_TEXT) {
  return text.length <= limit ? text : `${text.slice(0, limit - 1)}…`;
}

/** One message per signal: the plain-language command, the prediction and the disclaimer. */
export function formatSignal(item, generatedAt) {
  const when = new Date(Number(generatedAt) * 1000).toISOString().replace("T", " ").slice(0, 16);
  const pct = item.p_target_first === undefined ? "" : ` · P(target first) ${Math.round(item.p_target_first * 100)}%`;
  const bars = item.expected_bars ? ` · expected ${item.expected_bars} bars` : "";
  const lines = [
    `${item.verdict} ${item.display || item.symbol}`,
    item.one_line || "",
    `confidence ${item.confidence ?? "n/a"}${pct}${bars}`,
    `sent ${when} UTC`,
    "",
    DISCLAIMER,
  ];
  return clip(lines.join("\n"));
}

async function loadState(env) {
  const row = await requireDatabase(env)
    .prepare("SELECT value FROM telegram_state WHERE key = ?")
    .bind(STATE_KEY)
    .first();
  if (!row) return {};
  try {
    return JSON.parse(row.value) || {};
  } catch {
    return {};
  }
}

async function saveState(env, state, now = Date.now() / 1000) {
  await requireDatabase(env)
    .prepare("INSERT OR REPLACE INTO telegram_state (key, value, updated_at) VALUES (?, ?, ?)")
    .bind(STATE_KEY, JSON.stringify(state), now)
    .run();
}

/** Send one message through the Bot API. Errors never include the bot token. */
export async function sendTelegramMessage(env, text, fetchImpl = fetch) {
  const url = `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`;
  const response = await fetchImpl(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: env.TELEGRAM_CHAT_ID, text, disable_web_page_preview: true }),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok || body.ok !== true) {
    const reason = typeof body.description === "string" ? body.description : `HTTP ${response.status}`;
    throw new ApiError(502, `Telegram rejected the message: ${reason}`);
  }
  return body;
}

/**
 * Post every new or changed active signal once. Each message is recorded as soon as Telegram
 * accepts it, so a failure part-way does not repeat the messages that were already sent.
 */
export async function postSignalUpdates(env, { fetchImpl = fetch, now = Date.now() / 1000 } = {}) {
  if (!telegramConfigured(env)) return { posted: 0, skipped: "telegram is not configured" };
  const instructions = await freshInstructions(env, now);
  if (instructions === null) return { posted: 0, skipped: "no fresh live snapshot" };

  const previous = await loadState(env);
  const { toPost, next } = selectChanges(instructions, previous);
  const generatedAt = now;
  const state = { ...previous };
  // Forget signals that are no longer active, so a later signal for the same pair is posted again.
  for (const uid of Object.keys(state)) if (!(uid in next)) delete state[uid];

  let posted = 0;
  for (const item of toPost) {
    const uid = String(item.uid || item.symbol);
    await sendTelegramMessage(env, formatSignal(item, generatedAt), fetchImpl);
    state[uid] = next[uid];
    await saveState(env, state, now);
    posted += 1;
  }
  if (toPost.length === 0) await saveState(env, state, now);
  return { posted, active: Object.keys(next).length };
}
