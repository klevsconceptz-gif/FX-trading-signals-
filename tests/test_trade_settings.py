"""Each registered user's lot size: the migration, the endpoints, and the page wiring. Offline."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_migration_stores_one_lot_size_per_user_and_cascades_on_delete():
    sql = _read("worker/migrations/0008_user_trade_settings.sql")
    assert "CREATE TABLE IF NOT EXISTS user_trade_settings" in sql
    assert "user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE" in sql
    assert "lot_size REAL NOT NULL" in sql


def test_worker_exposes_get_and_put_for_logged_in_users_only():
    index = _read("worker/src/index.js")
    assert 'path === "/api/me/trade-settings" && method === "GET"' in index
    assert 'path === "/api/me/trade-settings" && method === "PUT"' in index
    assert "authenticated(env, request)" in index.split("/api/me/trade-settings")[1][:400]


def test_worker_limits_lot_sizes_to_the_broker_grid():
    module = _read("worker/src/tradeSettings.js")
    assert "MIN_LOT_SIZE = 0.01" in module and "MAX_LOT_SIZE = 50" in module
    assert "multiple of 0.01" in module


def test_page_has_the_lot_control_and_uses_the_per_lot_figures():
    html = _read("static/index.html")
    assert 'id="lot-input"' in html and 'id="lot-save"' in html and 'id="lot-reset"' in html
    app = _read("static/app.js")
    assert '"/api/me/trade-settings"' in app
    assert "ins.risk_per_lot" in app and "ins.reward_per_lot" in app
    assert "TRADE = { lotSize: null, loaded: false }" in app   # cleared on logout


def test_published_instructions_carry_the_per_lot_fields_the_page_reads():
    engine = _read("fxbot/engine.py")
    for key in ('"lots"', '"risk_amount"', '"risk_per_lot"', '"reward_per_lot"', '"lot_units"'):
        assert key in engine
