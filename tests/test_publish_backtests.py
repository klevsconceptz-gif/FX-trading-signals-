"""The backtest job: stores only live results, writes well-formed SQL, and refuses synthetic data."""

import json

import pytest

from fxbot.data import MarketData as RealMarketData
from fxbot.publish import PublishError
from scripts import publish_backtests as job


class LiveLookingData(RealMarketData):
    """The synthetic market, labelled as live, so the write path can be tested offline."""

    def load(self, prefer_live=False):
        history = super().load(prefer_live=False)
        self.source_label = "live (test feed, 1h)"
        return history


class SyntheticData(RealMarketData):
    def load(self, prefer_live=False):
        history = super().load(prefer_live=False)
        self.source_label = "synthetic (live unavailable: test)"
        return history


def test_dry_run_writes_one_statement_per_symbol(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "MarketData", LiveLookingData)
    out = tmp_path / "backtests.sql"
    code = job.main(["--symbols", "EURUSD,GBPUSD", "--bars", "600", "--dry-run", "--out", str(out)])
    assert code == 0
    sql = out.read_text(encoding="utf-8")
    assert sql.count("INSERT OR REPLACE INTO backtest_results") == 2
    assert "'live (test feed, 1h)'" in sql
    # The JSON payload is quoted as one literal and round-trips to the Lab's shape.
    body = sql.split(", 'live (test feed, 1h)', '", 1)[1].split("', ", 1)[0].replace("''", "'")
    payload = json.loads(body)
    assert payload["symbol"] == "EURUSD"
    assert set(payload) >= {"symbol", "run_at", "data_source", "params", "metrics", "equity_curve", "trades"}
    assert len(payload["equity_curve"]["t"]) == len(payload["equity_curve"]["v"])


def test_synthetic_data_is_refused_and_nothing_is_written(tmp_path, monkeypatch):
    monkeypatch.setattr(job, "MarketData", SyntheticData)
    out = tmp_path / "backtests.sql"
    with pytest.raises(SystemExit) as exc:
        job.main(["--symbols", "EURUSD", "--bars", "600", "--dry-run", "--out", str(out)])
    assert exc.value.code == 1
    assert not out.exists()


def test_unknown_symbol_is_rejected_before_any_data_is_loaded(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("must not load data for an unknown symbol")

    monkeypatch.setattr(job, "MarketData", boom)
    with pytest.raises(SystemExit):
        job.main(["--symbols", "NOTAPAIR", "--dry-run", "--out", str(tmp_path / "x.sql")])


def test_oversized_result_is_refused():
    payload = {"symbol": "EURUSD", "run_at": "2026-10-10T00:00:00Z", "data_source": "live (x)",
               "trades": ["y" * 200_000]}
    with pytest.raises(PublishError):
        job.upsert_statement(payload)


def test_quotes_are_escaped_in_sql_literals():
    statement = job.upsert_statement({"symbol": "EURUSD", "run_at": "2026-10-10T00:00:00Z",
                                      "data_source": "live (it's fine)", "trades": []})
    assert "'live (it''s fine)'" in statement
