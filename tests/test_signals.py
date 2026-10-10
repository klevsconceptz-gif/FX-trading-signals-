"""Signal-engine tests: plan invariants and factor sanity."""

import numpy as np
import pandas as pd

from fxbot.config import SignalConfig
from fxbot.signals import build_plan
from fxbot.strength import compute_strength, detect_regime


def test_wait_on_flat_market():
    """A dead-flat market must not produce a directional signal."""
    idx = pd.date_range("2025-01-01", periods=300, freq="h")
    flat = pd.DataFrame(
        {"open": 1.1, "high": 1.1001, "low": 1.0999, "close": 1.1},
        index=idx,
    )
    plan = build_plan("EURUSD", flat, SignalConfig())
    assert plan.side == "WAIT"
    assert abs(plan.score) < 0.3


def test_plan_level_invariants_long(eurusd):
    """Long plans must satisfy entry > stop, target > entry, R:R ≥ min."""
    cfg = SignalConfig()
    for tail in (600, 800, 1000, 1200):
        sub = eurusd.iloc[:tail]
        plan = build_plan("EURUSD", sub, cfg)
        if plan.side == "BUY":
            assert plan.stop < plan.entry < plan.target < plan.target2 or (
                plan.stop < plan.entry < plan.target
            )
            assert plan.risk_reward >= cfg.min_risk_reward - 1e-9
            assert plan.confidence >= 0
            assert len(plan.notes) >= 2


def test_plan_level_invariants_short(eurusd):
    cfg = SignalConfig()
    for tail in (650, 900, 1150):
        plan = build_plan("EURUSD", eurusd.iloc[:tail], cfg)
        if plan.side == "SELL":
            assert plan.target < plan.entry < plan.stop
            assert plan.risk_reward >= cfg.min_risk_reward - 1e-9


def test_synthetic_uptrend_produces_buy():
    """A strong synthetic uptrend should flip the engine to BUY."""
    idx = pd.date_range("2025-01-01", periods=300, freq="h")
    close = 1.0 * np.cumprod(1 + np.abs(np.sin(np.arange(300) / 12)) * 0.0012 + 0.0009)
    df = pd.DataFrame(
        {
            "open": np.roll(close, 1),
            "high": close * 1.0007,
            "low": close * 0.9993,
            "close": close,
        },
        index=idx,
    ).iloc[1:]
    plan = build_plan("EURUSD", df, SignalConfig())
    assert plan.side == "BUY"
    assert plan.score > 0.25


def test_regime_detection(eurusd):
    reg = detect_regime(eurusd)
    assert reg.name in {"trend_up", "trend_down", "range", "volatile"}
    assert reg.adx >= 0


def test_currency_strength_coherent(full_history):
    st = compute_strength(full_history)
    assert len(st.scores) >= 8
    assert st.ranking[0] == st.scores.idxmax()
    assert -100 <= st.dollar_index <= 100
    assert 0 <= st.breadth <= 1
    # factor model is zero-sum-ish: strengths should not all be one-sided
    assert st.scores.max() > 0 or st.scores.min() < 0


def test_score_range(eurusd):
    for tail in (400, 700, 1000):
        plan = build_plan("EURUSD", eurusd.iloc[:tail], SignalConfig())
        assert -1.0 <= plan.score <= 1.0
        assert 0.0 <= plan.confidence <= 1.0
        for v in plan.factors.values():
            assert -1.5 <= v <= 1.5


def test_instruction_lifecycle_buy():
    """A BUY plan must instruct: when to buy, the predicted outcome, when to sell."""
    idx = pd.date_range("2025-01-01", periods=300, freq="h")
    close = 1.0 * np.cumprod(1 + np.abs(np.sin(np.arange(300) / 12)) * 0.0012 + 0.0009)
    df = pd.DataFrame(
        {
            "open": np.roll(close, 1),
            "high": close * 1.0007,
            "low": close * 0.9993,
            "close": close,
        },
        index=idx,
    ).iloc[1:]
    cfg = SignalConfig()
    plan = build_plan("EURUSD", df, cfg)
    assert plan.side == "BUY"
    blob = " ".join(plan.actions).upper()
    assert "BUY NOW" in blob                      # ① when to buy
    assert "TAKE-PROFIT" in blob                  # ② sell instruction
    assert "STOP-LOSS" in blob                    # ③ sell-all protection
    assert "TIME STOP" in blob                    # ④ time-based exit
    assert "FLIP" in blob or "CONTRARY" in blob   # ⑤ early exit
    assert plan.predicted_outcome                 # predicted outcome text
    assert 0.0 < plan.p_target_first < 1.0
    assert plan.expected_bars >= 1
    assert plan.time_stop_bars == cfg.time_stop_bars


def test_instruction_lifecycle_sell():
    """A SELL (short) plan must instruct: sell now, cover at TP/SL."""
    idx = pd.date_range("2025-01-01", periods=300, freq="h")
    close = 1.2 * np.cumprod(1 - np.abs(np.sin(np.arange(300) / 12)) * 0.0012 - 0.0009)
    df = pd.DataFrame(
        {
            "open": np.roll(close, 1),
            "high": close * 1.0007,
            "low": close * 0.9993,
            "close": close,
        },
        index=idx,
    ).iloc[1:]
    cfg = SignalConfig()
    plan = build_plan("EURUSD", df, cfg)
    assert plan.side == "SELL"
    blob = " ".join(plan.actions).upper()
    assert "SELL NOW" in blob
    assert "TAKE-PROFIT" in blob
    assert "STOP-LOSS" in blob
    assert plan.predicted_outcome
    assert 0.0 < plan.p_target_first < 1.0


def test_wait_plan_says_do_not_buy():
    idx = pd.date_range("2025-01-01", periods=300, freq="h")
    flat = pd.DataFrame(
        {"open": 1.1, "high": 1.1001, "low": 1.0999, "close": 1.1}, index=idx
    )
    plan = build_plan("EURUSD", flat, SignalConfig())
    assert plan.side == "WAIT"
    blob = " ".join(plan.actions).upper()
    assert "DO NOT BUY" in blob
