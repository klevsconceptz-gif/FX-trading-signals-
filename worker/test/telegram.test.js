import test from "node:test";
import assert from "node:assert/strict";
import {
  fingerprint,
  formatSignal,
  postSignalUpdates,
  selectChanges,
  telegramRequestAuthorized,
  DISCLAIMER,
} from "../src/telegram.js";
import { ApiError } from "../src/http.js";

const NOW = 1_800_000_000;
const TOKEN = "123456:TEST-TOKEN-DO-NOT-LOG";

function item(overrides = {}) {
  return {
    uid: "EURUSD",
    symbol: "EURUSD",
    display: "EUR/USD",
    verdict: "BUY NOW",
    action: "buy",
    confidence: 0.62,
    entry: 1.1,
    p_target_first: 0.58,
    expected_bars: 12,
    one_line: "BUY EUR/USD NOW @ 1.1 → predicted +1.8% to 1.12",
    exit_orders: [{ label: "take profit", price: 1.12 }],
    ...overrides,
  };
}

// Fake D1: answers the signal_cache reads and stores telegram_state in memory.
function fakeEnv({ scan, instructions, state = null, data = {} } = {}) {
  const store = { state };
  const rows = {
    scan: scan === undefined ? { key: "scan", run_id: 5, generated_at: NOW - 60, data_source: "live (yfinance, 1h)", payload: "{}" } : scan,
    instructions: instructions === undefined
      ? { key: "instructions", run_id: 5, generated_at: NOW - 60, data_source: "live", payload: JSON.stringify({ instructions: [item()] }) }
      : instructions,
  };
  const DB = {
    prepare(sql) {
      return {
        bind(...args) {
          return {
            first: async () => {
              if (sql.includes("FROM signal_cache")) return rows[args[0]] || null;
              if (sql.includes("FROM telegram_state")) return store.state === null ? null : { value: store.state };
              return null;
            },
            run: async () => {
              if (sql.includes("INSERT OR REPLACE INTO telegram_state")) store.state = args[1];
              return {};
            },
          };
        },
      };
    },
  };
  return {
    env: {
      DB,
      TELEGRAM_BOT_TOKEN: TOKEN,
      TELEGRAM_CHAT_ID: "-1001234567890",
      TELEGRAM_POST_SECRET: "post-secret-value",
      ...data,
    },
    store,
  };
}

function recordingFetch(options = {}) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, body: JSON.parse(init.body) });
    if (options.fail) return new Response(JSON.stringify({ ok: false, description: "Forbidden: bot was kicked" }), { status: 403 });
    return new Response(JSON.stringify({ ok: true, result: { message_id: calls.length } }), { status: 200 });
  };
  return { calls, fetchImpl };
}

test("selectChanges posts new signals once and ignores waiting verdicts", () => {
  const instructions = [item(), item({ uid: "GBPUSD", symbol: "GBPUSD", verdict: "DO NOT BUY", action: "wait" })];
  const first = selectChanges(instructions, {});
  assert.deepEqual(first.toPost.map((i) => i.uid), ["EURUSD"]);
  assert.deepEqual(Object.keys(first.next), ["EURUSD"]);

  const again = selectChanges(instructions, first.next);
  assert.equal(again.toPost.length, 0);
});

test("a changed entry or exit is posted again; a removed signal is forgotten", () => {
  const before = selectChanges([item()], {});
  const changed = selectChanges([item({ entry: 1.105 })], before.next);
  assert.equal(changed.toPost.length, 1);
  const gone = selectChanges([], before.next);
  assert.deepEqual(gone.next, {});
  assert.notEqual(fingerprint(item()), fingerprint(item({ entry: 1.105 })));
});

test("postSignalUpdates sends one message and does not repeat it on the next run", async () => {
  const { env, store } = fakeEnv();
  const first = recordingFetch();
  const r1 = await postSignalUpdates(env, { fetchImpl: first.fetchImpl, now: NOW });
  assert.equal(r1.posted, 1);
  assert.equal(first.calls.length, 1);
  assert.match(first.calls[0].url, /^https:\/\/api\.telegram\.org\/bot.+\/sendMessage$/);
  assert.equal(first.calls[0].body.chat_id, "-1001234567890");
  assert.match(first.calls[0].body.text, /BUY NOW EUR\/USD/);
  assert.ok(first.calls[0].body.text.includes(DISCLAIMER));
  assert.ok(!first.calls[0].body.text.includes(TOKEN), "the bot token must never be in a message");

  const second = recordingFetch();
  const r2 = await postSignalUpdates(env, { fetchImpl: second.fetchImpl, now: NOW + 3600 });
  assert.equal(r2.posted, 0);
  assert.equal(second.calls.length, 0);
  assert.ok(store.state, "state is stored");
});

test("nothing is posted without configuration, from a stale snapshot, or from non-live data", async () => {
  const off = recordingFetch();
  const unconfigured = await postSignalUpdates({ DB: fakeEnv().env.DB }, { fetchImpl: off.fetchImpl, now: NOW });
  assert.equal(unconfigured.posted, 0);
  assert.equal(off.calls.length, 0);

  const stale = recordingFetch();
  const { env: staleEnv } = fakeEnv({ scan: { key: "scan", run_id: 5, generated_at: NOW - 7 * 3600, data_source: "live", payload: "{}" } });
  const staleResult = await postSignalUpdates(staleEnv, { fetchImpl: stale.fetchImpl, now: NOW });
  assert.equal(staleResult.posted, 0);
  assert.equal(stale.calls.length, 0);

  const synthetic = recordingFetch();
  const { env: syntheticEnv } = fakeEnv({ scan: { key: "scan", run_id: 5, generated_at: NOW - 60, data_source: "synthetic (demo)", payload: "{}" } });
  const syntheticResult = await postSignalUpdates(syntheticEnv, { fetchImpl: synthetic.fetchImpl, now: NOW });
  assert.equal(syntheticResult.posted, 0);
  assert.equal(synthetic.calls.length, 0);
});

test("a rejected message raises a 502 without the token and is not recorded as posted", async () => {
  const { env, store } = fakeEnv();
  const failing = recordingFetch({ fail: true });
  await assert.rejects(postSignalUpdates(env, { fetchImpl: failing.fetchImpl, now: NOW }), (error) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.status, 502);
    assert.match(error.message, /bot was kicked/);
    assert.ok(!error.message.includes(TOKEN));
    return true;
  });
  assert.equal(store.state, null, "a failed send must not be recorded, so it is retried");
});

test("the internal endpoint accepts only the shared bearer secret", () => {
  const { env } = fakeEnv();
  const ok = new Request("https://w.example/api/internal/telegram/post", { headers: { Authorization: "Bearer post-secret-value" } });
  const wrong = new Request("https://w.example/api/internal/telegram/post", { headers: { Authorization: "Bearer nope" } });
  const none = new Request("https://w.example/api/internal/telegram/post");
  assert.equal(telegramRequestAuthorized(ok, env), true);
  assert.equal(telegramRequestAuthorized(wrong, env), false);
  assert.equal(telegramRequestAuthorized(none, env), false);
  assert.throws(() => telegramRequestAuthorized(ok, { DB: env.DB }), (error) => error.status === 503);
});

test("messages stay under Telegram's length limit and name the snapshot time", () => {
  const long = formatSignal(item({ one_line: "x".repeat(10_000) }), NOW);
  assert.ok(long.length <= 4000);
  assert.match(formatSignal(item(), NOW), /sent 20\d\d-\d\d-\d\d \d\d:\d\d UTC/);
});
