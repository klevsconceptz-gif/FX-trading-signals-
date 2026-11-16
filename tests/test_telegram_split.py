"""The Telegram post runs as its own workflow step, from the report the publish step saved."""

import json
import re
from pathlib import Path

from fxbot import publish as publish_module
from scripts import post_telegram

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "signals.yml").read_text(encoding="utf-8")

REPORT = {
    "generated_at": 1794796239.5, "data_source": "live (yfinance, 1h, 30 symbols)",
    "summary": {}, "currency_strength": {}, "signals": [], "watchlist": [], "instructions": [],
}


def _steps() -> dict[str, str]:
    """Map each step name in the publish job to its block of YAML text (no YAML parser needed)."""
    blocks = re.split(r"\n      - (?=name: |uses: |run: )", WORKFLOW.split("    steps:\n", 1)[1])
    out: dict[str, str] = {}
    for block in blocks:
        match = re.match(r"name: (.+)", block)
        if match:
            out[match.group(1).strip()] = block
    return out


def test_publish_step_writes_to_d1_without_telegram_secrets():
    steps = _steps()
    publish_step = steps["Publish the latest live snapshot to D1"]
    assert "python -m scripts.publish_signals --report-out" in publish_step
    assert "TELEGRAM" not in publish_step
    assert "continue-on-error" not in publish_step       # a D1 failure must fail the job


def test_telegram_step_is_separate_continue_on_error_and_runs_after_the_publish():
    steps = _steps()
    names = list(steps)
    telegram = steps["Post new signals to Telegram"]
    assert names.index("Post new signals to Telegram") == names.index("Publish the latest live snapshot to D1") + 1
    assert "continue-on-error: true" in telegram
    assert "if: success()" in telegram
    assert "python -m scripts.post_telegram --report" in telegram
    assert "TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}" in telegram


def test_publish_saves_the_report_it_published(monkeypatch, tmp_path):
    monkeypatch.setattr(publish_module, "collect", lambda engine: (dict(REPORT), {}))
    monkeypatch.setattr(publish_module, "execute_remote", lambda sql_file: None)
    out = tmp_path / "signals.sql"
    report_out = tmp_path / "report.json"
    publish_module.publish(object(), out=out, report_out=report_out)
    assert json.loads(report_out.read_text(encoding="utf-8")) == REPORT


def test_publish_does_not_post_to_telegram_itself(monkeypatch, tmp_path):
    monkeypatch.setattr(publish_module, "collect", lambda engine: (dict(REPORT), {}))
    monkeypatch.setattr(publish_module, "execute_remote", lambda sql_file: None)

    def boom(*a, **k):
        raise AssertionError("the publish step must not post")

    monkeypatch.setattr("fxbot.telegram.post_new_signals", boom)
    result = publish_module.publish(object(), out=tmp_path / "s.sql", report_out=tmp_path / "r.json")
    assert "telegram" not in result


def test_post_step_skips_cleanly_without_secrets(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    report = tmp_path / "r.json"
    report.write_text(json.dumps(REPORT), encoding="utf-8")
    assert post_telegram.main(["--report", str(report)]) == 0
    assert "skipped (not configured)" in capsys.readouterr().out


def test_post_step_posts_the_saved_report(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:TEST-TOKEN")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1001234567890")
    seen = {}

    def fake_post(report, *, token, chat_id, run_sql):
        seen["report"] = report
        return {"posted": 2, "active": 3}

    monkeypatch.setattr(post_telegram, "post_new_signals", fake_post)
    report = tmp_path / "r.json"
    report.write_text(json.dumps(REPORT), encoding="utf-8")
    assert post_telegram.main(["--report", str(report)]) == 0
    assert seen["report"] == REPORT
    assert "posted=2 active=3" in capsys.readouterr().out


def test_post_step_failure_exits_1_and_never_prints_the_token(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:TEST-TOKEN")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1001234567890")

    def down(*a, **k):
        raise RuntimeError("Telegram returned HTTP 502")

    monkeypatch.setattr(post_telegram, "post_new_signals", down)
    report = tmp_path / "r.json"
    report.write_text(json.dumps(REPORT), encoding="utf-8")
    assert post_telegram.main(["--report", str(report)]) == 1
    captured = capsys.readouterr()
    assert "::error title=Telegram post failed::" in captured.out
    assert "TEST-TOKEN" not in captured.out + captured.err
