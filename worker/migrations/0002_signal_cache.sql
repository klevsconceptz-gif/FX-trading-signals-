-- Signal snapshots published by the scheduled job (scripts/publish_signals.py).
-- One row per key: 'scan', 'instructions', and 'pair:<SYMBOL>'. Every row of one publish shares run_id.
CREATE TABLE IF NOT EXISTS signal_cache (
    key TEXT PRIMARY KEY,
    run_id INTEGER NOT NULL,
    generated_at REAL NOT NULL,
    data_source TEXT NOT NULL,
    payload TEXT NOT NULL
);
