"""Gold (XAUUSD) uses its own, lower entry threshold. Other pairs keep the default."""

import pandas as pd

from fxbot.config import SignalConfig
from fxbot.signals import build_plan


def _flat(close: float) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=300, freq="h")
    return pd.DataFrame(
        {"open": close, "high": close * 1.0001, "low": close * 0.9999, "close": close},
        index=idx,
    )


def test_threshold_for_uses_gold_override_and_default_for_others():
    cfg = SignalConfig()
    assert cfg.threshold_for("XAUUSD") == 0.20
    assert cfg.threshold_for("EURUSD") == cfg.entry_threshold == 0.25
    assert cfg.threshold_for("XAGUSD") == 0.25


def test_gold_wait_message_shows_gold_threshold():
    """The WAIT plan states the threshold that applies to that symbol."""
    gold = build_plan("XAUUSD", _flat(2320.0), SignalConfig())
    eur = build_plan("EURUSD", _flat(1.1), SignalConfig())
    assert gold.side == "WAIT" and eur.side == "WAIT"
    assert any("0.2 threshold" in note for note in gold.notes)
    assert any("0.25 threshold" in note for note in eur.notes)


def test_override_can_be_changed_per_config():
    cfg = SignalConfig(symbol_entry_thresholds={"XAUUSD": 0.5})
    assert cfg.threshold_for("XAUUSD") == 0.5
    assert cfg.threshold_for("EURUSD") == 0.25
