import test from "node:test";
import assert from "node:assert/strict";
import { adminOverview } from "../src/overview.js";

// Fake D1: `answer(sql, args)` returns a row for first() and a list for all(), keyed by the SQL.
function fakeEnv(answer, extra = {}) {
  const db = {
    prepare(sql) {
      return {
        bind(...args) {
          return {
            first: async () => answer(sql, args).row ?? null,
            all: async () => ({ results: answer(sql, args).rows ?? [] }),
          };
        },
      };
    },
  };
  return { DB: db, ...extra };
}

const NOW = 1_800_000_000;

function answers({ state = null, audit = [] } = {}) {
  return (sql) => {
    if (/FROM users/.test(sql)) return { row: { total: 5, admins: 1, disabled: 1 } };
    if (/FROM access_grants/.test(sql)) return { row: { paid_users: 3, active_grants: 4 } };
    if (/FROM payments/.test(sql)) return { rows: [{ status: "submitted", n: 2 }, { status: "confirmed", n: 7 }] };
    if (/FROM telegram_state/.test(sql)) return { row: state };
    if (/FROM signal_cache/.test(sql)) return { row: null };
    if (/FROM admin_audit_events/.test(sql)) return { rows: audit };
    return {};
  };
}

test("summarises accounts, access, payments, Telegram and activity", async () => {
  const env = fakeEnv(answers({
    state: { value: JSON.stringify({ "EURUSD:BUY": { fp: "a" }, "GBPUSD:SELL": { fp: "b" } }), updated_at: NOW - 60 },
    audit: [{ event_id: 9, action: "payment_confirmed", actor: "ops" }],
  }), { TELEGRAM_BOT_TOKEN: "123:SECRET-TOKEN", TELEGRAM_CHAT_ID: "-1001234567890" });

  const out = await adminOverview(env, NOW);

  assert.equal(out.signals.state, "missing");
  assert.deepEqual(out.accounts, { total: 5, admins: 1, disabled: 1 });
  assert.deepEqual(out.access, { paid_users: 3, active_grants: 4 });
  assert.equal(out.payments.submitted, 2);
  assert.equal(out.payments.confirmed, 7);
  assert.equal(out.payments.awaiting_payment, 0);
  assert.deepEqual(out.telegram, { configured: true, last_check_at: NOW - 60, tracked_signals: 2 });
  assert.equal(out.recent_activity[0].action, "payment_confirmed");
});

test("never returns the Telegram token or chat ID", async () => {
  const env = fakeEnv(answers(), { TELEGRAM_BOT_TOKEN: "123:SECRET-TOKEN", TELEGRAM_CHAT_ID: "-1001234567890" });
  const text = JSON.stringify(await adminOverview(env, NOW));

  assert.ok(!text.includes("SECRET-TOKEN"));
  assert.ok(!text.includes("-1001234567890"));
});

test("reports Telegram as not configured and no state as zero signals", async () => {
  const out = await adminOverview(fakeEnv(answers()), NOW);

  assert.deepEqual(out.telegram, { configured: false, last_check_at: null, tracked_signals: 0 });
  assert.equal(out.recent_activity.length, 0);
});

test("a corrupt Telegram state row counts as zero signals rather than failing", async () => {
  const env = fakeEnv(answers({ state: { value: "not json", updated_at: NOW } }));
  const out = await adminOverview(env, NOW);

  assert.equal(out.telegram.tracked_signals, 0);
});
