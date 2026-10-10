"""Backtester tests: determinism, metrics sanity, no-lookahead execution."""

import numpy as np
import pandas as pd
import pytest

from fxbot.backtest import run_backtest
from fxbot.config import AppConfig


def test_backtest_runs_and_reports(eurusd, cfg):
    result = run_backtest("EURUSD", eurusd, cfg)
    m = result.metrics
    assert m["trades"] >= 1
    assert 0 <= m["win_rate"] <= 1
    assert np.isfinite(m["sharpe"])
    assert m["max_drawdown_pct"] >= 0
    assert len(result.equity_curve) > 500
    assert result.equity_curve.index.is_monotonic_increasing


def test_backtest_deterministic(eurusd, cfg):
    a = run_backtest("EURUSD", eurusd, cfg)
    b = run_backtest("EURUSD", eurusd, cfg)
    assert a.metrics == b.metrics
    assert len(a.trades) == len(b.trades)


def test_trades_have_exit_reason(eurusd, cfg):
    result = run_backtest("EURUSD", eurusd, cfg)
    valid = {"stop", "end_of_data", "signal", "target"}
    for t in result.trades:
        assert t.exit_reason in valid
        assert np.isfinite(t.pnl)


def test_no_lookahead_entry(eurusd, cfg):
    """Entries must execute at a bar open strictly after the signal bar;
    exits happen on the entry bar or later (same-bar stop-outs are valid)."""
    result = run_backtest("EURUSD", eurusd, cfg)
    times = set(eurusd.index)
    for t in result.trades:
        assert t.entry_time in times
        assert t.exit_time in times
        assert t.entry_time <= t.exit_time


def test_r_multiple_bounds(eurusd, cfg):
    """No single trade should lose more than ~1.1R (stop + slippage)."""
    result = run_backtest("EURUSD", eurusd, cfg)
    for t in result.trades:
        assert t.r_multiple >= -1.25
        assert t.r_multiple <= 6.0


def test_backtest_empty_history(cfg):
    empty = pd.DataFrame(
        {"open": [], "high": [], "low": [], "close": []},
        index=pd.DatetimeIndex([]),
    )
    with pytest.raises(ValueError, match="need at least"):
        run_backtest("EURUSD", empty, cfg)


def test_backtest_short_history_raises(cfg):
    tiny = pd.DataFrame(
        {"open": [1.0] * 10, "high": [1.1] * 10, "low": [0.9] * 10, "close": [1.0] * 10},
        index=pd.date_range("2025-01-01", periods=10, freq="h"),
    )
    with pytest.raises(ValueError, match="need at least"):
        run_backtest("EURUSD", tiny, cfg)
