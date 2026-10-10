#!/usr/bin/env python3
"""CLI: backtest the signal engine on one symbol.

    python -m scripts.backtest --symbol EURUSD
    python -m scripts.backtest --symbol GBPJPY --equity 25000 --risk 1.0
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fxbot.backtest import run_backtest
from fxbot.config import AppConfig
from fxbot.data import MarketData
from fxbot.universe import BY_SYMBOL


def main() -> None:
    ap = argparse.ArgumentParser(description="FX signal-engine backtester")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--bars", type=int, default=3000)
    ap.add_argument("--equity", type=float, default=10_000.0)
    ap.add_argument("--risk", type=float, default=0.75, help="risk per trade %%")
    args = ap.parse_args()

    symbol = args.symbol.upper()
    if symbol not in BY_SYMBOL:
        raise SystemExit(f"unknown symbol {symbol} — try {', '.join(list(BY_SYMBOL)[:8])} …")

    cfg = AppConfig()
    cfg.data.bars = args.bars
    cfg.risk.equity = args.equity
    cfg.risk.risk_per_trade_pct = args.risk

    df = MarketData(cfg.data).load([symbol])[symbol]
    result = run_backtest(symbol, df, cfg, equity=args.equity)

    print(f"\n=== BACKTEST {symbol} · {len(df)} bars · equity ${args.equity:,.0f} · "
          f"risk {args.risk}%/trade ===\n")
    m = result.metrics
    for k, v in m.items():
        print(f"  {k:22} {v}")

    if result.trades:
        print(f"\n  last {min(15, len(result.trades))} trades:")
        print(f"  {'SIDE':5} {'ENTRY TIME':22} {'ENTRY':>10} {'EXIT':>10} {'R':>6} {'P&L':>10}  REASON")
        for t in result.trades[-15:]:
            print(f"  {t.side:5} {str(t.entry_time):22} {t.entry:10.5g} {t.exit:10.5g} "
                  f"{t.r_multiple:6.2f} {t.pnl:10.2f}  {t.exit_reason}")


if __name__ == "__main__":
    main()
