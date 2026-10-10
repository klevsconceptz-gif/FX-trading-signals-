"""Indicator maths tests — known values and invariants."""

import numpy as np
import pandas as pd
import pytest

from fxbot.indicators import (
    atr,
    bollinger,
    compute_all,
    ema,
    macd,
    rsi,
    sma,
    stochastic,
    support_resistance,
    true_range,
)


def test_sma_known_values():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    out = sma(s, 3)
    assert np.isnan(out.iloc[1])
    assert out.iloc[2] == pytest.approx(2.0)
    assert out.iloc[4] == pytest.approx(4.0)


def test_ema_converges_to_constant():
    s = pd.Series([5.0] * 60)
    out = ema(s, 12)
    assert out.iloc[-1] == pytest.approx(5.0)


def test_rsi_bounds_and_direction(eurusd):
    out = rsi(eurusd["close"], 14).dropna()
    assert ((out >= 0) & (out <= 100)).all()
    # a strictly rising series → RSI near 100
    up = pd.Series(np.linspace(1, 2, 100))
    assert rsi(up, 14).iloc[-1] > 95


def test_true_range_atr_positive(eurusd):
    tr = true_range(eurusd)
    a = atr(eurusd, 14)
    assert (tr.dropna() >= 0).all()
    assert (a.dropna() > 0).all()


def test_bollinger_contains_price_mostly(eurusd):
    b = bollinger(eurusd["close"], 20, 2.0).dropna()
    inside = ((eurusd["close"].loc[b.index] >= b["lower"]) &
              (eurusd["close"].loc[b.index] <= b["upper"]))
    assert inside.mean() > 0.85  # ~95% expected for 2σ; allow band-tail slack
    assert (b["upper"] >= b["lower"]).all()


def test_macd_frame(eurusd):
    m = macd(eurusd["close"])
    assert {"macd", "signal", "hist"}.issubset(m.columns)
    assert np.allclose(m["hist"].dropna(), (m["macd"] - m["signal"]).dropna())


def test_stochastic_range(eurusd):
    st = stochastic(eurusd).dropna()
    assert st["k"].between(0, 100).all()


def test_support_resistance_levels(eurusd):
    sr = support_resistance(eurusd, order=5)
    assert isinstance(sr["support"], list)
    assert isinstance(sr["resistance"], list)
    for lv in sr["support"] + sr["resistance"]:
        assert np.isfinite(lv)


def test_compute_all_has_core_columns(eurusd):
    out = compute_all(eurusd)
    for col in ("ema20", "ema50", "ema200", "rsi14", "macd_hist",
                "bb_pct_b", "atr14", "adx", "stoch_k", "roc10", "donchian_upper"):
        assert col in out.columns, col
    assert len(out) == len(eurusd)
