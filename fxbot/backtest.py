"""Event-driven backtester for the signal engine.

Signals use data available through bar ``t`` and execute at the open of bar
``t+1``. Position management mirrors the emitted plan: stop and first target
are checked intrabar (the stop wins if both are touched), a configured partial
is taken at the plan's first target, the stop moves to breakeven, and the
remainder is managed toward the plan's stretch target. Opposite signals exit
at the next bar open. P&L and position sizing are converted into account
currency using rates observable before each bar.

Execution costs: proportional slippage estimated from instrument-specific typical spreads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
import pandas as pd

from .config import AppConfig, SignalConfig
from .conversion import conversion_rate_series
from .indicators import compute_all
from .signals import build_plan
from .universe import BY_SYMBOL


@dataclass
class Trade:
    symbol: str
    side: str
    entry_time: pd.Timestamp
    entry: float
    signal_time: Optional[pd.Timestamp] = None
    exit_time: Optional[pd.Timestamp] = None
    exit: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    target2: float = 0.0
    units: float = 1.0
    r_multiple: float = 0.0
    pnl: float = 0.0
    risk_amount: float = 0.0
    exit_reason: str = ""


@dataclass
class BacktestResult:
    symbol: str
    trades: list[Trade] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    metrics: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)


def _account_pnl(fill: float, entry: float, units: float, sign: int,
                 quote_to_account_rate: float) -> float:
    """Translate price P&L from quote currency into account currency."""
    return (fill - entry) * units * sign * quote_to_account_rate


def _conversion_rates(
    symbol: str,
    df: pd.DataFrame,
    cfg: AppConfig,
    conversion_history: Optional[dict[str, pd.DataFrame]],
    start: int,
) -> pd.Series:
    instrument = BY_SYMBOL.get(symbol)
    quote = instrument.quote if instrument else cfg.risk.account_currency
    account = cfg.risk.account_currency.upper()
    if quote.upper() == account:
        return pd.Series(1.0, index=df.index, dtype=float)

    history = conversion_history or {}
    rates = conversion_rate_series(
        history, quote, account, df.index, lag_bars=1
    )
    backtest_rates = rates.iloc[start + 1:]
    if not np.isfinite(backtest_rates).all() or (backtest_rates <= 0).any():
        raise ValueError(
            f"{symbol}: incomplete lagged {quote}/{account} conversion history "
            "for the backtest period"
        )
    return rates


def _run_single(
    symbol: str,
    df: pd.DataFrame,
    cfg: AppConfig,
    equity: float = 10_000.0,
    conversion_history: Optional[dict[str, pd.DataFrame]] = None,
) -> BacktestResult:
    ind = compute_all(df)
    sig_cfg: SignalConfig = cfg.signal
    ins = BY_SYMBOL.get(symbol)
    spread = (ins.typical_spread_pips * ins.pip) if ins else df["close"].iloc[-1] * 2e-4
    slippage = spread * 0.25

    start = 220  # warm-up for EMA200 etc.
    if len(df) <= start + 1:
        raise ValueError(f"{symbol}: need at least {start + 2} bars to backtest")
    rates = _conversion_rates(symbol, df, cfg, conversion_history, start)

    trades: list[Trade] = []
    eq_idx: list[pd.Timestamp] = []
    eq_val: list[float] = []
    cash = equity
    open_trade: Optional[Trade] = None
    partial_taken = False
    stop_dist = 0.0
    bars_held = 0
    risk_per = equity * cfg.risk.risk_per_trade_pct / 100.0

    # A plan is built from bars strictly before i, then filled on bar i's open.
    # The final bar is reserved for terminal marking/closure, so we do not open
    # a new position there.
    for i in range(start + 1, len(df) - 1):
        t = df.index[i]
        row = df.iloc[i]
        rate = float(rates.iloc[i])
        window = ind.iloc[:i]
        try:
            plan = build_plan(symbol, window, sig_cfg,
                              forecast=None, precomputed=True)
        except (ValueError, IndexError, KeyError):
            plan = None

        closed_on_signal = False
        if (open_trade is not None and plan is not None
                and plan.side in ("BUY", "SELL")
                and plan.side != open_trade.side):
            tr = open_trade
            sign = 1 if tr.side == "BUY" else -1
            fill = float(row["open"]) - slippage if sign > 0 else float(row["open"]) + slippage
            realized = _account_pnl(fill, tr.entry, tr.units, sign, rate)
            tr.pnl += realized
            cash += realized
            tr.exit, tr.exit_time, tr.exit_reason = fill, t, "signal"
            tr.r_multiple = tr.pnl / tr.risk_amount if tr.risk_amount else 0.0
            trades.append(tr)
            open_trade = None
            closed_on_signal = True

        # Execute a signal from the preceding bar at this bar's open. Do not
        # immediately reverse on the same open after closing an old position.
        if (open_trade is None and not closed_on_signal and plan is not None
                and plan.side in ("BUY", "SELL")):
            sign = 1 if plan.side == "BUY" else -1
            entry_price = float(row["open"]) + (slippage if sign > 0 else -slippage)
            stop = float(plan.stop)
            target = float(plan.target)
            target2 = float(plan.target2)
            distance = abs(entry_price - stop)
            valid_levels = (
                (stop < entry_price < target <= target2) if sign > 0
                else (target2 <= target < entry_price < stop)
            )
            entry_rate = rate
            if (valid_levels and distance > 0 and risk_per > 0
                    and np.isfinite(entry_rate) and entry_rate > 0):
                units = risk_per / (distance * entry_rate)
                if np.isfinite(units) and units > 0:
                    open_trade = Trade(
                        symbol=symbol,
                        side=plan.side,
                        entry_time=t,
                        entry=entry_price,
                        signal_time=window.index[-1],
                        stop=stop,
                        target=target,
                        target2=target2,
                        units=float(units),
                        risk_amount=risk_per,
                    )
                    stop_dist = distance
                    bars_held = 0
                    partial_taken = False

        # ---- manage the open position intrabar -----------------------------
        if open_trade is not None:
            tr = open_trade
            bars_held += 1
            long = tr.side == "BUY"
            sign = 1 if long else -1
            hit_stop = (row["low"] <= tr.stop) if long else (row["high"] >= tr.stop)
            hit_target = (row["high"] >= tr.target) if long else (row["low"] <= tr.target)
            hit_runner = (row["high"] >= tr.target2) if long else (row["low"] <= tr.target2)
            hit_time_stop = (
                sig_cfg.time_stop_bars > 0 and bars_held >= sig_cfg.time_stop_bars
            )

            if hit_stop:
                fill = tr.stop - slippage if long else tr.stop + slippage
                realized = _account_pnl(fill, tr.entry, tr.units, sign, rate)
                tr.pnl += realized
                cash += realized
                tr.exit, tr.exit_time, tr.exit_reason = fill, t, "stop"
                tr.r_multiple = tr.pnl / tr.risk_amount if tr.risk_amount else 0.0
                trades.append(tr)
                open_trade = None
            elif hit_target and not partial_taken:
                # The signal's configured first target is the partial-profit fill.
                fraction = float(np.clip(sig_cfg.partial_profit_fraction, 0.0, 1.0))
                partial_units = tr.units * fraction
                fill = tr.target - slippage if long else tr.target + slippage
                partial_pnl = _account_pnl(fill, tr.entry, partial_units, sign, rate)
                cash += partial_pnl
                tr.pnl += partial_pnl
                tr.units -= partial_units
                partial_taken = True
                if tr.units <= 1e-12:
                    tr.units = 0.0
                    tr.exit, tr.exit_time, tr.exit_reason = fill, t, "target"
                    tr.r_multiple = tr.pnl / tr.risk_amount if tr.risk_amount else 0.0
                    trades.append(tr)
                    open_trade = None
                else:
                    tr.stop = tr.entry  # move remaining units to breakeven
            elif hit_runner and partial_taken:
                fill = tr.target2 - slippage if long else tr.target2 + slippage
                realized = _account_pnl(fill, tr.entry, tr.units, sign, rate)
                tr.pnl += realized
                cash += realized
                tr.exit, tr.exit_time, tr.exit_reason = fill, t, "target"
                tr.r_multiple = tr.pnl / tr.risk_amount if tr.risk_amount else 0.0
                trades.append(tr)
                open_trade = None
            elif partial_taken:
                # ATR-style trailing: lock in +1R once the trade runs past +2R.
                r_now = (row["close"] - tr.entry) / stop_dist * sign if stop_dist else 0.0
                if r_now > 2.0:
                    new_stop = tr.entry + (1.0 * stop_dist if long else -1.0 * stop_dist)
                    if (long and new_stop > tr.stop) or (not long and new_stop < tr.stop):
                        tr.stop = new_stop

            # Time stop: predicted move failed to materialize -> close at market.
            if open_trade is not None and hit_time_stop:
                fill = float(row["close"])
                sign = 1 if tr.side == "BUY" else -1
                realized = _account_pnl(fill, tr.entry, tr.units, sign, rate)
                tr.pnl += realized
                cash += realized
                tr.exit, tr.exit_time, tr.exit_reason = fill, t, "time_stop"
                tr.r_multiple = tr.pnl / tr.risk_amount if tr.risk_amount else 0.0
                trades.append(tr)
                open_trade = None

        # Mark-to-market unrealized P&L in account currency.
        unrealized = 0.0
        if open_trade is not None:
            sign = 1 if open_trade.side == "BUY" else -1
            unrealized = _account_pnl(
                float(row["close"]), open_trade.entry, open_trade.units, sign, rate
            )
        eq_idx.append(t)
        eq_val.append(cash + unrealized)

    # Force-close at the final observed close and include the realized amount in
    # the terminal equity point (and thus in return/drawdown metrics).
    final_index = df.index[-1]
    final = df.iloc[-1]
    final_rate = float(rates.iloc[-1])
    if open_trade is not None:
        tr = open_trade
        sign = 1 if tr.side == "BUY" else -1
        fill = float(final["close"])
        realized = _account_pnl(fill, tr.entry, tr.units, sign, final_rate)
        tr.pnl += realized
        cash += realized
        tr.exit, tr.exit_time, tr.exit_reason = fill, final_index, "end_of_data"
        tr.r_multiple = tr.pnl / tr.risk_amount if tr.risk_amount else 0.0
        trades.append(tr)

    eq_idx.append(final_index)
    eq_val.append(cash)
    curve = pd.Series(eq_val, index=pd.DatetimeIndex(eq_idx), name="equity")
    metrics = _metrics(trades, curve, equity, risk_per)
    return BacktestResult(symbol=symbol, trades=trades, equity_curve=curve,
                          metrics=metrics, config=cfg.to_dict())


def _finite(v: float, default: float = 0.0) -> float:
    return default if not np.isfinite(v) else float(v)


def _metrics(trades: list[Trade], curve: pd.Series, equity0: float,
             risk_per: float) -> dict[str, Any]:
    if not trades:
        return {"trades": 0, "note": "no trades generated"}
    pnls = np.array([t.pnl for t in trades], dtype=float)
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    win_rate = len(wins) / len(pnls)
    gross_win = float(wins.sum()) if len(wins) else 0.0
    gross_loss = float(-losses.sum()) if len(losses) else 0.0
    profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")

    rets = curve.pct_change().dropna()
    if len(rets) > 2 and rets.std() > 0:
        sharpe = float(rets.mean() / rets.std() * np.sqrt(252 * 24))  # hourly bars
        downside = rets[rets < 0]
        sortino = float(rets.mean() / downside.std() * np.sqrt(252 * 24)) \
            if len(downside) > 2 and downside.std() > 0 else float("inf")
    else:
        sharpe = sortino = 0.0

    running_max = curve.cummax()
    dd = (curve / running_max - 1.0)
    max_dd = float(-dd.min()) if len(dd) else 0.0

    win_rs = [t.r_multiple for t in trades if t.r_multiple > 0]
    loss_rs = [t.r_multiple for t in trades if t.r_multiple < 0]
    avg_win_r = _finite(float(np.mean(win_rs))) if win_rs else 0.0
    avg_loss_r = _finite(float(np.mean(loss_rs))) if loss_rs else 0.0

    return {
        "trades": len(trades),
        "win_rate": round(_finite(win_rate), 4),
        "profit_factor": round(profit_factor, 3) if np.isfinite(profit_factor) else "inf",
        "total_pnl": round(_finite(float(pnls.sum())), 2),
        "total_return_pct": round(_finite(float((curve.iloc[-1] / equity0 - 1) * 100)), 3) if len(curve) else 0.0,
        "avg_win": round(_finite(float(wins.mean())) if len(wins) else 0.0, 2),
        "avg_loss": round(_finite(float(losses.mean())) if len(losses) else 0.0, 2),
        "avg_win_r": round(avg_win_r, 2),
        "avg_loss_r": round(avg_loss_r, 2),
        "expectancy_r": round(_finite(float(np.mean([t.r_multiple for t in trades]))), 3),
        "sharpe": round(_finite(sharpe), 2),
        "sortino": round(_finite(sortino), 2),
        "max_drawdown_pct": round(_finite(max_dd * 100), 2),
        "risk_per_trade": round(_finite(risk_per), 2),
        "best_trade": round(_finite(float(pnls.max())), 2),
        "worst_trade": round(_finite(float(pnls.min())), 2),
    }


def run_backtest(
    symbol: str,
    df: pd.DataFrame,
    cfg: Optional[AppConfig] = None,
    equity: Optional[float] = None,
    *,
    conversion_history: Optional[dict[str, pd.DataFrame]] = None,
) -> BacktestResult:
    cfg = cfg or AppConfig()
    if df is None or len(df) < 250:
        raise ValueError(f"{symbol}: need at least 250 bars to backtest")
    eq = equity if equity is not None else cfg.risk.equity
    return _run_single(symbol, df, cfg, eq, conversion_history)
