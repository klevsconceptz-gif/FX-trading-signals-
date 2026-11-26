-- Each registered user's chosen lot size for the signals they receive. NULL / no row means
-- "use the signal size" (the risk-based size the publisher calculated). Read and written only by
-- the account owner (GET and PUT /api/me/trade-settings). The published signals are shared and
-- never change per user; each user's own risk is calculated in the browser from the per-lot figures.
CREATE TABLE IF NOT EXISTS user_trade_settings (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    lot_size REAL NOT NULL,
    updated_at REAL NOT NULL
);
