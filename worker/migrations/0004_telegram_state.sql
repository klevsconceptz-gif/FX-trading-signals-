-- Dedupe state for the Telegram channel poster: the last posted fingerprint per signal.
CREATE TABLE IF NOT EXISTS telegram_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);
