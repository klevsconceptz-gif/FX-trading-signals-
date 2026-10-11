"""API smoke tests via FastAPI TestClient (offline, synthetic data)."""

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from fxbot.server import app


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["app"] == "klevs FX trading App"
    assert body["universe_size"] > 20


def test_universe(client):
    r = client.get("/api/universe")
    assert r.status_code == 200
    syms = {i["symbol"] for i in r.json()}
    assert {"EURUSD", "GBPJPY", "XAUUSD"} <= syms


def test_scan_report_shape(client):
    r = client.get("/api/scan")
    assert r.status_code == 200
    body = r.json()
    for key in ("signals", "watchlist", "insights", "currency_strength",
                "correlation", "summary", "risk", "ml"):
        assert key in body, key
    assert body["summary"]["pairs_scanned"] > 20
    assert len(body["currency_strength"]) > 8
    assert len(body["insights"]) >= 3
    if body["signals"]:
        s = body["signals"][0]
        for key in ("side", "entry", "stop", "target", "risk_reward", "confidence",
                    "actions", "predicted_outcome", "p_target_first", "time_stop_bars"):
            assert key in s, key


def test_instructions_endpoint(client):
    """The instruction stream must tell when to buy, predict outcome, say when to sell."""
    r = client.get("/api/instructions")
    assert r.status_code == 200
    body = r.json()
    assert "instructions" in body and isinstance(body["instructions"], list)
    assert body["instructions"], "expected at least one instruction"
    ins = body["instructions"][0]
    for key in ("verdict", "action", "one_line", "prediction", "exit_orders"):
        assert key in ins, key
    assert ins["verdict"] in ("BUY NOW", "SELL NOW", "DO NOT BUY")
    if ins["action"] in ("buy", "sell"):
        blob = (ins["one_line"] + " " + ins["prediction"]).upper()
        assert "BUY" in blob and "SELL" in blob
        labels = {o["label"] for o in ins["exit_orders"]}
        assert {"take profit", "stop loss", "time stop"} <= labels
        assert ins["p_target_first"] > 0


def test_pair_detail(client):
    r = client.get("/api/pair/EURUSD")
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "EURUSD"
    assert len(body["candles"]) > 100
    assert len(body["overlays"]["ema20"]) == len(body["candles"])
    assert len(body["rsi"]) == len(body["candles"])
    assert body["plan"]["side"] in ("BUY", "SELL", "WAIT")
    assert body["plan"]["actions"], "plan must carry explicit instructions"


def test_pair_unknown_404(client):
    assert client.get("/api/pair/NOPE").status_code == 404


def test_backtest_endpoint(client):
    r = client.post("/api/backtest",
                    json={"symbol": "EURUSD", "equity": 10000, "bars": 1500})
    assert r.status_code == 200
    body = r.json()
    assert "metrics" in body and "equity_curve" in body
    assert body["equity_curve"]["v"]
    if body["trades"]:
        t = body["trades"][0]
        assert {"side", "entry", "exit", "r_multiple", "pnl"} <= set(t)


def test_landing_page_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "klevs FX trading App" in r.text
    # monthly-subscription landing: pricing + subscribe CTA must exist
    assert "Subscribe monthly" in r.text or "Subscribe" in r.text
    assert "/month" in r.text


def test_app_page_served(client):
    r = client.get("/app")
    assert r.status_code == 200
    assert "Live Instructions" in r.text          # the bot dashboard
    assert "paywall" in r.text                    # subscriber gate present


def test_subscription_flow(client):
    """subscribe -> token works -> login re-issues -> unknown email 404."""
    r = client.post("/api/subscribe", json={
        "name": "Test Trader", "email": "trader@example.com", "plan": "pro"})
    assert r.status_code == 200
    body = r.json()
    token = body["token"]
    assert body["subscription"]["plan"] == "pro"
    assert body["subscription"]["price_usd"] == 79
    assert body["subscription"]["interval"] == "month"
    assert body["access_url"] == "/app"

    r2 = client.get(f"/api/subscription/{token}")
    assert r2.status_code == 200
    assert r2.json()["subscription"]["email"] == "trader@example.com"

    r3 = client.post("/api/login", json={"email": "trader@example.com"})
    assert r3.status_code == 200
    new_token = r3.json()["token"]
    assert new_token != token
    assert client.get(f"/api/subscription/{new_token}").status_code == 200
    # old token invalidated after re-login
    assert client.get(f"/api/subscription/{token}").status_code == 404

    assert client.post("/api/login", json={"email": "nobody@example.com"}).status_code == 404


def test_subscription_validation(client):
    assert client.post("/api/subscribe",
                       json={"name": "X", "email": "not-an-email", "plan": "pro"}
                       ).status_code == 422
    assert client.post("/api/subscribe",
                       json={"name": "X", "email": "a@b.co", "plan": "gold"}
                       ).status_code == 422
    assert client.get("/api/subscription/made-up-token").status_code == 404
