"""Gold source fallback: GC=F hourly bars, level set to gold-api.com spot. Offline (fakes only)."""

import types

import numpy as np
import pandas as pd
import pytest

from fxbot import data as fxdata
from fxbot.config import AppConfig
from fxbot.universe import BY_SYMBOL


def _futures(n=200, last=3600.0):
    idx = pd.date_range("2026-10-01", periods=n, freq="h", tz="UTC")
    close = np.linspace(last - 40, last, n)
    return pd.DataFrame({"Open": close, "High": close + 2, "Low": close - 2, "Close": close,
                         "Volume": 1000.0}, index=idx)


def _fake_yf(frame):
    calls = []

    def download(ticker, **kwargs):
        calls.append(ticker)
        return frame.copy()

    return types.SimpleNamespace(download=download), calls


def test_bars_are_shifted_so_the_last_close_is_spot(monkeypatch):
    fake, calls = _fake_yf(_futures(last=3600.0))
    monkeypatch.setitem(__import__("sys").modules, "yfinance", fake)
    monkeypatch.setattr(fxdata, "_try_fetch_json", lambda url, timeout: {"price": 3630.4})
    cfg = AppConfig().data
    out = fxdata.GoldFuturesSpotProvider().fetch(BY_SYMBOL["XAUUSD"], cfg)
    assert calls == ["GC=F"]
    assert out["close"].iloc[-1] == pytest.approx(3630.4)
    # a constant shift: bar-to-bar moves (what indicators use) are unchanged
    raw_close = _futures(last=3600.0)["Close"].to_numpy()
    assert np.allclose(np.diff(out["close"].to_numpy()), np.diff(raw_close))
    assert list(out.columns) == ["open", "high", "low", "close"]


def test_other_symbols_are_not_served(monkeypatch):
    monkeypatch.setattr(fxdata, "_try_fetch_json", lambda *a: pytest.fail("network must not be called"))
    fake, _ = _fake_yf(_futures())
    monkeypatch.setitem(__import__("sys").modules, "yfinance", fake)
    cfg = AppConfig().data
    assert fxdata.GoldFuturesSpotProvider().fetch(BY_SYMBOL["XAGUSD"], cfg) is None
    assert fxdata.GoldFuturesSpotProvider().fetch(BY_SYMBOL["EURUSD"], cfg) is None


def test_missing_spot_price_is_a_provider_error(monkeypatch):
    fake, _ = _fake_yf(_futures())
    monkeypatch.setitem(__import__("sys").modules, "yfinance", fake)
    monkeypatch.setattr(fxdata, "_try_fetch_json", lambda url, timeout: {"updatedAt": "2026-11-27T15:39:20Z"})
    with pytest.raises(fxdata.ProviderError):
        fxdata.GoldFuturesSpotProvider().fetch(BY_SYMBOL["XAUUSD"], AppConfig().data)


def test_empty_futures_is_a_provider_error(monkeypatch):
    fake, _ = _fake_yf(pd.DataFrame())
    monkeypatch.setitem(__import__("sys").modules, "yfinance", fake)
    monkeypatch.setattr(fxdata, "_try_fetch_json", lambda url, timeout: {"price": 3630.4})
    with pytest.raises(fxdata.ProviderError):
        fxdata.GoldFuturesSpotProvider().fetch(BY_SYMBOL["XAUUSD"], AppConfig().data)


def test_gold_provider_comes_after_the_yahoo_spot_ticker():
    order = list(AppConfig().data.provider_order)
    assert order.index("yfinance") < order.index("gold_futures_spot")
    assert "gold_futures_spot" in fxdata.PROVIDERS
