#!/usr/bin/env python3
"""CLI: scan the entire FX market and print signals + insights.

    python -m scripts.scan               # full market scan
    python -m scripts.scan --json out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fxbot.config import AppConfig
from fxbot.engine import MarketEngine


def main() -> None:
    ap = argparse.ArgumentParser(description="FX market scanner")
    ap.add_argument("--json", type=Path, help="write full report JSON to this path")
    ap.add_argument("--live", action="store_true", help="try live data providers first")
    ap.add_argument("--equity", type=float, default=None, help="account equity")
    ap.add_argument("--risk", type=float, default=None, help="risk per trade in %%")
    args = ap.parse_args()

    cfg = AppConfig()
    if args.equity:
        cfg.risk.equity = args.equity
    if args.risk:
        cfg.risk.risk_per_trade_pct = args.risk

    engine = MarketEngine(cfg, prefer_live=args.live)
    report = engine.scan()

    d = report.to_dict()
    print(f"\n=== FX MARKET SCAN · source: {d['data_source']} · {d['timeframe']} ===")
    s = d["summary"]
    print(f"scanned {s['pairs_scanned']} instruments | "
          f"{s['buy_signals']} BUY · {s['sell_signals']} SELL · "
          f"{s['watchlist_count']} watch | mood: {s['market_mood']} "
          f"| breadth {d['breadth']:.0%} | DXY {d['dollar_index']:.1f}")

    print("\n--- CURRENCY STRENGTH (strongest → weakest) ---")
    for row in d["currency_strength"]:
        bar = "+" * int(abs(row["score"]) / 5)
        sign = "▲" if row["score"] >= 0 else "▼"
        print(f"  {row['currency']:4} {row['score']:+7.1f} {sign} {bar}")

    print("\n--- ACTIVE SIGNALS ---")
    hdr = f"{'PAIR':9} {'SIDE':5} {'CONF':5} {'REGIME':10} {'PRICE':>10} {'ENTRY':>10} {'TP':>10} {'SL':>10} {'R:R':>5} {'LOTS':>7} {'RISK$':>8}"
    print(hdr)
    print("-" * len(hdr))
    for p in d["signals"]:
        print(f"{p['display']:9} {p['side']:5} {p['confidence']:.0%}   {p['regime']:10} "
              f"{p['price']:10.5g} {p['entry']:10.5g} {p['target']:10.5g} {p['stop']:10.5g} "
              f"{p['risk_reward']:5.2f} {p['lots']:7.2f} {p['risk_amount']:8.2f}")

    print("\n--- INSIGHTS ---")
    for line in d["insights"]:
        print(f"  • {line}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(d, indent=2, default=str))
        print(f"\nfull report → {args.json}")


if __name__ == "__main__":
    main()
