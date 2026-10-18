"""Live market-data path: provider parsing, freshness rules, fallback and breaker.

Every test is offline: provider calls are replaced with deterministic fakes.
"""

import time

import numpy as np
import pandas as pd
import pytest

from fxbot import data as data_mod
from fxbot.config import DataConfig
from fxbot.data import (
    LIVE_BREAKER_FAILURES,
    FrankfurterProvider,
    MarketData,
    ProviderError,
    SyntheticMarket,
    YFinanceProvider,
    _normalize_live,
)
from fxbot.universe import BY_SYMBOL, UNIVERSE


def _cfg(timeframe="1h", bars=1200, order=("yfinance", "frankfurter")) -> DataConfig:
    return DataConfig(timeframe=timeframe, bars=bars, provider_order=order)


def _fake_hourly(ins, cfg, n=3000):
    """A provider-shaped hourly frame: naive timestamps, like yfinance returns."""
    frame = SyntheticMarket(cfg).ohlc(ins, n)
    frame = frame.copy()
    frame.index = frame.index.tz_localize(None)
    return frame


# -- Frankfurter (v2 API) -------------------------------------------------------

def test_frankfurter_parses_v2_rows(monkeypatch):
    rows = []
    for i in range(300):
        d = (pd.Timestamp("2025-01-01") + pd.Timedelta(days=i)).date().isoformat()
        rows.append({"date": d, "base": "EUR", "quote": "USD", "rate": 1.10 + 0.0001 * i})
        rows.append({"date": d, "base": "EUR", "quote": "GBP", "rate": 0.85})   # other quote
    seen = {}

    def fake(url, timeout):
        seen["url"] = url
        return rows

    monkeypatch.setattr(data_mod, "_try_fetch_json", fake)
    frame = FrankfurterProvider().fetch(BY_SYMBOL["EURUSD"], _cfg("1d"))
    assert "api.frankfurter.dev/v2/rates" in seen["url"]
    assert "base=EUR" in seen["url"] and "quotes=USD" in seen["url"]
    assert len(frame) == 300
    assert list(frame.columns) == ["open", "high", "low", "close"]
    assert frame["close"].iloc[-1] == pytest.approx(1.10 + 0.0001 * 299)


def test_frankfurter_skips_metals_without_network(monkeypatch):
    def boom(url, timeout):
        raise AssertionError("must not call the network for metals")

    monkeypatch.setattr(data_mod, "_try_fetch_json", boom)
    assert FrankfurterProvider().fetch(BY_SYMBOL["XAUUSD"], _cfg("1d")) is None


