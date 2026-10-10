#!/usr/bin/env python3
"""CLI: train the ML forecasters across the universe and report accuracy.

    python -m scripts.train
    python -m scripts.train --symbols EURUSD,GBPJPY
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fxbot.config import AppConfig
from fxbot.data import MarketData
from fxbot.ml import MarketForecaster


def main() -> None:
    ap = argparse.ArgumentParser(description="Train FX direction/volatility models")
    ap.add_argument("--symbols", type=str, default=None,
                    help="comma-separated subset (default: whole universe)")
    ap.add_argument("--horizon", type=int, default=None, help="prediction horizon in bars")
    args = ap.parse_args()

    cfg = AppConfig()
    if args.horizon:
        cfg.ml.horizon = args.horizon
    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None

    history = MarketData(cfg.data).load(symbols)
    forecaster = MarketForecaster(cfg.ml)
    forecasts = forecaster.run(history)

    print(f"\n=== ML TRAINING · {len(forecasts)} models · horizon {cfg.ml.horizon} bars ===\n")
    print(f"  {'PAIR':9} {'P(up)':>7} {'EXP MOVE':>10} {'VOL x':>7} {'OOS ACC':>8} {'BARS':>7}")
    for symbol, f in sorted(forecasts.items()):
        acc = f"{f.accuracy:.1%}" if f.accuracy is not None else "—"
        print(f"  {symbol:9} {f.p_up:7.2f} {f.expected_move_pct * 100:9.3f}% "
              f"{f.vol_ratio:7.2f} {acc:>8} {f.trained_bars:7d}")
    accs = [f.accuracy for f in forecasts.values() if f.accuracy is not None]
    if accs:
        print(f"\n  average out-of-sample direction accuracy: {sum(accs) / len(accs):.1%}")


if __name__ == "__main__":
    main()
