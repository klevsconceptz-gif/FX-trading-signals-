#!/usr/bin/env python3
"""Publish the latest live market report to Cloudflare D1 (run by the scheduled workflow).

    python -m scripts.publish_signals --dry-run --out signals.sql   # build the SQL only
    python -m scripts.publish_signals                               # build and apply to D1

Needs CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID in the environment when applying.
When TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set, new or changed signals are posted to the
Telegram channel after the publish. A Telegram failure does not fail the publish.
The script never prints a secret.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fxbot.config import AppConfig
from fxbot.engine import MarketEngine
from fxbot.publish import PublishError, publish


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Publish live signals to Cloudflare D1")
    ap.add_argument("--dry-run", action="store_true", help="write the SQL to --out and do not touch D1")
    ap.add_argument("--out", type=Path, default=None, help="SQL file path (default: a temporary file)")
    args = ap.parse_args(argv)

    out = args.out or Path(tempfile.mkstemp(suffix=".sql")[1])
    engine = MarketEngine(AppConfig(), prefer_live=True)
    try:
        result = publish(engine, out=out, dry_run=args.dry_run,
                         telegram_token=os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or None,
                         telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", "").strip() or None)
    except PublishError as exc:
        print(f"publish failed: {exc}", file=sys.stderr)
        return 1
    mode = "dry run, SQL written" if result["dry_run"] else "published to D1"
    print(f"{mode}: source={result['data_source']} pairs={result['pairs']} "
          f"statements={result['statements']} bytes={result['bytes']} generated_at={result['generated_at']:.0f}")
    telegram = result.get("telegram")
    if telegram is None:
        print("telegram: skipped (not configured, or dry run)")
    elif "error" in telegram:
        print(f"telegram: NOT posted: {telegram['error']}", file=sys.stderr)
    else:
        print(f"telegram: posted={telegram['posted']} active={telegram['active']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
