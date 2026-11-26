"""Gold and silver stay in every live snapshot. When Yahoo drops one, it is retried, then published
as unavailable, so the page says so instead of silently omitting it. Offline.
"""

from types import SimpleNamespace

import pytest

from fxbot import publish
from fxbot.config import AppConfig
from fxbot.data import ProviderError, SyntheticMarket, YFinanceProvider
from fxbot.engine import MarketEngine
from fxbot.publish import PublishError, build_sql, collect
from fxbot.universe import UNIVERSE


def _frame(ins, n=1500):
    """A provider-shaped hourly frame (naive timestamps, like yfinance returns)."""
    frame = SyntheticMarket(AppConfig().data).ohlc(ins, n).copy()
    frame.index = frame.index.tz_localize(None)
    return frame


def _engine(monkeypatch, fail_metals: bool) -> MarketEngine:
    monkeypatch.setattr(YFinanceProvider, "unavailable_reason", lambda self: None)

    def fetch(self, ins, cfg):
        if fail_metals and ins.kind == "metal":
            raise ProviderError("yfinance: empty response")
        return _frame(ins)

    monkeypatch.setattr(YFinanceProvider, "fetch", fetch)
    engine = MarketEngine(AppConfig(), prefer_live=True)
    engine.forecaster.run = lambda _history: {}   # keep the test fast; the engine is otherwise real
    return engine


def test_missing_metals_are_published_as_unavailable_not_dropped(monkeypatch):
    engine = _engine(monkeypatch, fail_metals=True)
    report, pairs = collect(engine, retry_attempts=2, retry_seconds=0)   # no PublishError: source is live

    assert report["data_source"].startswith("live (")
    assert "excluded: XAUUSD" in report["data_source"]
    fx = {ins.symbol for ins in UNIVERSE if ins.kind == "fx"}
    assert {s for s, p in pairs.items() if p.get("status") != "unavailable"} == fx
    for metal in ("XAUUSD", "XAGUSD"):
        assert pairs[metal]["status"] == "unavailable"
        assert pairs[metal]["reason"]
    assert {item["symbol"] for item in report["unavailable"]} == {"XAUUSD", "XAGUSD"}
    verdicts = {i["symbol"]: i["verdict"] for i in report["instructions"] if i["symbol"] in ("XAUUSD", "XAGUSD")}
    assert verdicts == {"XAUUSD": "UNAVAILABLE", "XAGUSD": "UNAVAILABLE"}

    sql = build_sql(report, pairs, run_id=1)
    assert "'pair:XAUUSD'" in sql and "'pair:EURUSD'" in sql
    assert "excluded: XAUUSD" in sql                # the label reaches the D1 row


def test_full_live_snapshot_still_includes_gold(monkeypatch):
    engine = _engine(monkeypatch, fail_metals=False)
    report, pairs = collect(engine, retry_attempts=1, retry_seconds=0)
    assert "excluded" not in report["data_source"]
    assert "XAUUSD" in pairs and "XAGUSD" in pairs
    assert pairs["XAUUSD"].get("status") != "unavailable"
    assert report["unavailable"] == []


# -- retries with a scripted engine ------------------------------------------------

class ScriptedEngine:
    """Each scan returns the next scripted outcome: (source, excluded notes, symbols with data)."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.scans = 0
        self.data = SimpleNamespace(excluded=[])
        self._have = set()

    def scan(self, reload=False):
        source, excluded, have = self.outcomes[min(self.scans, len(self.outcomes) - 1)]
        self.scans += 1
        self.data.excluded = list(excluded)
        self._have = set(have)
        report = {"generated_at": 1_800_000_000.0, "data_source": source, "instructions": [], "signals": []}
        return SimpleNamespace(to_dict=lambda: report)

    def pair_report(self, symbol):
        if symbol not in self._have:
            return None
        return {"symbol": symbol, "candles": [], "rsi": []}


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch):
    monkeypatch.setattr(publish, "UNAVAILABLE_RETRY_SECONDS", 0.0)


ALL = [ins.symbol for ins in UNIVERSE]
LIVE = "live (yfinance, 1h, 32 symbols)"


def test_gold_is_retried_and_published_live_when_it_recovers(monkeypatch):
    engine = ScriptedEngine([
        ("live (yfinance, 1h, 30 symbols, excluded: XAUUSD (yfinance: empty response))", ["XAUUSD (yfinance: empty response)"],
         [s for s in ALL if s != "XAUUSD"]),
        (LIVE, [], ALL),
    ])
    report, pairs = collect(engine, retry_attempts=3, retry_seconds=0)
    assert engine.scans == 2
    assert pairs["XAUUSD"].get("status") != "unavailable"
    assert report["unavailable"] == []


def test_synthetic_scan_is_retried_and_then_live_snapshot_is_published(monkeypatch):
    engine = ScriptedEngine([
        ("synthetic (live unavailable: EURUSD unavailable — yfinance: empty response)", [], ALL),
        (LIVE, [], ALL),
    ])
    report, _ = collect(engine, retry_attempts=3, retry_seconds=0)
    assert engine.scans == 2
    assert report["data_source"] == LIVE


def test_every_attempt_synthetic_is_still_refused(monkeypatch):
    engine = ScriptedEngine([("synthetic (correlated factor model)", [], ALL)])
    with pytest.raises(PublishError, match="not live"):
        collect(engine, retry_attempts=3, retry_seconds=0)
    assert engine.scans == 3


def test_frontend_shows_unavailable_instead_of_an_empty_chart():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "static" / "app.js").read_text(encoding="utf-8")
    assert 'PAIR.status === "unavailable"' in text
    assert "live data unavailable" in text
