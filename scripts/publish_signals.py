#!/usr/bin/env python3
"""Publish the latest live market report to Cloudflare D1 (run by the scheduled workflow).

    python -m scripts.publish_signals --dry-run --out signals.sql   # build the SQL only
    python -m scripts.publish_signals                               # build and apply to D1

Needs CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID in the environment when applying.
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Publish live signals to Cloudflare D1")
    ap.add_argument("--dry-run", action="store_true", help="write the SQL to --out and do not touch D1")
    ap.add_argument("--out", type=Path, default=None, help="SQL file path (default: a temporary file)")
    args = ap.parse_args(argv)

    out = args.out or Path(tempfile.mkstemp(suffix=".sql")[1])
    engine = MarketEngine(AppConfig(), prefer_live=True)
    try:
        result = publish(engine, out=out, dry_run=args.dry_run)
    except PublishError as exc:
        print(f"publish failed: {exc}", file=sys.stderr)
        return 1
    mode = "dry run, SQL written" if result["dry_run"] else "published to D1"
    print(f"{mode}: source={result['data_source']} pairs={result['pairs']} "
          f"statements={result['statements']} bytes={result['bytes']} generated_at={result['generated_at']:.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
