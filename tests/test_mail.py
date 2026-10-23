"""E-mail goes through the Worker's send_email relay; nothing is sent over SMTP."""

import logging

import pytest

from fxbot import mail
from tests.fake_worker import FakeWorker


@pytest.fixture()
def relay(monkeypatch):
    fake = FakeWorker()
    base = fake.start()
    monkeypatch.setenv("KLEVS_EMAIL_URL", base + "/__internal/email")
    monkeypatch.setenv("KLEVS_INTERNAL_TOKEN", fake.token)
    yield fake
    fake.stop()


def test_send_is_accepted_with_202(relay):
    assert mail.send("trader@example.com", "Reset code", "Your code is 123456.") is True
    assert relay.emails == [{"to": "trader@example.com", "subject": "Reset code", "text": "Your code is 123456."}]


def test_wrong_token_is_refused(relay, monkeypatch):
    monkeypatch.setenv("KLEVS_INTERNAL_TOKEN", "wrong")
    assert mail.send("trader@example.com", "Reset code", "x") is False
    assert relay.emails == []


def test_relay_failure_returns_false_without_echoing_the_body(relay, caplog):
    relay.fail_email = True
    caplog.set_level(logging.DEBUG, logger="klevs.mail")
    secret_text = "Your reset code is 987654 and must stay private."
    assert mail.send("trader@example.com", "Reset code", secret_text) is False
    assert "987654" not in caplog.text
    assert "trader@example.com" not in caplog.text


def test_not_configured_sends_nothing(monkeypatch, caplog):
    monkeypatch.delenv("KLEVS_EMAIL_URL", raising=False)
    monkeypatch.delenv("KLEVS_INTERNAL_TOKEN", raising=False)
    assert mail.configured() is False
    assert mail.send("trader@example.com", "Reset code", "x") is False
    assert "not configured" in caplog.text


def test_local_validation_rejects_empty_recipient(relay):
    assert mail.send("", "Reset code", "x") is False
    assert relay.emails == []


def test_unreachable_relay_returns_false(monkeypatch):
    monkeypatch.setenv("KLEVS_EMAIL_URL", "http://127.0.0.1:9/__internal/email")
    monkeypatch.setenv("KLEVS_INTERNAL_TOKEN", "t")
    assert mail.send("trader@example.com", "Reset code", "x") is False
