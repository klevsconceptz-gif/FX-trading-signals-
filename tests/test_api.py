"""API smoke tests via FastAPI TestClient (offline, synthetic data)."""

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from fxbot.server import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
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
        for key in ("side", "entry", "stop", "target", "risk_reward", "confidence"):
            assert key in s, key


def test_pair_detail(client):
    r = client.get("/api/pair/EURUSD")
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "EURUSD"
    assert len(body["candles"]) > 100
    assert len(body["overlays"]["ema20"]) == len(body["candles"])
    assert len(body["rsi"]) == len(body["candles"])
    assert body["plan"]["side"] in ("BUY", "SELL", "WAIT")


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


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "FX Trading Signals" in r.text
