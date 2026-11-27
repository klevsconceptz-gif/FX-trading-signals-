"""Per-symbol entry thresholds. Gold uses the shared default unless an override is set."""

import pandas as pd

from fxbot.config import SignalConfig
from fxbot.signals import build_plan


def _flat(close: float) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=300, freq="h")
    return pd.DataFrame(
        {"open": close, "high": close * 1.0001, "low": close * 0.9999, "close": close},
        index=idx,
    )


def test_gold_uses_the_shared_default_threshold():
    cfg = SignalConfig()
    assert cfg.symbol_entry_thresholds == {}
    assert cfg.threshold_for("XAUUSD") == cfg.entry_threshold == 0.25


def test_wait_message_shows_the_symbol_threshold():
    gold = build_plan("XAUUSD", _flat(2320.0), SignalConfig())
    eur = build_plan("EURUSD", _flat(1.1), SignalConfig())
    assert gold.side == "WAIT" and eur.side == "WAIT"
    assert any("0.25 threshold" in note for note in gold.notes)
    assert any("0.25 threshold" in note for note in eur.notes)


def test_override_applies_only_to_its_symbol():
    cfg = SignalConfig(symbol_entry_thresholds={"XAUUSD": 0.20})
    assert cfg.threshold_for("XAUUSD") == 0.20
    assert cfg.threshold_for("EURUSD") == 0.25
    gold = build_plan("XAUUSD", _flat(2320.0), cfg)
    assert any("0.2 threshold" in note for note in gold.notes)
