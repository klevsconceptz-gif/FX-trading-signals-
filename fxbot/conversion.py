"""Account-currency conversion helpers for FX risk and P&L calculations.

Rates are built from the available FX-pair history. Each edge is interpreted as
"units of quote currency per one unit of base currency"; an inverse edge uses
its reciprocal. Conversion paths are shortest-path BFS routes over the tracked
FX universe (metals are deliberately excluded).
"""

from __future__ import annotations

from collections import deque
from typing import Optional

import numpy as np
import pandas as pd

from .universe import BY_SYMBOL


def _utc_index(index: pd.Index) -> pd.DatetimeIndex:
    result = pd.DatetimeIndex(index)
    return result.tz_localize("UTC") if result.tz is None else result.tz_convert("UTC")


def _conversion_path(
    history: dict[str, pd.DataFrame], source: str, target: str
) -> Optional[list[tuple[str, bool]]]:
    """Return ``(symbol, inverse)`` edges from source to target, if available."""
    if source == target:
        return []

    graph: dict[str, list[tuple[str, str, bool]]] = {}
    for symbol, frame in history.items():
        instrument = BY_SYMBOL.get(symbol)
        if instrument is None or instrument.kind != "fx" or frame.empty or "close" not in frame:
            continue
        if not frame["close"].dropna().size:
            continue
        graph.setdefault(instrument.base, []).append((instrument.quote, symbol, False))
        graph.setdefault(instrument.quote, []).append((instrument.base, symbol, True))

    queue = deque([(source, [])])
    visited = {source}
    while queue:
        currency, path = queue.popleft()
        for neighbor, symbol, inverse in sorted(graph.get(currency, [])):
            if neighbor in visited:
                continue
            next_path = [*path, (symbol, inverse)]
            if neighbor == target:
                return next_path
            visited.add(neighbor)
            queue.append((neighbor, next_path))
    return None


def conversion_rate_series(
    history: dict[str, pd.DataFrame],
    source: str,
    target: str,
    index: pd.Index,
    *,
    lag_bars: int = 0,
) -> pd.Series:
    """Convert one source-currency unit into target-currency units over ``index``.

    The latest available rate at or before each target timestamp is forward
    filled. ``lag_bars=1`` makes the conversion observable before the target bar
    opens, which is appropriate for a backtest entry at that bar's open.
    """
    source, target = source.upper(), target.upper()
    target_index = pd.DatetimeIndex(index)
    if source == target:
        return pd.Series(1.0, index=target_index, dtype=float)
    if lag_bars < 0:
        raise ValueError("lag_bars must be non-negative")

    path = _conversion_path(history, source, target)
    if path is None:
        raise ValueError(f"no FX conversion path from {source} to {target}")

    target_utc = _utc_index(target_index)
    result = pd.Series(1.0, index=target_index, dtype=float)
    for symbol, inverse in path:
        close = history[symbol]["close"].dropna().astype(float).copy()
        close.index = _utc_index(close.index)
        close = close[~close.index.duplicated(keep="last")].sort_index()
        aligned = close.reindex(target_utc, method="ffill")
        if lag_bars:
            aligned = aligned.shift(lag_bars)
        aligned.index = target_index
        if inverse:
            aligned = 1.0 / aligned.replace(0.0, np.nan)
        result *= aligned
    return result.replace([np.inf, -np.inf], np.nan)


def latest_conversion_rate(
    history: dict[str, pd.DataFrame], source: str, target: str
) -> float:
    """Return the latest available conversion rate, failing closed if missing."""
    frames = [frame for frame in history.values() if not frame.empty]
    if not frames:
        raise ValueError("market history is empty; cannot convert currencies")
    latest_index = max(frame.index[-1] for frame in frames)
    rate = conversion_rate_series(history, source, target, pd.DatetimeIndex([latest_index])).iloc[0]
    if not np.isfinite(rate) or rate <= 0:
        raise ValueError(f"no current FX conversion rate from {source} to {target}")
    return float(rate)
