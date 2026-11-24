#!/usr/bin/env python3
"""Run the backtest on live market data and store the results in Cloudflare D1 (manual GitHub job).

    python -m scripts.publish_backtests                       # every instrument, default settings
    python -m scripts.publish_backtests --symbols EURUSD,GBPUSD
    python -m scripts.publish_backtests --dry-run --out backtests.sql   # write the SQL, do not touch D1

Needs CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID when writing to D1 (not in --dry-run).
Only live market data is stored. A synthetic market never reaches D1: if the live load is
not available, the job stores nothing and fails. The Lab reads the rows through GET /api/backtest/<symbol>
(paid members only). Each run replaces the previous row for a symbol.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fxbot.backtest import run_backtest  # noqa: E402
from fxbot.config import AppConfig  # noqa: E402
from fxbot.data import MarketData  # noqa: E402
from fxbot.publish import MAX_STATEMENT_BYTES, PublishError, execute_remote, sql_text  # noqa: E402
from fxbot.universe import BY_SYMBOL  # noqa: E402
from scripts.backtest import build_payload  # noqa: E402


def fail(message: str) -> None:
    print(f"::error title=Backtest not stored::{message}", file=sys.stderr)
    raise SystemExit(1)


def upsert_statement(payload: dict) -> str:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    statement = (
        "INSERT OR REPLACE INTO backtest_results (symbol, run_at, data_source, payload, stored_at) VALUES ("
        f"{sql_text(payload['symbol'])}, {sql_text(payload['run_at'])}, {sql_text(payload['data_source'])}, "
        f"{sql_text(body)}, {datetime.now(timezone.utc).timestamp()!r});\n"
    )
    size = len(statement.encode("utf-8"))
    if size > MAX_STATEMENT_BYTES:
        raise PublishError(f"{payload['symbol']}: result is {size} bytes, over the {MAX_STATEMENT_BYTES}-byte statement limit")
    return statement


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Store live backtest results in Cloudflare D1")
    ap.add_argument("--symbols", default="all", help="comma-separated symbols, or 'all'")
    ap.add_argument("--bars", type=int, default=3000)
    ap.add_argument("--equity", type=float, default=10_000.0)
    ap.add_argument("--risk", type=float, default=0.75, help="risk per trade %%")
    ap.add_argument("--dry-run", action="store_true", help="write the SQL to --out and do not touch D1")
    ap.add_argument("--out", help="SQL file path (required with --dry-run; otherwise a temp file)")
    args = ap.parse_args(argv)

    if args.symbols.strip().lower() == "all":
        wanted = list(BY_SYMBOL)
    else:
        wanted = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        unknown = [s for s in wanted if s not in BY_SYMBOL]
        if unknown:
            fail(f"unknown symbol(s): {', '.join(unknown)}")

    cfg = AppConfig()
    cfg.data.bars = args.bars
    cfg.risk.equity = args.equity
    cfg.risk.risk_per_trade_pct = args.risk

    data = MarketData(cfg.data)
    history = data.load(prefer_live=True)
    if not str(data.source_label).startswith("live"):
        fail(f"live market data is not available ({data.source_label}); nothing was stored")

    statements: list[str] = []
    missing: list[str] = []
    for symbol in wanted:
        df = history.get(symbol)
        if df is None or len(df) == 0:
            missing.append(symbol)
            continue
        result = run_backtest(symbol, df, cfg, equity=args.equity, conversion_history=history)
        payload = build_payload(symbol, df, result, equity=args.equity, risk=args.risk, bars=args.bars,
                                data_source=data.source_label)
        statements.append(upsert_statement(payload))
        m = result.metrics
        print(f"{symbol:8} {len(df):6} bars  trades {m.get('trades')}  return {m.get('total_return_pct')}%  "
              f"win rate {m.get('win_rate')}")

    if not statements:
        fail("no instrument returned live data; nothing was stored")

    sql = "".join(statements)
    if args.dry_run:
        out = Path(args.out or "backtests.sql")
        out.write_text(sql, encoding="utf-8")
        print(f"dry run: wrote {len(statements)} statement(s) to {out}")
    else:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "backtests.sql"
            path.write_text(sql, encoding="utf-8")
            execute_remote(path)
        print(f"stored {len(statements)} backtest result(s) in D1 ({data.source_label})")

    if missing:
        print(f"::warning title=No live data::{', '.join(missing)} had no live data; their stored results were not replaced")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
