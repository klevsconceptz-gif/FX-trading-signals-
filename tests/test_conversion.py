"""FX conversion path, inverse-rate and lagging regressions."""

import pandas as pd
import pytest

from fxbot.conversion import conversion_rate_series, latest_conversion_rate


def _closes(values, index):
    return pd.DataFrame({"close": values}, index=index)


def test_shortest_path_converts_through_usd_and_inverts_edges():
    index = pd.date_range("2025-01-01", periods=2, freq="h", tz="UTC")
    history = {
        "EURUSD": _closes([1.10, 1.20], index),
        "GBPUSD": _closes([1.25, 1.50], index),
    }

    rates = conversion_rate_series(history, "EUR", "GBP", index)

    assert rates.iloc[0] == pytest.approx(1.10 / 1.25)
    assert rates.iloc[1] == pytest.approx(1.20 / 1.50)
    assert latest_conversion_rate(history, "EUR", "GBP") == pytest.approx(0.8)


def test_lagged_conversion_uses_only_prior_bar_close():
    index = pd.date_range("2025-01-01", periods=3, freq="h", tz="UTC")
    history = {"USDJPY": _closes([100.0, 110.0, 125.0], index)}

    rates = conversion_rate_series(history, "JPY", "USD", index, lag_bars=1)

    assert pd.isna(rates.iloc[0])
    assert rates.iloc[1] == pytest.approx(1 / 100.0)
    assert rates.iloc[2] == pytest.approx(1 / 110.0)
