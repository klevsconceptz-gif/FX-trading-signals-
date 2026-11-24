-- One-time bulk grant: one month (30 days) of Pro access for every existing customer account.
-- The admin account (stored role 'admin') is left out: it has unrestricted access already.
--
-- Same rule as the admin "Grant access" button: the 30 days are added after any access that is
-- still running. Each grant has a fixed key per account, so running this file again adds nothing.
-- Every grant gets an audit row.
--
-- Scope: only the accounts that exist when this runs. Sign-up never creates a grant, so an account
-- registered later gets access only when an admin grants it (Accounts tab, or Access grants tab).
--
-- Run it only through the "Grant one month to existing accounts" workflow (manual, on main).

INSERT OR IGNORE INTO access_grants
  (grant_key, user_id, plan, status, starts_at, expires_at, granted_by, payment_id, note)
SELECT 'bulk-one-month-v1:' || u.id,
       u.id,
       'pro',
       'active',
       MAX(CAST(strftime('%s', 'now') AS INTEGER),
           COALESCE((SELECT MAX(expires_at) FROM access_grants
                      WHERE user_id = u.id AND status = 'active'
                        AND expires_at > CAST(strftime('%s', 'now') AS INTEGER)), 0)),
       MAX(CAST(strftime('%s', 'now') AS INTEGER),
           COALESCE((SELECT MAX(expires_at) FROM access_grants
                      WHERE user_id = u.id AND status = 'active'
                        AND expires_at > CAST(strftime('%s', 'now') AS INTEGER)), 0)) + 2592000,
       'operator',
       NULL,
       'One month of access for existing accounts (bulk grant)'
  FROM users u
 WHERE u.role <> 'admin';

INSERT OR IGNORE INTO admin_audit_events
  (event_key, occurred_at, actor_username, action, target_user_id, target_username, grant_id, reference, summary, note)
SELECT 'bulk-grant:' || g.grant_key,
       CAST(strftime('%s', 'now') AS INTEGER),
       'operator',
       'access_granted',
       u.id,
       u.username,
       g.id,
       'grant #' || g.id,
       'Granted Pro access for 30 days (bulk, existing accounts)',
       g.note
  FROM access_grants g JOIN users u ON u.id = g.user_id
 WHERE g.grant_key LIKE 'bulk-one-month-v1:%';
