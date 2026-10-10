"""Feature engineering for the ML forecasters.

Features are strictly *backward-looking* (no lookahead): every row t uses
data available at or before bar t to describe the market state.  Labels
are produced separately by :mod:`fxbot.ml`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import compute_all


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build the model feature matrix from an OHLC frame.

    Returns a DataFrame aligned to ``df.index`` (may contain NaN head rows
    which the trainer drops).
    """
    ind = compute_all(df)
    close = ind["close"]
    log_ret = np.log(close).diff()

    feats = pd.DataFrame(index=df.index)

    # --- returns at multiple horizons ---
    for lag in (1, 2, 3, 5, 8, 13, 21):
        feats[f"ret_{lag}"] = log_ret.rolling(lag).sum()

    # --- momentum / oscillators (normalized to be scale-free) ---
    feats["rsi14"] = (ind["rsi14"] - 50.0) / 50.0
    feats["stoch_k"] = (ind["stoch_k"] - 50.0) / 50.0
    feats["stoch_d"] = (ind["stoch_d"] - 50.0) / 50.0
    feats["cci20"] = np.clip(ind["cci20"] / 200.0, -3, 3)
    atr_rel = ind["atr14"] / close
    feats["macd_hist_norm"] = ind["macd_hist"] / (atr_rel * close).replace(0, np.nan)
    feats["macd_slope"] = ind["macd_hist"].diff(3) / (atr_rel * close).replace(0, np.nan)

    # --- trend geometry ---
    feats["ema20_vs_close"] = (close / ind["ema20"] - 1.0) * 100.0
    feats["ema50_vs_close"] = (close / ind["ema50"] - 1.0) * 100.0
    feats["ema200_vs_close"] = (close / ind["ema200"] - 1.0) * 100.0
    feats["ema20_vs_ema50"] = (ind["ema20"] / ind["ema50"] - 1.0) * 100.0
    feats["ema50_slope"] = ind["ema50"].pct_change(5) * 100.0
    feats["adx"] = ind["adx"] / 50.0
    feats["di_diff"] = (ind["plus_di"] - ind["minus_di"]) / 50.0

    # --- volatility state ---
    feats["bb_pct_b"] = ind["bb_pct_b"]
    feats["bb_bandwidth"] = ind["bb_bandwidth"]
    feats["bb_bandwidth_z"] = (
        ind["bb_bandwidth"] - ind["bb_bandwidth"].rolling(100).mean()
    ) / ind["bb_bandwidth"].rolling(100).std(ddof=0)
    feats["atr_ratio"] = ind["atr14"] / ind["atr14"].rolling(50).mean()
    feats["vol_ratio"] = ind["vol20"] / ind["vol20"].rolling(50).mean()

    # --- candle anatomy ---
    body = (close - ind["open"]) / atr_rel.replace(0, np.nan) / close
    rng = (ind["high"] - ind["low"]) / atr_rel.replace(0, np.nan) / close
    feats["body_norm"] = body
    feats["range_norm"] = rng
    feats["upper_wick"] = ((ind["high"] - np.maximum(close, ind["open"])) / (ind["high"] - ind["low"]).replace(0, np.nan))
    feats["lower_wick"] = ((np.minimum(close, ind["open"]) - ind["low"]) / (ind["high"] - ind["low"]).replace(0, np.nan))

    # --- position inside the recent range ---
    hi20 = ind["high"].rolling(20).max()
    lo20 = ind["low"].rolling(20).min()
    feats["range_pos"] = (close - lo20) / (hi20 - lo20).replace(0, np.nan)
    hi50 = ind["high"].rolling(50).max()
    lo50 = ind["low"].rolling(50).min()
    feats["range_pos_50"] = (close - lo50) / (hi50 - lo50).replace(0, np.nan)

    # --- time-of-day cyclical encoding (session effects in FX) ---
    if getattr(df.index, "hour", None) is not None:
        hour = df.index.hour + df.index.minute / 60.0
        feats["hour_sin"] = np.sin(2 * np.pi * hour / 24.0)
        feats["hour_cos"] = np.cos(2 * np.pi * hour / 24.0)

    # --- cross-sectional context: shared currency factor momentum ---
    # (filled by the trainer when full-market context is available)
    feats["log_ret_1"] = log_ret

    return feats.replace([np.inf, -np.inf], np.nan)


def make_labels(df: pd.DataFrame, horizon: int = 8) -> pd.DataFrame:
    """Labels for training: forward return and direction over ``horizon`` bars.

    ``fwd_ret`` is the *label* (future) — never include it in features.
    """
    close = df["close"]
    fwd_ret = close.shift(-horizon) / close - 1.0
    direction = pd.Series(np.where(fwd_ret > 0, 1, 0), index=df.index)
    direction[fwd_ret.isna()] = np.nan
    fwd_vol = np.log(close).diff().rolling(horizon).std(ddof=0).shift(-horizon)
    return pd.DataFrame(
        {"fwd_ret": fwd_ret, "direction": direction, "fwd_vol": fwd_vol},
        index=df.index,
    )
