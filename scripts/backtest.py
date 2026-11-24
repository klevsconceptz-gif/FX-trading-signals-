#!/usr/bin/env python3
"""CLI: backtest the signal engine on one symbol.

    python -m scripts.backtest --symbol EURUSD
    python -m scripts.backtest --symbol GBPJPY --equity 25000 --risk 1.0
    python -m scripts.backtest --symbol EURUSD --live --json-out result.json

``--json-out`` writes the same JSON shape the Backtest Lab reads. ``--live`` uses live hourly bars
(the same source as the signal engine); without it the synthetic market is used, as before.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fxbot.backtest import run_backtest
from fxbot.config import AppConfig
from fxbot.data import MarketData
from fxbot.universe import BY_SYMBOL

# The Lab keeps the last trades and at most about 400 curve points, so the stored result stays small.
TRADES_KEPT = 80
CURVE_POINTS = 400


def json_safe(value: Any) -> Any:
    """Convert numpy/pandas values to plain JSON types. NaN and infinity become None."""
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        value = value.item()  # numpy scalar -> Python scalar
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return str(value)


def build_payload(symbol: str, df, result, *, equity: float, risk: float, bars: int,
                  data_source: str, run_at: str | None = None) -> dict[str, Any]:
    """Build the result in the shape the Lab reads (the same shape as the self-hosted /api/backtest)."""
    curve = result.equity_curve
    step = max(1, len(curve) // CURVE_POINTS)
    sampled = curve.iloc[::step]
    return {
        "symbol": symbol,
        "run_at": run_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_source": data_source,
        "params": {
            "bars": bars,
            "bars_used": int(len(df)),
            "equity": float(equity),
            "risk_per_trade_pct": float(risk),
        },
        "metrics": json_safe(result.metrics),
        "equity_curve": {
            "t": [int(ts.timestamp()) for ts in sampled.index],
            "v": [json_safe(round(float(v), 2)) for v in sampled],
        },
        "trades": [
            {
                "side": t.side,
                "signal_time": str(t.signal_time),
                "entry_time": str(t.entry_time),
                "entry": json_safe(round(float(t.entry), 6)),
                "exit_time": str(t.exit_time),
                "exit": json_safe(round(float(t.exit), 6)),
                "stop": json_safe(round(float(t.stop), 6)),
                "target": json_safe(round(float(t.target), 6)),
                "r_multiple": json_safe(round(float(t.r_multiple), 2)),
                "pnl": json_safe(round(float(t.pnl), 2)),
                "exit_reason": t.exit_reason,
            }
            for t in result.trades[-TRADES_KEPT:]
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="FX signal-engine backtester")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--bars", type=int, default=3000)
    ap.add_argument("--equity", type=float, default=10_000.0)
    ap.add_argument("--risk", type=float, default=0.75, help="risk per trade %%")
    ap.add_argument("--live", action="store_true", help="use live hourly bars instead of the synthetic market")
    ap.add_argument("--json-out", help="write the result as JSON to this file instead of printing a table")
    args = ap.parse_args()

    symbol = args.symbol.upper()
    if symbol not in BY_SYMBOL:
        raise SystemExit(f"unknown symbol {symbol} — try {', '.join(list(BY_SYMBOL)[:8])} …")

    cfg = AppConfig()
    cfg.data.bars = args.bars
    cfg.risk.equity = args.equity
    cfg.risk.risk_per_trade_pct = args.risk

    data = MarketData(cfg.data)
    history = data.load(prefer_live=args.live)
    df = history[symbol]
    result = run_backtest(symbol, df, cfg, equity=args.equity, conversion_history=history)

    if args.json_out:
        payload = build_payload(symbol, df, result, equity=args.equity, risk=args.risk, bars=args.bars,
                                data_source=data.source_label)
        text = json.dumps(payload, allow_nan=False, separators=(",", ":"))
        Path(args.json_out).write_text(text, encoding="utf-8")
        print(f"wrote {args.json_out} ({len(text)} bytes) · {symbol} · {len(df)} bars · {data.source_label}")
        return

    print(f"\n=== BACKTEST {symbol} · {len(df)} bars · equity ${args.equity:,.0f} · "
          f"risk {args.risk}%/trade ===\n")
    m = result.metrics
    for k, v in m.items():
        print(f"  {k:22} {v}")

    if result.trades:
        print(f"\n  last {min(15, len(result.trades))} trades:")
        print(f"  {'SIDE':5} {'ENTRY TIME':22} {'ENTRY':>10} {'EXIT':>10} {'R':>6} {'P&L':>10}  REASON")
        for t in result.trades[-15:]:
            print(f"  {t.side:5} {t.entry_time!s:22} {t.entry:10.5g} {t.exit:10.5g} "
                  f"{t.r_multiple:6.2f} {t.pnl:10.2f}  {t.exit_reason}")


if __name__ == "__main__":
    main()
