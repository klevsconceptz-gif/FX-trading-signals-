"""Backtester tests: determinism, metrics sanity, timing, P&L and exits."""

import numpy as np
import pandas as pd
import pytest

from fxbot.backtest import run_backtest
from fxbot.signals import TradePlan


def _test_frame(symbol="EURUSD", n=260, price=1.1):
    idx = pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC")
    close = np.full(n, price, dtype=float)
    frame = pd.DataFrame({
        "open": close.copy(),
        "high": close + (0.001 if symbol == "EURUSD" else 0.1),
        "low": close - (0.001 if symbol == "EURUSD" else 0.1),
        "close": close,
    }, index=idx)
    return frame


def _plan(symbol, window, side="BUY", *, stop=None, target=None, target2=None):
    price = float(window["close"].iloc[-1])
    is_long = side == "BUY"
    stop = stop if stop is not None else price - 0.01 if is_long else price + 0.01
    target = target if target is not None else price + 0.02 if is_long else price - 0.02
    target2 = target2 if target2 is not None else price + 0.03 if is_long else price - 0.03
    return TradePlan(
        symbol=symbol, display=symbol, side=side, score=0.5 if is_long else -0.5,
        confidence=0.8, price=price, entry=price, stop=stop, target=target,
        target2=target2, risk_reward=2.0, atr=0.01,
        stop_dist=abs(price - stop), expected_move_pct=0.0, p_up=0.5,
        regime="range", factors={},
    )


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
    valid = {"stop", "end_of_data", "signal", "target", "time_stop"}
    for trade in result.trades:
        assert trade.exit_reason in valid
        assert np.isfinite(trade.pnl)


def test_no_lookahead_entry(eurusd, cfg):
    """Every entry is filled strictly after the bar that produced its signal."""
    result = run_backtest("EURUSD", eurusd, cfg)
    times = set(eurusd.index)
    for trade in result.trades:
        assert trade.signal_time in times
        assert trade.entry_time in times
        assert trade.signal_time < trade.entry_time
        assert trade.entry_time <= trade.exit_time


def test_r_multiple_bounds(eurusd, cfg):
    """No single trade should lose more than ~1.1R (stop + slippage)."""
    result = run_backtest("EURUSD", eurusd, cfg)
    for trade in result.trades:
        assert trade.r_multiple >= -1.25
        assert trade.r_multiple <= 6.0


def test_terminal_close_is_in_final_equity(monkeypatch, cfg):
    """Forced terminal P&L is realized and included at the final timestamp."""
    import fxbot.backtest as backtest

    frame = _test_frame(n=260)
    frame.iloc[-1, frame.columns.get_loc("close")] = 1.11
    frame.iloc[-1, frame.columns.get_loc("high")] = 1.112
    frame.iloc[-1, frame.columns.get_loc("low")] = 1.109
    cfg.signal.time_stop_bars = 0

    def always_buy(symbol, window, signal_cfg, forecast=None, precomputed=False):
        return _plan(symbol, window, stop=1.0, target=1.2, target2=1.3)

    monkeypatch.setattr(backtest, "build_plan", always_buy)
    result = run_backtest("EURUSD", frame, cfg)
    assert result.trades
    assert result.trades[-1].exit_reason == "end_of_data"
    assert result.equity_curve.index[-1] == frame.index[-1]
    assert result.equity_curve.iloc[-1] == pytest.approx(
        cfg.risk.equity + sum(trade.pnl for trade in result.trades)
    )
    assert result.metrics["total_pnl"] == pytest.approx(
        sum(trade.pnl for trade in result.trades), abs=0.01
    )


def test_backtest_enters_on_next_bar_open(monkeypatch, cfg):
    import fxbot.backtest as backtest

    frame = _test_frame()
    seen_signal_times = []

    def always_buy(symbol, window, signal_cfg, forecast=None, precomputed=False):
        seen_signal_times.append(window.index[-1])
        return _plan(symbol, window, stop=1.0, target=1.2, target2=1.3)

    monkeypatch.setattr(backtest, "build_plan", always_buy)
    result = run_backtest("EURUSD", frame, cfg)
    first = result.trades[0]
    assert first.signal_time == seen_signal_times[0]
    assert first.entry_time == frame.index[221]
    assert first.entry_time > first.signal_time
    assert first.entry == pytest.approx(frame.loc[first.entry_time, "open"] + 0.000015)