def test_frankfurter_network_error_is_provider_error(monkeypatch):
    def down(url, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr(data_mod, "_try_fetch_json", down)
    with pytest.raises(ProviderError):
        FrankfurterProvider().fetch(BY_SYMBOL["EURUSD"], _cfg("1d"))


def test_frankfurter_bad_shape_is_provider_error(monkeypatch):
    monkeypatch.setattr(data_mod, "_try_fetch_json", lambda u, t: {"message": "not found"})
    with pytest.raises(ProviderError):
        FrankfurterProvider().fetch(BY_SYMBOL["EURUSD"], _cfg("1d"))


# -- yfinance -------------------------------------------------------------------

def test_yfinance_empty_download_counts_as_failure(monkeypatch):
    yf = pytest.importorskip("yfinance")
    monkeypatch.setattr(yf, "download", lambda *a, **k: pd.DataFrame())
    with pytest.raises(ProviderError):
        YFinanceProvider().fetch(BY_SYMBOL["EURUSD"], _cfg("1h"))


def test_yfinance_multiindex_columns_are_flattened(monkeypatch):
    yf = pytest.importorskip("yfinance")
    raw = _fake_hourly(BY_SYMBOL["EURUSD"], _cfg("1h"), n=300)
    raw.columns = pd.MultiIndex.from_product([["Open", "High", "Low", "Close"], ["EURUSD=X"]])
    raw.columns = raw.columns.set_names(["Price", "Ticker"])
    raw.columns = pd.MultiIndex.from_tuples([(c.capitalize(), "EURUSD=X") for c in
                                            ["open", "high", "low", "close"]],
                                            names=["Price", "Ticker"])
    monkeypatch.setattr(yf, "download", lambda *a, **k: raw)
    frame = YFinanceProvider().fetch(BY_SYMBOL["EURUSD"], _cfg("1h"))
    assert list(frame.columns) == ["open", "high", "low", "close"]
    assert len(frame) == 300


def test_yfinance_missing_package_reports_reason(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "yfinance", None)
    reason = YFinanceProvider().unavailable_reason()
    assert reason and "requirements-live.txt" in reason


# -- freshness / alignment rules ------------------------------------------------

def test_daily_provider_is_never_used_for_hourly_model(monkeypatch):
    calls = []
    monkeypatch.setattr(FrankfurterProvider, "fetch",
                        lambda self, ins, cfg: calls.append(ins) or None)
    md = MarketData(_cfg("1h", order=("frankfurter",)))
    md.load(prefer_live=True)
    assert calls == []                      # rejected before any request
    assert md.source_label.startswith("synthetic (live unavailable")


def test_normalize_live_utc_dedupe_and_trim():
    idx = pd.date_range("2026-01-01", periods=400, freq="h")
    base = 1.0 + 0.001 * np.arange(400)
    raw = pd.DataFrame(dict.fromkeys(["open", "high", "low", "close"], base), index=idx)
    raw = pd.concat([raw, raw.iloc[[5]]])                        # duplicate timestamp
    out = _normalize_live(raw, bars=250)
    assert str(out.index.tz) == "UTC"
    assert len(out) == 250 and out.index.is_unique and out.index.is_monotonic_increasing
    assert _normalize_live(raw.iloc[:50], bars=250) is None      # too short to model


def test_full_live_load_is_labelled_and_trimmed(monkeypatch):
    cfg = _cfg("1h", bars=1500)
    monkeypatch.setattr(YFinanceProvider, "unavailable_reason", lambda self: None)
    monkeypatch.setattr(YFinanceProvider, "fetch",
                        lambda self, ins, c: _fake_hourly(ins, c, n=2600))
    md = MarketData(cfg)
    history = md.load(prefer_live=True)
    assert len(history) == len(UNIVERSE)
    assert md.source_label.startswith("live (yfinance, 1h, 32 symbols")
    frame = history["EURUSD"]
    assert len(frame) == 1500
    assert str(frame.index.tz) == "UTC"


def test_partial_live_is_rejected_all_or_nothing(monkeypatch):
    cfg = _cfg("1h", bars=1200)
    monkeypatch.setattr(YFinanceProvider, "unavailable_reason", lambda self: None)

    def fetch(self, ins, c):
        if ins.symbol == "USDZAR":          # one gap must not mix real and simulated data
            raise ProviderError("yfinance: empty response")
        return _fake_hourly(ins, c, n=1500)

    monkeypatch.setattr(YFinanceProvider, "fetch", fetch)
    md = MarketData(cfg)
    history = md.load(prefer_live=True)
    assert md.source_label.startswith("synthetic (live unavailable: USDZAR")
    assert len(history) == len(UNIVERSE)


def test_offline_fallback_is_labelled_with_reason(monkeypatch):
    cfg = _cfg("1h", bars=1200)
    monkeypatch.setattr(YFinanceProvider, "unavailable_reason", lambda self: None)

    def down(self, ins, c):
        raise ProviderError("yfinance: OSError")

    monkeypatch.setattr(YFinanceProvider, "fetch", down)
    md = MarketData(cfg)
    md.load(prefer_live=True)
    assert md.source_label.startswith("synthetic (live unavailable")
    assert "yfinance" in md.source_label


# -- circuit breaker -------------------------------------------------------------

def test_breaker_pauses_after_repeated_failures(monkeypatch):
    cfg = _cfg("1h")
    monkeypatch.setattr(YFinanceProvider, "unavailable_reason", lambda self: None)
    calls = {"n": 0}

    def down(self, ins, c):
        calls["n"] += 1
        raise ProviderError("yfinance: OSError")

    monkeypatch.setattr(YFinanceProvider, "fetch", down)
    md = MarketData(cfg)
    for ins in UNIVERSE[:10]:
        md._fetch_live(ins)
    assert calls["n"] == LIVE_BREAKER_FAILURES      # later symbols never hit the network
    assert md._paused("yfinance")


def test_breaker_recovers_after_cooldown(monkeypatch):
    md = MarketData(_cfg("1h"))
    md._breaker["yfinance"] = {"fails": 0.0, "until": time.time() - 1}
    assert not md._paused("yfinance")
    md._breaker["yfinance"] = {"fails": 0.0, "until": time.time() + 600}
    assert md._paused("yfinance")


def test_success_resets_failure_count(monkeypatch):
    md = MarketData(_cfg("1h"))
    md._record_failure("yfinance", ProviderError("x"))
    md._record_failure("yfinance", ProviderError("x"))
    md._record_success("yfinance")
    md._record_failure("yfinance", ProviderError("x"))
    assert not md._paused("yfinance")


# -- engine / server refresh semantics --------------------------------------------

def test_server_refresh_requests_reload(monkeypatch):
    from fastapi.testclient import TestClient

    from fxbot import server

    seen = []
    # the scan endpoint is paid-only; bypass the access check to test refresh semantics
    monkeypatch.setitem(server.app.dependency_overrides, server.require_access, lambda: None)

    class Stub:
        def scan(self, use_cache=False, reload=False):
            seen.append((use_cache, reload))
            raise RuntimeError("stop after recording the call")

    monkeypatch.setattr(server, "_engine", Stub())
    client = TestClient(server.app, raise_server_exceptions=False)
    client.get("/api/scan")
    client.get("/api/scan?refresh=true")
    assert seen == [(True, False), (False, True)]
