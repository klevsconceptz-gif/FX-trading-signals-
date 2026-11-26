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

from .universe import BY_SYMBOL, UNIVERSE

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


# A scan can come back synthetic (a provider rate-limited an FX pair) or without an optional
# metal (Yahoo drops XAUUSD=X now and then). Retry before giving up, so the snapshot stays live.
UNAVAILABLE_RETRY_ATTEMPTS = 3
UNAVAILABLE_RETRY_SECONDS = 60.0


def _missing_reasons(engine: Any) -> dict[str, str]:
    """Why each optional instrument was left out of the last live load, keyed by symbol."""
    reasons: dict[str, str] = {}
    data = getattr(engine, "data", None)
    for note in getattr(data, "excluded", None) or []:
        symbol, _, rest = str(note).partition(" (")
        if symbol:
            reasons[symbol] = rest.rstrip(")") or "no live data"
    return reasons


def _unavailable_instruction(symbol: str, reason: str) -> dict[str, Any]:
    ins = BY_SYMBOL[symbol]
    name = ins.description.split(" / ")[0] or ins.display
    label = f"{symbol} ({name})"
    return {
        "uid": symbol, "symbol": symbol, "display": ins.display,
        "verdict": "UNAVAILABLE", "action": "wait",
        "confidence": None, "entry": None,
        "prediction": (f"No signal for {label}: live data could not be loaded ({reason}). "
                       "It is retried on every scheduled publish."),
        "p_target_first": 0.0, "expected_bars": 0,
        "one_line": f"{label}: live data unavailable, so no buy or sell signal. ({reason})",
        "exit_orders": [], "actions": [],
    }


def collect(engine: Any, *, retry_attempts: Optional[int] = None,
            retry_seconds: Optional[float] = None) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Run one scan on live data and return (report, pair payloads).

    Refuses non-live data after the retries. An optional instrument that is still missing is
    published as ``unavailable``: its pair row and an UNAVAILABLE instruction say so, so the
    page never silently drops gold or silver.
    """
    attempts = max(1, UNAVAILABLE_RETRY_ATTEMPTS if retry_attempts is None else retry_attempts)
    delay = UNAVAILABLE_RETRY_SECONDS if retry_seconds is None else retry_seconds
    for attempt in range(1, attempts + 1):
        report = engine.scan(reload=True).to_dict()
        source = str(report.get("data_source", ""))
        last = attempt == attempts
        if not source.startswith("live"):
            if last:
                raise PublishError(f"refusing to publish: the data source is '{source or 'unknown'}', not live")
            time.sleep(delay)
            continue
        pairs: dict[str, dict[str, Any]] = {}
        for instrument in UNIVERSE:
            payload = engine.pair_report(instrument.symbol)
            if payload is not None:
                pairs[instrument.symbol] = payload
        if not pairs:
            if last:
                raise PublishError("no pair data was produced by the scan")
            time.sleep(delay)
            continue
        missing = [ins.symbol for ins in UNIVERSE if ins.symbol not in pairs]
        if missing and not last:
            time.sleep(delay)
            continue
        reasons = _missing_reasons(engine)
        unavailable = {symbol: reasons.get(symbol, "no live data") for symbol in missing}
        _add_unavailable(report, pairs, unavailable)
        return report, pairs
    raise PublishError("refusing to publish: no live snapshot")  # unreachable; keeps type checkers calm


def _add_unavailable(report: dict[str, Any], pairs: dict[str, dict[str, Any]],
                     unavailable: dict[str, str]) -> None:
    """Record instruments with no live data as explicit unavailable rows, never as silent gaps."""
    report["unavailable"] = [
        {"symbol": symbol, "display": BY_SYMBOL[symbol].display, "reason": reason}
        for symbol, reason in sorted(unavailable.items())
    ]
    instructions = report.setdefault("instructions", [])
    for symbol, reason in sorted(unavailable.items()):
        pairs[symbol] = {
            "symbol": symbol, "display": BY_SYMBOL[symbol].display, "status": "unavailable",
            "reason": reason, "generated_at": report["generated_at"],
        }
        instructions.append(_unavailable_instruction(symbol, reason))


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


def run_remote_sql(sql: str) -> list[dict[str, Any]]:
    """Run one SQL statement on the remote D1 and return the result rows (empty for writes)."""
    command = ["npx", "wrangler", "d1", "execute", DATABASE, "--remote",
               "--command", sql, "--json", "--config", WRANGLER_CONFIG]
    proc = subprocess.run(command, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        raise PublishError("wrangler d1 execute failed: " + " | ".join(tail)[:500])
    # Wrangler may print notices before the JSON; the JSON array starts at a line that begins with "[".
    text = proc.stdout
    start = 0
    offset = 0
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("["):
            start = offset
            break
        offset += len(line)
    try:
        statements = json.loads(text[start:])
    except ValueError as exc:
        raise PublishError("wrangler d1 execute returned unreadable output") from exc
    rows: list[dict[str, Any]] = []
    for statement in statements if isinstance(statements, list) else []:
        rows.extend(statement.get("results") or [])
    return rows


def publish(engine: Any, *, out: Path, dry_run: bool = False,
            telegram_token: Optional[str] = None, telegram_chat_id: Optional[str] = None,
            report_out: Optional[Path] = None) -> dict[str, Any]:
    """Collect, build, and either write the SQL to ``out`` (dry run) or apply it to D1.

    After a successful write, new or changed signals are posted to the Telegram channel when a
    token and chat id are given. A Telegram failure is reported in the result and never fails
    the publish, because the snapshot is already in D1.
    """
    report, pairs = collect(engine)
    sql = build_sql(report, pairs)
    out.write_text(sql, encoding="utf-8")
    if not dry_run:
        execute_remote(out)
    if report_out is not None:
        # the exact report that was just published; the Telegram step posts from this file
        report_out.write_text(json.dumps(_clean(report), ensure_ascii=False, allow_nan=False), encoding="utf-8")
    result: dict[str, Any] = {
        "data_source": report["data_source"],
        "generated_at": report["generated_at"],
        "pairs": len(pairs),
        "statements": sql.count(";\n"),
        "bytes": len(sql.encode("utf-8")),
        "dry_run": dry_run,
        "unavailable": [item["symbol"] for item in report.get("unavailable", [])],
    }
    if not dry_run and telegram_token and telegram_chat_id:
        from .telegram import post_new_signals
        try:
            result["telegram"] = post_new_signals(
                report, token=telegram_token, chat_id=telegram_chat_id, run_sql=run_remote_sql)
        except Exception as exc:  # the snapshot is already published; report and move on
            result["telegram"] = {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    return result
