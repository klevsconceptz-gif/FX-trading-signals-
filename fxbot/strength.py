"""Currency-strength engine & market-regime detection.

The strength meter is the classic cross-sectional approach (EarnForex
Currency-Strength-Matrix et al.): every currency's relative performance is
averaged across *all* pairs containing it, producing a coherent ranking
for the whole market.  With the synthetic factor model this recovers the
true latent factors; with live data it reflects real flows.

Regime detection classifies each pair as trending / ranging / volatile
using ADX, Bollinger bandwidth and realized volatility, which switches
the signal engine between trend-following and mean-reversion behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .indicators import compute_all
from .universe import BY_SYMBOL, Instrument


@dataclass
class CurrencyStrength:
    scores: pd.Series          # currency -> strength in [-100, 100]
    ranking: list[str]         # strongest → weakest
    dollar_index: float        # USD strength (DXY-like composite)
    breadth: float             # fraction of pairs with positive 24-bar return


@dataclass
class Regime:
    name: str                  # 'trend_up' | 'trend_down' | 'range' | 'volatile'
    adx: float
    vol_ratio: float


def compute_strength(history: dict[str, pd.DataFrame],
                     lookback: int = 48) -> CurrencyStrength:
    """Cross-sectional currency strength from a full-market history map."""
    contrib: dict[str, list[float]] = {}
    positive = 0
    total = 0

    for symbol, df in history.items():
        ins: Instrument | None = BY_SYMBOL.get(symbol)
        if ins is None or len(df) < lookback + 2:
            continue
        ret = float(df["close"].iloc[-1] / df["close"].iloc[-1 - lookback] - 1.0)
        total += 1
        if ret > 0:
            positive += 1
        scale = 100.0  # ~1% move maps to strength 1; clipped later
        contrib.setdefault(ins.base, []).append(ret * scale)
        contrib.setdefault(ins.quote, []).append(-ret * scale)

    scores = {
        cur: float(np.clip(np.mean(vals), -100.0, 100.0))
        for cur, vals in contrib.items()
    }
    series = pd.Series(scores).sort_values(ascending=False)
    breadth = positive / total if total else 0.5
    dollar_index = scores.get("USD", 0.0)
    return CurrencyStrength(
        scores=series,
        ranking=list(series.index),
        dollar_index=dollar_index,
        breadth=breadth,
    )


def detect_regime(df: pd.DataFrame, adx_threshold: float = 20.0) -> Regime:
    if {"adx", "bb_bandwidth", "ema50", "ema200", "close"}.issubset(df.columns):
        ind = df.tail(220)                      # already carries the indicators
    else:
        ind = compute_all(df.tail(220)) if len(df) > 220 else compute_all(df)
    adx_val = float(ind["adx"].iloc[-1]) if np.isfinite(ind["adx"].iloc[-1]) else 0.0
    bw = ind["bb_bandwidth"].iloc[-1]
    bw_mean = ind["bb_bandwidth"].rolling(100).mean().iloc[-1]
    vol_ratio = float(bw / bw_mean) if (bw_mean and np.isfinite(bw_mean) and bw_mean > 0) else 1.0
    close = ind["close"]
    trend_up = close.iloc[-1] > ind["ema50"].iloc[-1] > ind["ema200"].iloc[-1]
    trend_down = close.iloc[-1] < ind["ema50"].iloc[-1] < ind["ema200"].iloc[-1]

    if vol_ratio > 1.8:
        name = "volatile"
    elif adx_val >= adx_threshold and trend_up:
        name = "trend_up"
    elif adx_val >= adx_threshold and trend_down:
        name = "trend_down"
    else:
        name = "range"
    return Regime(name=name, adx=adx_val, vol_ratio=vol_ratio)


def correlation_matrix(history: dict[str, pd.DataFrame],
                       symbols: list[str] | None = None,
                       window: int = 200) -> pd.DataFrame:
    """Rolling return correlation across the tracked universe."""
    symbols = symbols or sorted(history.keys())
    rets = {}
    for s in symbols:
        df = history.get(s)
        if df is None or len(df) < window + 2:
            continue
        rets[s] = df["close"].pct_change().tail(window)
    if not rets:
        return pd.DataFrame()
    return pd.DataFrame(rets).corr()
