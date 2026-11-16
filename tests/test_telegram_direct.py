"""The publish job posts signals straight to Telegram. These tests use fakes; nothing leaves the machine."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fxbot import publish as publish_module
from fxbot import telegram

GENERATED_AT = 1789000000.0


def item(uid="EURUSD", verdict="BUY NOW", entry=1.08452, price=1.09, **extra):
    base = {
        "uid": uid, "symbol": uid, "display": f"{uid[:3]}/{uid[3:]}", "verdict": verdict,
        "confidence": 0.7234, "entry": entry, "p_target_first": 0.6215, "expected_bars": 12,
        "one_line": "Buy now.", "exit_orders": [{"label": "TP1", "price": price}],
    }
    base.update(extra)
    return base


class FakeD1:
    """A tiny stand-in for the telegram_state table. It understands the three statements the module sends."""

    def __init__(self, value=None):
        self.value = value
        self.statements: list[str] = []

    def __call__(self, sql: str):
        self.statements.append(sql)
        if sql.startswith("SELECT value"):
            return [] if self.value is None else [{"value": self.value}]
        if sql.startswith("INSERT OR REPLACE INTO telegram_state"):
            start = sql.index("VALUES ('posted', '") + len("VALUES ('posted', '")
            end = sql.rindex("', ")
            self.value = sql[start:end].replace("''", "'")
        return []


class FakeTelegram:
    def __init__(self, fail_on=None):
        self.sent: list[str] = []
        self.fail_on = fail_on

    def __call__(self, token, chat_id, text):
        if self.fail_on is not None and len(self.sent) == self.fail_on:
            raise telegram.TelegramError("Telegram rejected the message: Forbidden: bot is not a member")
        assert token == "123:TEST-TOKEN" and chat_id == "-1001234567890"
        self.sent.append(text)
        return {"ok": True}


def report(*items):
    return {"generated_at": GENERATED_AT, "data_source": "live: test", "instructions": list(items)}


def test_format_signal_matches_the_worker_text():
    text = telegram.format_signal(item(), GENERATED_AT)
    assert text.splitlines() == [
        "BUY NOW EUR/USD",
        "Buy now.",
        "confidence 0.7234 · P(target first) 62% · expected 12 bars",
        "sent 2026-09-10 00:26 UTC",
        "",
        telegram.DISCLAIMER,
    ]


def test_fingerprint_matches_the_worker_for_null_prices():
    assert telegram.fingerprint(item(price=None)) == "BUY NOW|1.08452|TP1:"
    assert telegram.fingerprint(item(entry=150)) == "BUY NOW|150|TP1:1.09"


def test_new_then_unchanged_then_changed_signals_are_posted_once_each():
    d1 = FakeD1()
    send = FakeTelegram()
    first = telegram.post_new_signals(report(item()), token="123:TEST-TOKEN", chat_id="-1001234567890",
                                      run_sql=d1, send=send, now=GENERATED_AT)
    assert first == {"posted": 1, "active": 1}

    again = telegram.post_new_signals(report(item()), token="123:TEST-TOKEN", chat_id="-1001234567890",
                                      run_sql=d1, send=send, now=GENERATED_AT)
    assert again == {"posted": 0, "active": 1}
    assert len(send.sent) == 1

    changed = telegram.post_new_signals(report(item(price=1.095)), token="123:TEST-TOKEN",
                                        chat_id="-1001234567890", run_sql=d1, send=send, now=GENERATED_AT)
    assert changed["posted"] == 1
    assert len(send.sent) == 2


def test_a_signal_that_goes_inactive_is_forgotten_and_posted_again_later():
    d1 = FakeD1()
    send = FakeTelegram()
    kw = {"token": "123:TEST-TOKEN", "chat_id": "-1001234567890", "run_sql": d1, "send": send, "now": GENERATED_AT}
    telegram.post_new_signals(report(item()), **kw)
    telegram.post_new_signals(report(item(verdict="DO NOT BUY")), **kw)
    assert json.loads(d1.value) == {}
    telegram.post_new_signals(report(item()), **kw)
    assert len(send.sent) == 2


def test_the_state_is_shared_with_the_worker_format():
    d1 = FakeD1(value=json.dumps({"EURUSD": telegram.fingerprint(item())}))
    send = FakeTelegram()
    result = telegram.post_new_signals(report(item()), token="123:TEST-TOKEN", chat_id="-1001234567890",
                                       run_sql=d1, send=send, now=GENERATED_AT)
    assert result == {"posted": 0, "active": 1}
    assert send.sent == []


def test_a_failed_message_is_not_recorded_so_it_is_retried_next_run():
    d1 = FakeD1()
    send = FakeTelegram(fail_on=0)
    with pytest.raises(telegram.TelegramError):
        telegram.post_new_signals(report(item()), token="123:TEST-TOKEN", chat_id="-1001234567890",
                                  run_sql=d1, send=send, now=GENERATED_AT)
    assert d1.value is None or json.loads(d1.value) == {}


def test_only_active_verdicts_are_posted():
    d1 = FakeD1()
    send = FakeTelegram()
    result = telegram.post_new_signals(
        report(item(uid="GBPUSD", verdict="WATCH"), item(uid="USDJPY", verdict="DO NOT BUY")),
        token="123:TEST-TOKEN", chat_id="-1001234567890", run_sql=d1, send=send, now=GENERATED_AT)
    assert result == {"posted": 0, "active": 0}


def test_send_message_errors_never_contain_the_token(monkeypatch):
    class Boom(Exception):
        pass

    def refuse(request, timeout):
        raise telegram.urllib.error.URLError("network down: " + request.full_url)

    monkeypatch.setattr(telegram.urllib.request, "urlopen", refuse)
    with pytest.raises(telegram.TelegramError) as info:
        telegram.send_message("123:SECRET-TOKEN", "-100", "hi")
    assert "SECRET-TOKEN" not in str(info.value)


def test_publish_posts_only_after_a_successful_d1_write(monkeypatch, tmp_path):
    calls: list[str] = []
    monkeypatch.setattr(publish_module, "collect", lambda engine: (report(item()), {"EURUSD": {"x": 1}}))
    monkeypatch.setattr(publish_module, "execute_remote", lambda path: calls.append("d1"))
    posted = []
    monkeypatch.setattr(telegram, "post_new_signals",
                        lambda rep, **kw: posted.append(kw["chat_id"]) or {"posted": 1, "active": 1})

    result = publish_module.publish(object(), out=tmp_path / "s.sql", dry_run=False,
                                    telegram_token="123:TEST-TOKEN", telegram_chat_id="-1001234567890")
    assert calls == ["d1"] and posted == ["-1001234567890"]
    assert result["telegram"] == {"posted": 1, "active": 1}


def test_publish_reports_a_telegram_failure_without_failing(monkeypatch, tmp_path):
    monkeypatch.setattr(publish_module, "collect", lambda engine: (report(item()), {"EURUSD": {"x": 1}}))
    monkeypatch.setattr(publish_module, "execute_remote", lambda path: None)

    def broken(rep, **kw):
        raise telegram.TelegramError("Telegram rejected the message: Forbidden")

    monkeypatch.setattr(telegram, "post_new_signals", broken)
    result = publish_module.publish(object(), out=tmp_path / "s.sql", dry_run=False,
                                    telegram_token="123:TEST-TOKEN", telegram_chat_id="-1001234567890")
    assert "Forbidden" in result["telegram"]["error"]


def test_publish_skips_telegram_on_dry_run_and_when_unconfigured(monkeypatch, tmp_path):
    monkeypatch.setattr(publish_module, "collect", lambda engine: (report(item()), {"EURUSD": {"x": 1}}))
    monkeypatch.setattr(publish_module, "execute_remote", lambda path: pytest.fail("no D1 write on a dry run"))
    monkeypatch.setattr(telegram, "post_new_signals", lambda rep, **kw: pytest.fail("no post on a dry run"))
    result = publish_module.publish(object(), out=tmp_path / "s.sql", dry_run=True,
                                    telegram_token="123:TEST-TOKEN", telegram_chat_id="-1001234567890")
    assert "telegram" not in result

    monkeypatch.setattr(publish_module, "execute_remote", lambda path: None)
    result = publish_module.publish(object(), out=tmp_path / "s.sql", dry_run=False)
    assert "telegram" not in result


def test_run_remote_sql_reads_rows_past_wrangler_notices(monkeypatch):
    class Proc:
        returncode = 0
        stderr = ""
        stdout = 'Update available\n[{"results":[{"value":"{}"}],"success":true}]\n'

    monkeypatch.setattr(publish_module.subprocess, "run", lambda *a, **k: Proc())
    assert publish_module.run_remote_sql("SELECT 1") == [{"value": "{}"}]
