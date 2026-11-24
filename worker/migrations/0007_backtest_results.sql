-- Stored backtest results for the Backtest Lab. One row per symbol: the latest run wins.
-- Written only by the backtest job (scripts/publish_backtests.py, GitHub Actions) from live market
-- data. Served to paid members only (GET /api/backtest). `payload` is the JSON the Lab reads.
CREATE TABLE IF NOT EXISTS backtest_results (
    symbol TEXT PRIMARY KEY,
    run_at TEXT NOT NULL,
    data_source TEXT NOT NULL,
    payload TEXT NOT NULL,
    stored_at REAL NOT NULL
);
