/**
 * Private Telegram channel access for paying members.
 *
 * A member with an active access grant gets a single-use invite link (created by the bot). When
 * Telegram reports the join (chat_member update on the webhook), the Telegram user is recorded
 * against the account. When the grant ends, the cron removes the member and revokes any unused
 * link. Sign-in is unchanged: members still log in with username and password.
 */

import { ApiError, requireDatabase } from "./http.js";
import { accessStatus } from "./auth.js";

const API_BASE = "https://api.telegram.org";

function nowSeconds() {
  return Date.now() / 1000;
}

/** One Bot API call. Errors carry Telegram's description, never the bot token. */
export async function callBot(env, method, payload, fetchImpl = fetch) {
  if (!env?.TELEGRAM_BOT_TOKEN || !env?.TELEGRAM_CHAT_ID) {
    throw new ApiError(503, "Telegram is not configured on the Worker");
  }
  const response = await fetchImpl(`${API_BASE}/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok || body.ok !== true) {
    const reason = typeof body.description === "string" ? body.description : `HTTP ${response.status}`;
    throw new ApiError(502, `Telegram rejected ${method}: ${reason}`);
  }
  return body.result;
}

const ACTIVE_GRANT_EXISTS = `EXISTS (
  SELECT 1 FROM access_grants g
   WHERE g.user_id = {col} AND g.status = 'active' AND g.expires_at > ?
)`;

/** Status for the member's account page. */
export async function channelStatus(env, user) {
  const db = requireDatabase(env);
  const access = await accessStatus(env, user);
  const member = await db.prepare("SELECT removed_at FROM channel_members WHERE user_id = ?")
    .bind(user.id).first();
  const invite = await db.prepare(`
    SELECT invite_link FROM channel_invites
     WHERE user_id = ? AND revoked_at IS NULL AND used_at IS NULL
     ORDER BY id DESC LIMIT 1
  `).bind(user.id).first();
  return {
    eligible: access.access === "subscriber",
    joined: Boolean(member) && member.removed_at == null,
    invite_link: invite?.invite_link || null,
  };
}

/** Create (or reuse) this member's single-use invite link. Only members with an active plan get one. */
export async function createChannelInvite(env, user, fetchImpl = fetch) {
  const status = await channelStatus(env, user);
  if (!status.eligible) throw new ApiError(403, "Channel access needs an active plan");
  if (status.joined) throw new ApiError(409, "You are already in the channel");
  if (status.invite_link) return { invite_link: status.invite_link, reused: true };

  const created = await callBot(env, "createChatInviteLink", {
    chat_id: env.TELEGRAM_CHAT_ID,
    name: `u${user.id}`,
    member_limit: 1,
  }, fetchImpl);
  await requireDatabase(env).prepare(
    "INSERT INTO channel_invites (user_id, invite_link, created_at) VALUES (?, ?, ?)",
  ).bind(user.id, created.invite_link, nowSeconds()).run();
  return { invite_link: created.invite_link, reused: false };
}

/** Ban then unban: the member is out now, and can rejoin later with a new paid link. */
async function removeFromChannel(env, telegramUserId, fetchImpl) {
  await callBot(env, "banChatMember", {
    chat_id: env.TELEGRAM_CHAT_ID, user_id: telegramUserId, revoke_messages: false,
  }, fetchImpl);
  await callBot(env, "unbanChatMember", {
    chat_id: env.TELEGRAM_CHAT_ID, user_id: telegramUserId, only_if_banned: true,
  }, fetchImpl);
}

/**
 * Telegram webhook: chat_member updates for the channel.
 * A join through one of our links is recorded; a join without an active grant is removed at once.
 * A departure is recorded so the member is not removed again.
 */
export async function handleChannelUpdate(env, update, fetchImpl = fetch, now = nowSeconds()) {
  const change = update?.chat_member;
  if (!change || String(change.chat?.id) !== String(env.TELEGRAM_CHAT_ID)) return { handled: false };
  const db = requireDatabase(env);
  const status = change.new_chat_member?.status;
  const telegramUserId = change.new_chat_member?.user?.id;
  if (typeof telegramUserId !== "number") return { handled: false };

  if (status === "member") {
    const link = change.invite_link?.invite_link;
    if (!link) return { handled: false };
    const invite = await db.prepare(
      "SELECT id, user_id, used_at FROM channel_invites WHERE invite_link = ? AND revoked_at IS NULL",
    ).bind(link).first();
    if (!invite || invite.used_at) return { handled: false };
    await db.prepare("UPDATE channel_invites SET used_at = ?, telegram_user_id = ? WHERE id = ?")
      .bind(now, telegramUserId, invite.id).run();

    const active = await db.prepare(
      "SELECT 1 AS ok FROM access_grants WHERE user_id = ? AND status = 'active' AND expires_at > ?",
    ).bind(invite.user_id, now).first();
    if (!active) {
      await removeFromChannel(env, telegramUserId, fetchImpl);
      return { handled: true, action: "removed" };
    }
    await db.prepare(`
      INSERT INTO channel_members (user_id, telegram_user_id, joined_at, removed_at)
      VALUES (?, ?, ?, NULL)
      ON CONFLICT(user_id) DO UPDATE SET
        telegram_user_id = excluded.telegram_user_id, joined_at = excluded.joined_at, removed_at = NULL
    `).bind(invite.user_id, telegramUserId, now).run();
    return { handled: true, action: "joined" };
  }

  if (status === "left" || status === "kicked") {
    await db.prepare(
      "UPDATE channel_members SET removed_at = ? WHERE telegram_user_id = ? AND removed_at IS NULL",
    ).bind(now, telegramUserId).run();
    return { handled: true, action: "left" };
  }
  return { handled: false };
}

/**
 * Cron: revoke unused links, and remove members, for accounts whose access has ended.
 * Returns counts only; a failed Telegram call is counted and retried on the next run.
 */
export async function enforceChannelAccess(env, fetchImpl = fetch, now = nowSeconds()) {
  const db = requireDatabase(env);
  const result = { revoked_invites: 0, removed_members: 0, failed: 0 };

  const staleInvites = await db.prepare(`
    SELECT i.id, i.invite_link FROM channel_invites i
     WHERE i.revoked_at IS NULL AND i.used_at IS NULL
       AND NOT ${ACTIVE_GRANT_EXISTS.replace("{col}", "i.user_id")}
  `).bind(now).all();
  for (const row of staleInvites.results || []) {
    try {
      await callBot(env, "revokeChatInviteLink", {
        chat_id: env.TELEGRAM_CHAT_ID, invite_link: row.invite_link,
      }, fetchImpl);
      await db.prepare("UPDATE channel_invites SET revoked_at = ? WHERE id = ?").bind(now, row.id).run();
      result.revoked_invites += 1;
    } catch (error) {
      result.failed += 1;
      console.error("channel invite revoke failed", error?.name || "Error", row.id);
    }
  }

  const expired = await db.prepare(`
    SELECT m.user_id, m.telegram_user_id FROM channel_members m
     WHERE m.removed_at IS NULL
       AND NOT ${ACTIVE_GRANT_EXISTS.replace("{col}", "m.user_id")}
  `).bind(now).all();
  for (const row of expired.results || []) {
    try {
      await removeFromChannel(env, row.telegram_user_id, fetchImpl);
      await db.prepare("UPDATE channel_members SET removed_at = ? WHERE user_id = ?").bind(now, row.user_id).run();
      result.removed_members += 1;
    } catch (error) {
      result.failed += 1;
      console.error("channel member removal failed", error?.name || "Error", row.user_id);
    }
  }
  return result;
}