def test_partial_profit_uses_plan_first_target(monkeypatch, cfg):
    import fxbot.backtest as backtest

    frame = _test_frame(n=260)
    # Entry at 221, partial at the configured plan target on 222, then the
    # breakeven stop is crossed on 223.
    frame.iloc[222, frame.columns.get_loc("high")] = 1.102
    frame.iloc[223, frame.columns.get_loc("low")] = 1.099
    cfg.signal.time_stop_bars = 0

    def always_buy(symbol, window, signal_cfg, forecast=None, precomputed=False):
        return _plan(symbol, window, stop=1.09, target=1.101, target2=1.103)

    monkeypatch.setattr(backtest, "build_plan", always_buy)
    result = run_backtest("EURUSD", frame, cfg)
    trade = result.trades[0]
    assert trade.target == pytest.approx(1.101)
    assert trade.exit_reason == "stop"
    assert trade.exit == pytest.approx(trade.entry - 0.000015)
    assert trade.pnl > 0  # the partial gain was banked before breakeven exit


def test_opposite_signal_exits_at_next_open(monkeypatch, cfg):
    import fxbot.backtest as backtest

    frame = _test_frame()
    cfg.signal.time_stop_bars = 0

    def flip_after_entry(symbol, window, signal_cfg, forecast=None, precomputed=False):
        if len(window) <= 221:
            return _plan(symbol, window, "BUY", stop=1.0, target=1.2, target2=1.3)
        return _plan(symbol, window, "SELL", stop=1.2, target=0.9, target2=0.8)

    monkeypatch.setattr(backtest, "build_plan", flip_after_entry)
    result = run_backtest("EURUSD", frame, cfg)
    first = result.trades[0]
    assert first.exit_reason == "signal"
    assert first.exit_time == frame.index[222]


def test_non_account_quote_pnl_converts_with_lagged_rate(monkeypatch, cfg):
    import fxbot.backtest as backtest

    frame = _test_frame("USDJPY", n=260, price=150.0)
    frame.iloc[-1, frame.columns.get_loc("close")] = 151.0
    frame.iloc[-1, frame.columns.get_loc("high")] = 151.1
    frame.iloc[-1, frame.columns.get_loc("low")] = 150.8
    cfg.risk.equity = 10_000.0
    cfg.risk.risk_per_trade_pct = 1.0
    cfg.signal.time_stop_bars = 0

    def one_signal_then_wait(symbol, window, signal_cfg, forecast=None, precomputed=False):
        if len(window) == 221:
            return _plan(symbol, window, "BUY", stop=149.0, target=160.0, target2=170.0)
        return _plan(symbol, window, "WAIT", stop=150.0, target=150.0, target2=150.0)

    monkeypatch.setattr(backtest, "build_plan", one_signal_then_wait)
    result = run_backtest("USDJPY", frame, cfg, conversion_history={"USDJPY": frame})
    trade = result.trades[0]
    slippage = 0.8 * 0.01 * 0.25
    entry = 150.0 + slippage
    risk_per = 100.0
    units = risk_per / ((entry - 149.0) / 150.0)
    expected_pnl = (151.0 - entry) * units / 150.0
    assert trade.exit_reason == "end_of_data"
    assert trade.pnl == pytest.approx(expected_pnl, rel=1e-6)
    assert trade.pnl == pytest.approx(99.6, abs=0.2)


def test_backtest_empty_history(cfg):
    empty = pd.DataFrame(
        {"open": [], "high": [], "low": [], "close": []},
        index=pd.DatetimeIndex([]),
    )
    with pytest.raises(ValueError, match="need at least"):
        run_backtest("EURUSD", empty, cfg)


def test_backtest_short_history_raises(cfg):
    tiny = pd.DataFrame(
        {"open": [1.0] * 10, "high": [1.1] * 10, "low": [0.9] * 10, "close": [1.0] * 10,
         }, index=pd.date_range("2025-01-01", periods=10, freq="h"),
    )
    with pytest.raises(ValueError, match="need at least"):
        run_backtest("EURUSD", tiny, cfg)


def test_pinned_eurusd_baseline_matches_cli_path():
    """Regression pin for the deterministic synthetic market; update intentionally when it changes."""
    from fxbot.config import AppConfig
    from fxbot.data import MarketData

    cfg = AppConfig()
    cfg.data.bars = 3000
    cfg.risk.equity = 10_000.0
    cfg.risk.risk_per_trade_pct = 0.75
    df = MarketData(cfg.data).load(["EURUSD"])["EURUSD"]
    m = run_backtest("EURUSD", df, cfg, equity=10_000.0).metrics
    assert m["trades"] == 65
    assert m["profit_factor"] == pytest.approx(1.632, abs=0.001)
    assert m["total_return_pct"] == pytest.approx(15.701, abs=0.001)
