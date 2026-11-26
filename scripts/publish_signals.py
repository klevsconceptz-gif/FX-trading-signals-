#!/usr/bin/env python3
"""Publish the latest live market report to Cloudflare D1 (run by the scheduled workflow).

    python -m scripts.publish_signals --dry-run --out signals.sql   # build the SQL only
    python -m scripts.publish_signals                               # build and apply to D1

Needs CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID in the environment when applying.
With --report-out, the published report is saved as JSON. scripts.post_telegram posts from that file
in a separate workflow step, so a Telegram failure cannot fail the D1 publish.
The script never prints a secret.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fxbot.config import AppConfig
from fxbot.engine import MarketEngine
from fxbot.publish import PublishError, publish


def fail(message: str) -> None:
    """Print the reason to the log and as a GitHub annotation, so it shows on the run page."""
    print(message, file=sys.stderr)
    print(f"::error title=Publish failed::{message.replace(chr(10), ' ')}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Publish live signals to Cloudflare D1")
    ap.add_argument("--dry-run", action="store_true", help="write the SQL to --out and do not touch D1")
    ap.add_argument("--out", type=Path, default=None, help="SQL file path (default: a temporary file)")
    ap.add_argument("--report-out", type=Path, default=None,
                    help="also save the published report as JSON, for scripts.post_telegram")
    args = ap.parse_args(argv)

    out = args.out or Path(tempfile.mkstemp(suffix=".sql")[1])
    engine = MarketEngine(AppConfig(), prefer_live=True)
    try:
        result = publish(engine, out=out, dry_run=args.dry_run, report_out=args.report_out)
    except PublishError as exc:
        fail(f"publish failed: {exc}")
        return 1
    except Exception as exc:  # report any engine or network error on the run page
        fail(f"publish crashed: {type(exc).__name__}: {str(exc)[:300]}")
        raise
    mode = "dry run, SQL written" if result["dry_run"] else "published to D1"
    print(f"{mode}: source={result['data_source']} pairs={result['pairs']} "
          f"statements={result['statements']} bytes={result['bytes']} generated_at={result['generated_at']:.0f}")
    for symbol in result.get("unavailable", []):
        # shown on the run page: the snapshot is still published, with this instrument marked unavailable
        print(f"::warning title=Signal unavailable::{symbol} had no live data this run and is marked unavailable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
