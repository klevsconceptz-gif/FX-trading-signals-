"""Publishing the scheduled signal snapshot: refusal rules, SQL shape, size limits. Offline."""

import json
import re
from types import SimpleNamespace

import numpy as np
import pytest

from fxbot import publish
from fxbot.publish import PublishError, build_sql, collect, sql_text
from fxbot.publish import publish as run_publish
from fxbot.universe import UNIVERSE

STATEMENT_END = re.compile(r"\);\n$")
PAYLOAD = re.compile(r"'((?:[^']|'')*)'\);\n$")


class FakeEngine:
    def __init__(self, source="live (yfinance, 1h)", pairs=None, extra=None):
        self.source = source
        self.pairs = UNIVERSE if pairs is None else pairs
        self.extra = extra or {}

    def scan(self, reload=False):
        report = {
            "generated_at": 1_800_000_000.5,
            "data_source": self.source,
            "timeframe": "1h",
            "summary": {"pairs_scanned": len(self.pairs)},
            "instructions": [{"symbol": "EURUSD", "action": "BUY"}],
            "signals": [],
            **self.extra,
        }
        return SimpleNamespace(to_dict=lambda: report)

    def pair_report(self, symbol):
        if symbol not in {ins.symbol for ins in self.pairs}:
            return None
        return {"symbol": symbol, "candles": [{"c": 1.0}], "rsi": [float("nan"), 55.5]}


def _statements(sql):
    return [part + ";\n" for part in sql.split(";\n") if part.strip()]


def _key(statement):
    return re.search(r"VALUES \('([^']*)'", statement).group(1)


def _payload(statement):
    """The JSON stored in a statement: its last SQL literal, with quotes un-doubled."""
    return json.loads(PAYLOAD.search(statement).group(1).replace("''", "'"))


def test_sql_text_doubles_quotes_so_payloads_cannot_break_out():
    assert sql_text("it's") == "'it''s'"
    assert sql_text("x'); DROP TABLE users; --") == "'x''); DROP TABLE users; --'"


def test_rows_are_written_pairs_first_then_instructions_then_scan_with_one_run_id():
    report, pairs = collect(FakeEngine())
    statements = _statements(build_sql(report, pairs, run_id=42))
    keys = [_key(s) for s in statements]
    assert keys[-2:] == ["instructions", "scan"]
    assert keys[:-2] == [f"pair:{ins.symbol}" for ins in sorted(UNIVERSE, key=lambda i: i.symbol)]
    assert all(STATEMENT_END.search(s) for s in statements)
    assert all(", 42, " in s for s in statements)


def test_nan_is_published_as_null_and_payloads_are_valid_json():
    report, pairs = collect(FakeEngine())
    statements = _statements(build_sql(report, pairs, run_id=1))
    assert _payload(statements[-1])["summary"]["pairs_scanned"] == len(UNIVERSE)
    pair_payload = _payload(statements[0])
    assert pair_payload["rsi"] == [None, 55.5]
    assert "NaN" not in json.dumps(pair_payload)


def test_non_live_data_is_refused_so_the_last_live_snapshot_stays():
    with pytest.raises(PublishError, match="not live"):
        collect(FakeEngine(source="synthetic (correlated factor model)"))
    with pytest.raises(PublishError, match="not live"):
        collect(FakeEngine(source="synthetic (live unavailable: timeout)"))


def test_empty_scan_is_refused():
    with pytest.raises(PublishError, match="no pair data"):
        collect(FakeEngine(pairs=[]))


def test_oversized_statement_is_refused():
    report, pairs = collect(FakeEngine(extra={"blob": "x" * (publish.MAX_STATEMENT_BYTES + 10)}))
    with pytest.raises(PublishError, match="statement limit"):
        build_sql(report, pairs, run_id=1)


def test_dry_run_writes_sql_and_never_calls_wrangler(tmp_path, monkeypatch):
    def must_not_run(_):
        raise AssertionError("dry run must not touch D1")

    monkeypatch.setattr(publish, "execute_remote", must_not_run)
    out = tmp_path / "signals.sql"
    result = run_publish(FakeEngine(), out=out, dry_run=True)
    text = out.read_text(encoding="utf-8")
    assert result["dry_run"] is True and result["pairs"] == len(UNIVERSE)
    assert "INSERT OR REPLACE INTO signal_cache" in text and "VALUES ('scan'," in text


def test_refused_publish_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(publish, "execute_remote", lambda _: pytest.fail("must not publish"))
    out = tmp_path / "signals.sql"
    with pytest.raises(PublishError):
        run_publish(FakeEngine(source="synthetic (x)"), out=out, dry_run=True)
    assert not out.exists()


def test_numpy_scalars_are_published_as_plain_numbers():
    report, pairs = collect(FakeEngine(extra={"score": np.float64(1.5), "count": np.int64(3)}))
    payload = _payload(_statements(build_sql(report, pairs, run_id=7))[-1])
    assert payload["score"] == 1.5 and payload["count"] == 3
