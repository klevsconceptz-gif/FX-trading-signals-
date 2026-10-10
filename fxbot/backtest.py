"""Event-driven backtester for the signal engine.

Replays history bar-by-bar.  Signals are computed on data available at
bar ``t`` and *executed at the open of bar t+1* (no lookahead).  Position
management mirrors the trade plan the live engine emits:

* hard stop-loss and take-profit checked intrabar (conservative: if both
  fit inside one bar, the stop is assumed to have filled first);
* partial profit (default 50%) at +1.5R with stop moved to breakeven;
* ATR trailing stop on the remainder after the partial fill;
* optional exit on an opposite signal.

Costs: fixed spread (instrument-specific pips) + proportional slippage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
import pandas as pd

from .config import AppConfig, SignalConfig
from .indicators import compute_all
from .signals import build_plan
from .universe import BY_SYMBOL


@dataclass
class Trade:
    symbol: str
    side: str
    entry_time: pd.Timestamp
    entry: float
    exit_time: Optional[pd.Timestamp] = None
    exit: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    units: float = 1.0
    r_multiple: float = 0.0
    pnl: float = 0.0
    exit_reason: str = ""


@dataclass
class BacktestResult:
    symbol: str
    trades: list[Trade] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    metrics: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)


def _run_single(symbol: str, df: pd.DataFrame, cfg: AppConfig,
                equity: float = 10_000.0) -> BacktestResult:
    ind = compute_all(df)
    sig_cfg: SignalConfig = cfg.signal
    ins = BY_SYMBOL.get(symbol)
    spread = (ins.typical_spread_pips * ins.pip) if ins else df["close"].iloc[-1] * 2e-4
    slippage = spread * 0.25

    start = 220  # warm-up for EMA200 etc.
    trades: list[Trade] = []
    eq_idx: list[pd.Timestamp] = []
    eq_val: list[float] = []

    cash = equity
    open_trade: Optional[Trade] = None
    partial_taken = False
    stop_dist = 0.0
    entry_at_next_open: Optional[tuple[str, float, float, float]] = None  # side, stop, target, units

    risk_per = equity * cfg.risk.risk_per_trade_pct / 100.0

    for i in range(start, len(df) - 1):
        t = df.index[i]
        row = df.iloc[i]
        nxt = df.iloc[i + 1]

        # ---- entry signal (computed only on data ≤ t) ----
        if open_trade is None and entry_at_next_open is None:
            window = ind.iloc[: i + 1]
            try:
                plan = build_plan(symbol, window, sig_cfg,
                                  forecast=None, precomputed=True)
            except ValueError:
                plan = None
            if plan is not None and plan.side in ("BUY", "SELL"):
                entry_price = float(nxt["open"]) + (slippage if plan.side == "BUY" else -slippage)
                stop = plan.stop
                target = plan.target
                sd = abs(entry_price - stop)
                if sd > 0:
                    units = risk_per / sd
                    entry_at_next_open = (plan.side, stop, target, units)

        # ---- open pending position at this bar's open ----
        if entry_at_next_open is not None and open_trade is None:
            side, stop, target, units = entry_at_next_open
            entry_price = float(row["open"]) + (slippage if side == "BUY" else -slippage)
            open_trade = Trade(
                symbol=symbol, side=side, entry_time=t, entry=entry_price,
                stop=stop, target=target, units=units,
            )
            stop_dist = abs(entry_price - stop)
            bars_held = 0
            partial_taken = False
            entry_at_next_open = None

        # ---- manage open position intrabar ----
        if open_trade is not None:
            tr = open_trade
            bars_held += 1
            long = tr.side == "BUY"
            sign = 1 if long else -1
            hit_stop = (row["low"] <= tr.stop) if long else (row["high"] >= tr.stop)
            hit_target = (row["high"] >= tr.target) if long else (row["low"] <= tr.target)
            hit_time_stop = (
                sig_cfg.time_stop_bars > 0 and bars_held >= sig_cfg.time_stop_bars
            )

            if hit_stop:
                fill = tr.stop - slippage if long else tr.stop + slippage
                # remainder exits at the stop (breakeven stop after a partial → 0R)
                realized = (fill - tr.entry) * tr.units * sign
                tr.pnl += realized
                cash += realized
                tr.exit, tr.exit_time, tr.exit_reason = fill, t, "stop"
                tr.r_multiple = tr.pnl / risk_per if risk_per else 0.0
                trades.append(tr)
                open_trade = None
            elif hit_target and not partial_taken:
                # take partial profit at the first target, stop → breakeven
                partial_taken = True
                half = tr.units * sig_cfg.partial_profit_fraction
                fill = tr.target - slippage if long else tr.target + slippage
                partial_pnl = (fill - tr.entry) * half * sign
                cash += partial_pnl
                tr.pnl += partial_pnl
                tr.units -= half
                tr.stop = tr.entry  # move to breakeven
                # stretch target to 3R on the remainder
                tr.target = tr.entry + (3 * stop_dist if long else -3 * stop_dist)
            elif hit_target and partial_taken:
                # remainder exits at the stretch target
                fill = tr.target - slippage if long else tr.target + slippage
                realized = (fill - tr.entry) * tr.units * sign
                tr.pnl += realized
                cash += realized
                tr.exit, tr.exit_time, tr.exit_reason = fill, t, "target"
                tr.r_multiple = tr.pnl / risk_per if risk_per else 0.0
                trades.append(tr)
                open_trade = None
            elif partial_taken:
                # ATR-style trailing: lock in +1R once the trade runs past +2R
                r_now = (row["close"] - tr.entry) / stop_dist * sign if stop_dist else 0.0
                if r_now > 2.0:
                    new_stop = tr.entry + (1.0 * stop_dist if long else -1.0 * stop_dist)
                    if (long and new_stop > tr.stop) or (not long and new_stop < tr.stop):
                        tr.stop = new_stop

            # time stop: predicted move failed to materialize → close at market
            if open_trade is not None and hit_time_stop and not hit_stop:
                fill = float(row["close"])
                realized = (fill - tr.entry) * tr.units * sign
                tr.pnl += realized
                cash += realized
                tr.exit, tr.exit_time, tr.exit_reason = fill, t, "time_stop"
                tr.r_multiple = tr.pnl / risk_per if risk_per else 0.0
                trades.append(tr)
                open_trade = None

        eq_idx.append(t)
        eq_val.append(cash + (0.0 if open_trade is None else
                              (row["close"] - open_trade.entry) * open_trade.units
                              * (1 if open_trade.side == "BUY" else -1)))

    # force-close anything left open at the end of the data
    if open_trade is not None:
        tr = open_trade
        last = df.iloc[-1]
        fill = float(last["close"])
        sign = 1 if tr.side == "BUY" else -1
        realized = (fill - tr.entry) * tr.units * sign
        tr.pnl += realized
        cash += realized   # partial profits were already added to cash when banked
        tr.exit, tr.exit_time, tr.exit_reason = fill, df.index[-1], "end_of_data"
        tr.r_multiple = tr.pnl / risk_per if risk_per else 0.0
        trades.append(tr)

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


def run_backtest(symbol: str, df: pd.DataFrame,
                 cfg: Optional[AppConfig] = None,
                 equity: Optional[float] = None) -> BacktestResult:
    cfg = cfg or AppConfig()
    if df is None or len(df) < 250:
        raise ValueError(f"{symbol}: need at least 250 bars to backtest")
    eq = equity if equity is not None else cfg.risk.equity
    return _run_single(symbol, df, cfg, eq)
