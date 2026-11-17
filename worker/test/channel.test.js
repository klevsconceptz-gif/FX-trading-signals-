import test from "node:test";
import assert from "node:assert/strict";
import {
  channelStatus,
  createChannelInvite,
  enforceChannelAccess,
  handleChannelUpdate,
} from "../src/channel.js";
import { telegramWebhookAuthorized } from "../src/telegram.js";
import { ApiError } from "../src/http.js";

const NOW = 1_800_000_000;
const CHAT = "-1001234567890";
const LINK = "https://t.me/+ABCdef";
const member = { id: 7, role: "member", username: "trader_one" };

// Fake D1: the first rule whose SQL fragment matches answers the query.
function fakeDb(rules = {}) {
  const calls = [];
  const entries = Object.entries(rules);
  async function run(sql, args, kind) {
    calls.push({ sql, args, kind });
    for (const [fragment, answer] of entries) {
      if (sql.includes(fragment)) return typeof answer === "function" ? answer(args) : answer;
    }
    return kind === "all" ? [] : null;
  }
  return {
    calls,
    prepare(sql) {
      let args = [];
      const statement = {
        bind(...values) { args = values; return statement; },
        async first() { return run(sql, args, "first"); },
        async all() {
          const rows = await run(sql, args, "all");
          return { results: Array.isArray(rows) ? rows : [] };
        },
        async run() { return run(sql, args, "run"); },
      };
      return statement;
    },
  };
}

function fakeEnv(rules = {}) {
  return {
    DB: fakeDb(rules),
    TELEGRAM_BOT_TOKEN: "123456:TEST-TOKEN-DO-NOT-LOG",
    TELEGRAM_CHAT_ID: CHAT,
    TELEGRAM_WEBHOOK_SECRET: "hook-secret-value",
  };
}

// Fake Bot API: records each call and answers ok, unless a method is set to fail.
function fakeTelegram({ failing = [] } = {}) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    const method = url.split("/").pop();
    calls.push({ method, body: JSON.parse(init.body) });
    if (failing.includes(method)) {
      return new Response(JSON.stringify({ ok: false, description: "Bad Request: user not found" }), { status: 400 });
    }
    const result = method === "createChatInviteLink" ? { invite_link: LINK } : true;
    return new Response(JSON.stringify({ ok: true, result }), { status: 200 });
  };
  return { calls, fetchImpl };
}

const ACTIVE_PLAN = { "SELECT plan, expires_at FROM access_grants": { plan: "pro", expires_at: NOW + 3600 } };
const ACTIVE_GRANT_ROW = { "SELECT 1 AS ok FROM access_grants": { ok: 1 } };

function joinUpdate(status = "member", overrides = {}) {
  return {
    chat_member: {
      chat: { id: Number(CHAT) },
      new_chat_member: { status, user: { id: 555 } },
      invite_link: { invite_link: LINK },
      ...overrides,
    },
  };
}

test("the webhook secret must match the header; a missing secret is a 503", () => {
  const ok = new Request("https://w.example/api/internal/telegram/webhook", {
    headers: { "X-Telegram-Bot-Api-Secret-Token": "hook-secret-value" },
  });
  const wrong = new Request("https://w.example/api/internal/telegram/webhook", {
    headers: { "X-Telegram-Bot-Api-Secret-Token": "nope" },
  });
  const none = new Request("https://w.example/api/internal/telegram/webhook");
  const env = fakeEnv();
  assert.equal(telegramWebhookAuthorized(ok, env), true);
  assert.equal(telegramWebhookAuthorized(wrong, env), false);
  assert.equal(telegramWebhookAuthorized(none, env), false);
  assert.throws(() => telegramWebhookAuthorized(ok, { ...env, TELEGRAM_WEBHOOK_SECRET: "" }),
    (error) => error instanceof ApiError && error.status === 503);
});

test("a join through our link with an active plan is recorded as a member", async () => {
  const telegram = fakeTelegram();
  const env = fakeEnv({
    "SELECT id, user_id, used_at FROM channel_invites": { id: 1, user_id: 7, used_at: null },
    ...ACTIVE_GRANT_ROW,
  });
  const result = await handleChannelUpdate(env, joinUpdate(), telegram.fetchImpl, NOW);
  assert.deepEqual(result, { handled: true, action: "joined" });
  assert.ok(env.DB.calls.some((c) => c.sql.includes("INSERT INTO channel_members")));
  assert.equal(telegram.calls.length, 0);   // no ban for a paying member
});

