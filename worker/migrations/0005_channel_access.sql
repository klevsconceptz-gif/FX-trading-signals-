-- Private Telegram channel access for paid members.
-- channel_invites: one single-use invite link per request; used_at is set when Telegram reports the join.
-- channel_members: the Telegram user behind each joined member; removed_at is set when the member
-- is removed (access ended) or leaves on their own.
CREATE TABLE IF NOT EXISTS channel_invites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    invite_link TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    used_at REAL,
    telegram_user_id INTEGER,
    revoked_at REAL
);

CREATE INDEX IF NOT EXISTS idx_channel_invites_user ON channel_invites(user_id, revoked_at, used_at);

CREATE TABLE IF NOT EXISTS channel_members (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    telegram_user_id INTEGER NOT NULL,
    joined_at REAL NOT NULL,
    removed_at REAL
);

CREATE INDEX IF NOT EXISTS idx_channel_members_telegram ON channel_members(telegram_user_id, removed_at);
