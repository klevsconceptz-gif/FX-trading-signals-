// Admin audit events. Every operator change builds its audit row with auditStatement and runs it
// in the same D1 batch as the change, so a change never happens without its record.
import { randomToken } from "./security.js";

function nowSeconds() {
  return Date.now() / 1000;
}

export function auditStatement(db, { operator, action, target, summary, note = null, paymentId = null, grantId = null, reference = null }) {
  return db.prepare(`
    INSERT INTO admin_audit_events
      (event_key, occurred_at, actor_username, action, target_user_id, target_username,
       payment_id, grant_id, reference, summary, note)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).bind(
    `${action}:${randomToken(12)}`,
    nowSeconds(),
    operator,
    action,
    target ? Number(target.id) : null,
    target ? target.username : null,
    paymentId,
    grantId,
    reference,
    summary,
    note,
  );
}
