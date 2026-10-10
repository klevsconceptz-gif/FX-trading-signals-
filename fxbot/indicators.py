"""Technical-analysis indicator library (vectorized pandas, no C deps).

Every function takes a DataFrame with columns ``open, high, low, close``
(or a price Series) and returns one or more Series aligned to the input.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# moving averages / trend
# ---------------------------------------------------------------------------

def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=window).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def wma(series: pd.Series, window: int) -> pd.Series:
    weights = np.arange(1, window + 1, dtype=float)

    def _w(x: np.ndarray) -> float:
        return float(np.dot(x, weights) / weights.sum())

    return series.rolling(window).apply(_w, raw=True)


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "hist": hist})


def adx(df: pd.DataFrame, window: int = 14) -> pd.DataFrame:
    """Average Directional Index with +DI / -DI (Wilder smoothing)."""
    high, low, close = df["high"], df["low"], df["close"]
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr = true_range(df)
    alpha = 1.0 / window
    atr_ = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=alpha, adjust=False).mean() / atr_.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=alpha, adjust=False).mean() / atr_.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_ = dx.ewm(alpha=alpha, adjust=False).mean()
    return pd.DataFrame({"adx": adx_, "plus_di": plus_di, "minus_di": minus_di})


# ---------------------------------------------------------------------------
# volatility
# ---------------------------------------------------------------------------

def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr


def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1.0 / window, adjust=False).mean()


def bollinger(series: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    mid = sma(series, window)
    std = series.rolling(window, min_periods=window).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    pct_b = (series - lower) / (upper - lower).replace(0, np.nan)
    bandwidth = (upper - lower) / mid.replace(0, np.nan)
    return pd.DataFrame(
        {"mid": mid, "upper": upper, "lower": lower, "pct_b": pct_b, "bandwidth": bandwidth}
    )


def keltner(df: pd.DataFrame, window: int = 20, atr_mult: float = 2.0) -> pd.DataFrame:
    mid = ema(df["close"], window)
    rng = atr(df, window) * atr_mult
    return pd.DataFrame({"mid": mid, "upper": mid + rng, "lower": mid - rng})


def donchian(df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    upper = df["high"].rolling(window, min_periods=window).max()
    lower = df["low"].rolling(window, min_periods=window).min()
    return pd.DataFrame({"upper": upper, "lower": lower, "mid": (upper + lower) / 2.0})


def realized_vol(series: pd.Series, window: int = 20) -> pd.Series:
    return np.log(series).diff().rolling(window).std(ddof=0) * np.sqrt(252.0)


# ---------------------------------------------------------------------------
# momentum / oscillators
# ---------------------------------------------------------------------------

def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / window, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # flat series (no gains and no losses) is neutral
    out = out.mask((avg_gain == 0) & (avg_loss == 0), 50.0)
    # avg_loss == 0 with gains → all-up market → 100
    return out.fillna(100.0)


def stochastic(df: pd.DataFrame, k_window: int = 14, d_window: int = 3) -> pd.DataFrame:
    low_min = df["low"].rolling(k_window, min_periods=k_window).min()
    high_max = df["high"].rolling(k_window, min_periods=k_window).max()
    denom = (high_max - low_min).replace(0, np.nan)
    k = 100.0 * (df["close"] - low_min) / denom
    d = k.rolling(d_window).mean()
    return pd.DataFrame({"k": k, "d": d})


def cci(df: pd.DataFrame, window: int = 20) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    ma = tp.rolling(window, min_periods=window).mean()
    md = tp.rolling(window).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
    return (tp - ma) / (0.015 * md.replace(0, np.nan))


def roc(series: pd.Series, window: int = 10) -> pd.Series:
    return series.pct_change(window) * 100.0


def momentum(series: pd.Series, window: int = 10) -> pd.Series:
    return series - series.shift(window)


def obv(df: pd.DataFrame, volume: pd.Series | None = None) -> pd.Series:
    if volume is None:
        volume = pd.Series(1.0, index=df.index)
    direction = np.sign(df["close"].diff()).fillna(0.0)
    return (direction * volume).cumsum()


def ichimoku(df: pd.DataFrame, conv: int = 9, base: int = 26,
             span_b_window: int = 52, displacement: int = 26) -> pd.DataFrame:
    conv_line = (df["high"].rolling(conv).max() + df["low"].rolling(conv).min()) / 2.0
    base_line = (df["high"].rolling(base).max() + df["low"].rolling(base).min()) / 2.0
    span_a = ((conv_line + base_line) / 2.0).shift(displacement)
    span_b = ((df["high"].rolling(span_b_window).max()
               + df["low"].rolling(span_b_window).min()) / 2.0).shift(displacement)
    return pd.DataFrame(
        {"conversion": conv_line, "base": base_line, "span_a": span_a, "span_b": span_b}
    )


# ---------------------------------------------------------------------------
# structure helpers
# ---------------------------------------------------------------------------

def swing_points(series: pd.Series, order: int = 5) -> pd.DataFrame:
    """Detect swing highs / lows (fractal method)."""
    is_high = series == series.rolling(2 * order + 1, center=True).max()
    is_low = series == series.rolling(2 * order + 1, center=True).min()
    return pd.DataFrame({"swing_high": is_high, "swing_low": is_low})


def support_resistance(df: pd.DataFrame, order: int = 5, levels: int = 3) -> dict[str, list[float]]:
    """Most recent swing-based support & resistance levels."""
    sw = swing_points(df["close"], order)
    highs = df.loc[sw["swing_high"], "close"].dropna().tail(levels).tolist()
    lows = df.loc[sw["swing_low"], "close"].dropna().tail(levels).tolist()
    return {"resistance": sorted(highs, reverse=True), "support": sorted(lows)}


# ---------------------------------------------------------------------------
# one-shot feature frame
# ---------------------------------------------------------------------------

def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    """Return the input OHLC augmented with every indicator used downstream."""
    out = df.copy()
    c = out["close"]
    out["ema20"] = ema(c, 20)
    out["ema50"] = ema(c, 50)
    out["ema200"] = ema(c, 200)
    out["sma20"] = sma(c, 20)
    out["rsi14"] = rsi(c, 14)
    m = macd(c)
    out["macd"] = m["macd"]
    out["macd_signal"] = m["signal"]
    out["macd_hist"] = m["hist"]
    b = bollinger(c)
    out["bb_upper"] = b["upper"]
    out["bb_mid"] = b["mid"]
    out["bb_lower"] = b["lower"]
    out["bb_pct_b"] = b["pct_b"]
    out["bb_bandwidth"] = b["bandwidth"]
    out["atr14"] = atr(out, 14)
    a = adx(out)
    out["adx"] = a["adx"]
    out["plus_di"] = a["plus_di"]
    out["minus_di"] = a["minus_di"]
    st = stochastic(out)
    out["stoch_k"] = st["k"]
    out["stoch_d"] = st["d"]
    out["cci20"] = cci(out, 20)
    out["roc10"] = roc(c, 10)
    out["mom10"] = momentum(c, 10)
    d = donchian(out, 20)
    out["donchian_upper"] = d["upper"]
    out["donchian_lower"] = d["lower"]
    out["vol20"] = realized_vol(c, 20)
    return out