test("a join without an active plan is removed at once and not recorded", async () => {
  const telegram = fakeTelegram();
  const env = fakeEnv({
    "SELECT id, user_id, used_at FROM channel_invites": { id: 1, user_id: 7, used_at: null },
  });
  const result = await handleChannelUpdate(env, joinUpdate(), telegram.fetchImpl, NOW);
  assert.deepEqual(result, { handled: true, action: "removed" });
  assert.deepEqual(telegram.calls.map((c) => c.method), ["banChatMember", "unbanChatMember"]);
  assert.equal(telegram.calls[0].body.user_id, 555);
  assert.ok(!env.DB.calls.some((c) => c.sql.includes("INSERT INTO channel_members")));
});

test("a join through a link we did not create is ignored", async () => {
  const telegram = fakeTelegram();
  const env = fakeEnv();
  assert.deepEqual(await handleChannelUpdate(env, joinUpdate(), telegram.fetchImpl, NOW), { handled: false });
  assert.equal(telegram.calls.length, 0);
});

test("updates for another chat are ignored", async () => {
  const env = fakeEnv();
  const update = joinUpdate("member", { chat: { id: -999 } });
  assert.deepEqual(await handleChannelUpdate(env, update, fakeTelegram().fetchImpl, NOW), { handled: false });
});

test("a departure is recorded so the member is not removed again", async () => {
  const env = fakeEnv();
  const result = await handleChannelUpdate(env, joinUpdate("left", {
    invite_link: undefined,
  }), fakeTelegram().fetchImpl, NOW);
  assert.deepEqual(result, { handled: true, action: "left" });
  assert.ok(env.DB.calls.some((c) => c.sql.includes("UPDATE channel_members SET removed_at")));
});

test("a member with an active plan gets one single-use link, then the same link again", async () => {
  const telegram = fakeTelegram();
  const env = fakeEnv(ACTIVE_PLAN);
  const first = await createChannelInvite(env, member, telegram.fetchImpl);
  assert.deepEqual(first, { invite_link: LINK, reused: false });
  const created = telegram.calls.find((c) => c.method === "createChatInviteLink");
  assert.equal(created.body.member_limit, 1);
  assert.equal(created.body.chat_id, CHAT);
  assert.equal(created.body.name, "u7");
  assert.ok(env.DB.calls.some((c) => c.sql.includes("INSERT INTO channel_invites")));

  const reusing = fakeEnv({
    ...ACTIVE_PLAN,
    "SELECT invite_link FROM channel_invites": { invite_link: LINK },
  });
  const telegramAgain = fakeTelegram();
  assert.deepEqual(await createChannelInvite(reusing, member, telegramAgain.fetchImpl), { invite_link: LINK, reused: true });
  assert.equal(telegramAgain.calls.length, 0);
});

test("no active plan means no invite link, and no Telegram call", async () => {
  const telegram = fakeTelegram();
  const env = fakeEnv();
  await assert.rejects(createChannelInvite(env, member, telegram.fetchImpl),
    (error) => error instanceof ApiError && error.status === 403);
  assert.equal(telegram.calls.length, 0);
});

test("channel status says whether the member is eligible, joined, and has a link", async () => {
  const env = fakeEnv({
    ...ACTIVE_PLAN,
    "SELECT removed_at FROM channel_members": { removed_at: null },
    "SELECT invite_link FROM channel_invites": { invite_link: LINK },
  });
  assert.deepEqual(await channelStatus(env, member), { eligible: true, joined: true, invite_link: LINK });
  const outsider = await channelStatus(fakeEnv(), member);
  assert.deepEqual(outsider, { eligible: false, joined: false, invite_link: null });
});

test("the cron revokes unused links and removes members whose access has ended", async () => {
  const telegram = fakeTelegram();
  const env = fakeEnv({
    "SELECT i.id, i.invite_link FROM channel_invites i": [{ id: 3, invite_link: "https://t.me/+old" }],
    "SELECT m.user_id, m.telegram_user_id FROM channel_members m": [{ user_id: 9, telegram_user_id: 777 }],
  });
  const result = await enforceChannelAccess(env, telegram.fetchImpl, NOW);
  assert.deepEqual(result, { revoked_invites: 1, removed_members: 1, failed: 0 });
  assert.deepEqual(telegram.calls.map((c) => c.method),
    ["revokeChatInviteLink", "banChatMember", "unbanChatMember"]);
  assert.equal(telegram.calls[1].body.user_id, 777);
});

test("a failed Telegram call is counted, and the rest of the run continues", async () => {
  const telegram = fakeTelegram({ failing: ["banChatMember"] });
  const env = fakeEnv({
    "SELECT m.user_id, m.telegram_user_id FROM channel_members m": [
      { user_id: 9, telegram_user_id: 777 },
      { user_id: 10, telegram_user_id: 778 },
    ],
  });
  const result = await enforceChannelAccess(env, telegram.fetchImpl, NOW);
  assert.equal(result.failed, 2);
  assert.equal(result.removed_members, 0);
  assert.ok(!JSON.stringify(result).includes("TEST-TOKEN"));
});
