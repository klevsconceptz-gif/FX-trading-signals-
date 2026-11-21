// Project overview for the admin console's General tab. It reads counts and status only.
// It never returns a secret value: Telegram is reported as configured or not, nothing more.
import { requireDatabase } from "./http.js";
import { listAdminAudit } from "./billing.js";
import { signalHealth } from "./signals.js";
import { telegramConfigured } from "./telegram.js";

const PAYMENT_STATUSES = ["awaiting_payment", "submitted", "confirmed", "rejected", "cancelled"];
const TELEGRAM_STATE_KEY = "posted";

function count(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

export async function adminOverview(env, now = Date.now() / 1000) {
  const db = requireDatabase(env);

  const signals = await signalHealth(env, now);

  const users = (await db.prepare(`
    SELECT COUNT(*) AS total,
           SUM(CASE WHEN role = 'admin' THEN 1 ELSE 0 END) AS admins,
           SUM(CASE WHEN disabled_at > 0 THEN 1 ELSE 0 END) AS disabled
      FROM users
  `).bind().first()) || {};

  const access = (await db.prepare(`
    SELECT COUNT(DISTINCT user_id) AS paid_users, COUNT(*) AS active_grants
      FROM access_grants
     WHERE status = 'active' AND expires_at > ?
  `).bind(Math.floor(now)).first()) || {};

  const statusRows = (await db.prepare("SELECT status, COUNT(*) AS n FROM payments GROUP BY status").bind().all())
    .results || [];
  const payments = Object.fromEntries(PAYMENT_STATUSES.map((status) => [status, 0]));
  for (const row of statusRows) {
    if (row.status in payments) payments[row.status] = count(row.n);
  }

  const stateRow = await db.prepare("SELECT value, updated_at FROM telegram_state WHERE key = ?")
    .bind(TELEGRAM_STATE_KEY)
    .first();
  let trackedSignals = 0;
  if (stateRow) {
    try {
      trackedSignals = Object.keys(JSON.parse(stateRow.value) || {}).length;
    } catch {
      trackedSignals = 0;
    }
  }

  const audit = await listAdminAudit(env, 5);

  return {
    generated_at: Math.floor(now),
    signals,
    telegram: {
      configured: telegramConfigured(env),
      last_check_at: stateRow ? Number(stateRow.updated_at) : null,
      tracked_signals: trackedSignals,
    },
    accounts: {
      total: count(users.total),
      admins: count(users.admins),
      disabled: count(users.disabled),
    },
    access: {
      paid_users: count(access.paid_users),
      active_grants: count(access.active_grants),
    },
    payments,
    recent_activity: audit.events,
  };
}
