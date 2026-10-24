"""Publish the latest market report to Cloudflare D1 for the Worker to serve.

The Python engine takes tens of seconds per scan, so it cannot run inside a Worker request. A
scheduled job runs this module, and the Worker only reads the rows it writes to ``signal_cache``.

Rows (one per key, all carrying the same ``run_id``):

* ``pair:<SYMBOL>``: the deep-dive payload for each instrument, written first;
* ``instructions``: the distilled command stream;
* ``scan``: the full market report. It is written last and acts as the commit marker. The Worker
  trusts the other rows only when their ``run_id`` matches the ``scan`` row.

Only live market data is published. A synthetic fallback never reaches the database, so the
last live snapshot stays in place until the next successful run.
"""

from __future__ import annotations

import json
import math
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .universe import UNIVERSE

# D1 limits one SQL statement to 100 KB. Stay well below it.
MAX_STATEMENT_BYTES = 90_000
DATABASE = "klev"
WRANGLER_CONFIG = "worker/wrangler.jsonc"


class PublishError(RuntimeError):
    """The snapshot must not be published. The previous snapshot stays in place."""


def _clean(value: Any) -> Any:
    """Make a report JSON-safe: no NaN/inf, numpy scalars converted, dict keys as strings."""
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, int):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "item"):  # numpy scalar
        return _clean(value.item())
    raise TypeError(f"cannot publish value of type {type(value).__name__}")


def sql_text(value: str) -> str:
    """Quote a Python string as a SQL literal."""
    return "'" + value.replace("'", "''") + "'"


def upsert_statement(key: str, run_id: int, generated_at: float, data_source: str, payload: Any) -> str:
    body = json.dumps(_clean(payload), separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    statement = (
        "INSERT OR REPLACE INTO signal_cache (key, run_id, generated_at, data_source, payload) VALUES ("
        f"{sql_text(key)}, {int(run_id)}, {float(generated_at)!r}, {sql_text(data_source)}, {sql_text(body)});\n"
    )
    size = len(statement.encode("utf-8"))
    if size > MAX_STATEMENT_BYTES:
        raise PublishError(f"'{key}' is {size} bytes, over the {MAX_STATEMENT_BYTES}-byte statement limit")
    return statement


def collect(engine: Any) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Run one scan on live data and return (report, pair payloads). Refuses non-live data."""
    report = engine.scan(reload=True).to_dict()
    source = str(report.get("data_source", ""))
    if not source.startswith("live"):
        raise PublishError(f"refusing to publish: the data source is '{source or 'unknown'}', not live")
    pairs: dict[str, dict[str, Any]] = {}
    for instrument in UNIVERSE:
        payload = engine.pair_report(instrument.symbol)
        if payload is not None:
            pairs[instrument.symbol] = payload
    if not pairs:
        raise PublishError("no pair data was produced by the scan")
    return report, pairs


def build_sql(report: dict[str, Any], pairs: dict[str, dict[str, Any]], run_id: Optional[int] = None) -> str:
    """The full SQL for one publish, in commit order: pairs, instructions, then the scan row."""
    run_id = int(run_id if run_id is not None else time.time() * 1000)
    generated_at = float(report["generated_at"])
    source = str(report["data_source"])
    statements = [
        upsert_statement(f"pair:{symbol}", run_id, generated_at, source, payload)
        for symbol, payload in sorted(pairs.items())
    ]
    statements.append(upsert_statement(
        "instructions", run_id, generated_at, source,
        {"generated_at": generated_at, "data_source": source, "instructions": report.get("instructions", [])},
    ))
    statements.append(upsert_statement("scan", run_id, generated_at, source, report))
    return "".join(statements)


def execute_remote(sql_file: Path) -> None:
    """Apply the SQL to the remote D1 database through Wrangler (it reads the API token from the env)."""
    command = ["npx", "wrangler", "d1", "execute", DATABASE, "--remote",
               "--file", str(sql_file), "--config", WRANGLER_CONFIG]
    proc = subprocess.run(command, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        raise PublishError("wrangler d1 execute failed: " + " | ".join(tail)[:500])


def publish(engine: Any, *, out: Path, dry_run: bool = False) -> dict[str, Any]:
    """Collect, build, and either write the SQL to ``out`` (dry run) or apply it to D1."""
    report, pairs = collect(engine)
    sql = build_sql(report, pairs)
    out.write_text(sql, encoding="utf-8")
    if not dry_run:
        execute_remote(out)
    return {
        "data_source": report["data_source"],
        "generated_at": report["generated_at"],
        "pairs": len(pairs),
        "statements": sql.count(";\n"),
        "bytes": len(sql.encode("utf-8")),
        "dry_run": dry_run,
    }
