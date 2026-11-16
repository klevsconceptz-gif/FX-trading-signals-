"""A live snapshot without gold still publishes: FX pairs only, with the gap named in the label."""

from fxbot.config import AppConfig
from fxbot.data import ProviderError, SyntheticMarket, YFinanceProvider
from fxbot.engine import MarketEngine
from fxbot.publish import build_sql, collect
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


def test_fx_only_snapshot_publishes_without_gold(monkeypatch):
    engine = _engine(monkeypatch, fail_metals=True)
    report, pairs = collect(engine)                 # no PublishError: the source is live

    assert report["data_source"].startswith("live (")
    assert "excluded: XAUUSD" in report["data_source"]
    assert "XAUUSD" not in pairs and "XAGUSD" not in pairs
    assert set(pairs) == {ins.symbol for ins in UNIVERSE if ins.kind == "fx"}

    sql = build_sql(report, pairs, run_id=1)
    assert "'pair:EURUSD'" in sql and "'pair:XAUUSD'" not in sql
    assert "excluded: XAUUSD" in sql                # the label reaches the D1 row


def test_full_live_snapshot_still_includes_gold(monkeypatch):
    engine = _engine(monkeypatch, fail_metals=False)
    report, pairs = collect(engine)
    assert "excluded" not in report["data_source"]
    assert "XAUUSD" in pairs and "XAGUSD" in pairs
