"""Market-scan risk-allocation regression tests."""

import numpy as np
import pandas as pd

import fxbot.engine as engine_module
from fxbot.config import AppConfig
from fxbot.engine import MarketEngine
from fxbot.signals import TradePlan
from fxbot.strength import CurrencyStrength


def _plan(symbol, confidence):
    return TradePlan(
        symbol=symbol, display=symbol, side="BUY", score=0.6,
        confidence=confidence, price=1.1, entry=1.1, stop=1.095,
        target=1.11, target2=1.12, risk_reward=2.0, atr=0.01,
        stop_dist=0.005, expected_move_pct=0.0, p_up=0.6,
        regime="trend_up", factors={},
    )


def test_scan_reserves_proposed_positions_and_vetoes_over_cap(monkeypatch):
    cfg = AppConfig()
    cfg.risk.max_open_positions = 1
    cfg.risk.max_positions_per_currency = 5
    frame_index = pd.date_range("2025-01-01", periods=300, freq="h", tz="UTC")
    close = np.full(300, 1.1)
    frame = pd.DataFrame({
        "open": close, "high": close + 0.001, "low": close - 0.001, "close": close,
    }, index=frame_index)
    history = {"EURUSD": frame, "GBPUSD": frame}

    strength = CurrencyStrength(
        scores=pd.Series({"EUR": 1.0, "GBP": 0.5, "USD": -0.5}),
        ranking=["EUR", "GBP", "USD"], dollar_index=-0.5, breadth=0.6,
    )
    monkeypatch.setattr(engine_module, "compute_strength", lambda _history: strength)
    monkeypatch.setattr(
        engine_module, "correlation_matrix",
        lambda _history: pd.DataFrame(
            [[1.0, 0.9], [0.9, 1.0]],
            index=["EURUSD", "GBPUSD"], columns=["EURUSD", "GBPUSD"],
        ),
    )
    monkeypatch.setattr(
        engine_module, "build_plan",
        lambda symbol, *_args, **_kwargs: _plan(symbol, 0.9 if symbol == "EURUSD" else 0.8),
    )

    engine = MarketEngine(cfg)
    engine._history = history
    engine.forecaster.run = lambda _history: {}

    report = engine.scan()

    assert [plan["symbol"] for plan in report.signals] == ["EURUSD"]
    vetoed = next(plan for plan in report.watchlist if plan["symbol"] == "GBPUSD")
    assert vetoed["risk_veto_reason"] == "max open positions reached"
    assert "DO NOT BUY" in vetoed["actions"][0]
    assert report.risk["projected_positions"] == 1
    assert report.risk["stateful_circuit_breakers_active"] is False
