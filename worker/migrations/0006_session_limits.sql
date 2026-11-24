-- Session limits: a session ends 15 minutes after its last activity, and at most 1 day after sign-in.
-- last_seen_at is the time of the most recent request (written at most once a minute).
-- Rows created before this migration have no last_seen_at, so they count from created_at.
ALTER TABLE user_sessions ADD COLUMN last_seen_at REAL;

-- Cap sessions that were issued with the old 14-day lifetime, so the expiry sweep removes them within a day.
UPDATE user_sessions SET expires_at = MIN(expires_at, created_at + 86400);
