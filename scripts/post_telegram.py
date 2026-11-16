#!/usr/bin/env python3
"""Post new or changed active signals to the private Telegram channel (run after the D1 publish).

    python -m scripts.post_telegram --report "$RUNNER_TEMP/signals-report.json"

Reads the report that scripts.publish_signals saved after its D1 write, so the posts match the
snapshot in D1. Needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID; without them it does nothing.
It exits 1 on a Telegram error, which the workflow step marks as failed without failing the job.
The script never prints a secret.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fxbot.publish import run_remote_sql
from fxbot.telegram import post_new_signals


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Post new or changed signals to Telegram")
    ap.add_argument("--report", type=Path, required=True, help="report JSON saved by scripts.publish_signals")
    args = ap.parse_args(argv)

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("telegram: skipped (not configured)")
        return 0

    report = json.loads(args.report.read_text(encoding="utf-8"))
    try:
        result = post_new_signals(report, token=token, chat_id=chat_id, run_sql=run_remote_sql)
    except Exception as exc:  # report the reason on the run page; the token is never in the message
        message = f"{type(exc).__name__}: {str(exc)[:300]}"
        print(f"telegram: NOT posted: {message}", file=sys.stderr)
        print(f"::error title=Telegram post failed::{message.replace(chr(10), ' ')}")
        return 1
    print(f"telegram: posted={result['posted']} active={result['active']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
