"""Post new or changed trade signals to the private Telegram channel, straight from the publish job.

This is the same logic as ``worker/src/telegram.js``: the same message text, the same fingerprint,
and the same dedupe state in the ``telegram_state`` table of D1. The Worker's cron and this module
therefore share one state, so a signal is posted once per change, whichever side sends it first.

Only the live report that was just published is posted. The Telegram token is only ever sent to
api.telegram.org, and errors never include it.
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional

ACTIVE_VERDICTS = frozenset({"BUY NOW", "SELL NOW"})
STATE_KEY = "posted"
MAX_TEXT = 4000  # Telegram's limit is 4096 characters per message.
DISCLAIMER = "Signals only. Do not trade: this channel generates signals and a risk matrix."
API_ORIGIN = "https://api.telegram.org"
CREATE_STATE_TABLE = (
    "CREATE TABLE IF NOT EXISTS telegram_state (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL)"
)


class TelegramError(RuntimeError):
    """Telegram refused a request. The message never contains the bot token."""


def js_str(value: Any) -> str:
    """Render a value the way JavaScript's String() does for the cases used here."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if math.isfinite(value) and value.is_integer() and abs(value) < 1e21:
            return str(int(value))
        return repr(value)
    return str(value)


def js_round(value: float) -> int:
    """JavaScript's Math.round: halves round up, not to even."""
    return math.floor(value + 0.5)


def fingerprint(item: dict[str, Any]) -> str:
    """The verdict, the entry and the exit orders. Matches the Worker's fingerprint()."""
    exits = ",".join(
        f"{js_str(order.get('label'))}:{'' if order.get('price') is None else js_str(order.get('price'))}"
        for order in (item.get("exit_orders") or [])
    )
    return "|".join([js_str(item.get("verdict")), js_str(item.get("entry")), exits])


def uid_of(item: dict[str, Any]) -> str:
    return js_str(item.get("uid") or item.get("symbol"))


def select_changes(instructions: Iterable[dict[str, Any]], previous: dict[str, str]) -> tuple[list[dict], dict[str, str]]:
    """The active signals whose fingerprint differs from the last posted one, and the next state."""
    active = [item for item in instructions if item.get("verdict") in ACTIVE_VERDICTS]
    next_state: dict[str, str] = {}
    to_post: list[dict[str, Any]] = []
    for item in active:
        uid = uid_of(item)
        fp = fingerprint(item)
        next_state[uid] = fp
        if previous.get(uid) != fp:
            to_post.append(item)
    return to_post, next_state


def format_signal(item: dict[str, Any], generated_at: float) -> str:
    """One message per signal. Matches the Worker's formatSignal() line for line."""
    when = datetime.fromtimestamp(float(generated_at), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    if "p_target_first" in item:
        p_value = item.get("p_target_first") or 0
        pct = f" · P(target first) {js_round(float(p_value) * 100)}%"
    else:
        pct = ""
    bars = f" · expected {js_str(item.get('expected_bars'))} bars" if item.get("expected_bars") else ""
    confidence = item.get("confidence")
    confidence_text = "n/a" if confidence is None else js_str(confidence)
    lines = [
        f"{js_str(item.get('verdict'))} {js_str(item.get('display') or item.get('symbol'))}",
        js_str(item.get("one_line") or ""),
        f"confidence {confidence_text}{pct}{bars}",
        f"sent {when} UTC",
        "",
        DISCLAIMER,
    ]
    text = "\n".join(lines)
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…"


def send_message(token: str, chat_id: str, text: str, timeout: float = 30.0) -> dict[str, Any]:
    """Send one message through the Bot API. The token is only in the request URL, never in errors."""
    url = f"{API_ORIGIN}/bot{token}/sendMessage"
    body = json.dumps({"chat_id": chat_id, "text": text, "disable_web_page_preview": True}).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
            status = response.status
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            payload = {}
        status = exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TelegramError(f"could not reach Telegram: {type(exc).__name__}") from None
    if status != 200 or payload.get("ok") is not True:
        description = payload.get("description") if isinstance(payload.get("description"), str) else f"HTTP {status}"
        raise TelegramError(f"Telegram rejected the message: {description}")
    return payload


RunSql = Callable[[str], list[dict[str, Any]]]
Send = Callable[[str, str, str], dict[str, Any]]


def load_state(run_sql: RunSql) -> dict[str, str]:
    run_sql(CREATE_STATE_TABLE)
    rows = run_sql(f"SELECT value FROM telegram_state WHERE key = '{STATE_KEY}'")
    if not rows:
        return {}
    try:
        state = json.loads(rows[0].get("value") or "{}")
    except (ValueError, TypeError):
        return {}
    return state if isinstance(state, dict) else {}


def save_state(run_sql: RunSql, state: dict[str, str], now: float) -> None:
    value = json.dumps(state, separators=(",", ":"), ensure_ascii=False).replace("'", "''")
    run_sql(
        "INSERT OR REPLACE INTO telegram_state (key, value, updated_at) VALUES "
        f"('{STATE_KEY}', '{value}', {float(now)!r})"
    )


def post_new_signals(
    report: dict[str, Any],
    *,
    token: str,
    chat_id: str,
    run_sql: RunSql,
    send: Optional[Send] = None,
    now: Optional[float] = None,
) -> dict[str, Any]:
    """Post every new or changed active signal once. Each message is recorded as soon as Telegram accepts it."""
    send = send or (lambda t, c, text: send_message(t, c, text))
    now = float(now) if now is not None else float(report["generated_at"])
    generated_at = float(report["generated_at"])
    instructions = report.get("instructions") or []

    previous = load_state(run_sql)
    to_post, next_state = select_changes(instructions, previous)
    state = {uid: fp for uid, fp in previous.items() if uid in next_state}
    posted = 0
    for item in to_post:
        uid = uid_of(item)
        send(token, chat_id, format_signal(item, generated_at))
        state[uid] = next_state[uid]
        save_state(run_sql, state, now)
        posted += 1
    if not to_post:
        save_state(run_sql, state, now)
    return {"posted": posted, "active": len(next_state)}
