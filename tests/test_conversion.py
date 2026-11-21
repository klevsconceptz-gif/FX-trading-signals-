"""FX conversion path, inverse-rate and lagging regressions."""

import pandas as pd
import pytest

from fxbot.conversion import MAX_CONVERSION_RATE_AGE, conversion_rate_series, latest_conversion_rate


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
    now = index[-1] + pd.Timedelta(hours=1)
    assert latest_conversion_rate(history, "EUR", "GBP", now=now) == pytest.approx(0.8)


def test_lagged_conversion_uses_only_prior_bar_close():
    index = pd.date_range("2025-01-01", periods=3, freq="h", tz="UTC")
    history = {"USDJPY": _closes([100.0, 110.0, 125.0], index)}

    rates = conversion_rate_series(history, "JPY", "USD", index, lag_bars=1)

    assert pd.isna(rates.iloc[0])
    assert rates.iloc[1] == pytest.approx(1 / 100.0)
    assert rates.iloc[2] == pytest.approx(1 / 110.0)


def test_rate_within_the_limit_is_accepted():
    index = pd.date_range("2025-01-01", periods=2, freq="h", tz="UTC")
    history = {
        "EURUSD": _closes([1.10, 1.20], index),
        "GBPUSD": _closes([1.25, 1.50], index),
    }
    now = index[-1] + pd.Timedelta(hours=1)

    assert latest_conversion_rate(history, "EUR", "GBP", now=now) == pytest.approx(0.8)


def test_rate_exactly_at_the_limit_is_accepted():
    index = pd.date_range("2025-01-01", periods=2, freq="h", tz="UTC")
    history = {"EURUSD": _closes([1.10, 1.20], index)}
    now = index[-1] + MAX_CONVERSION_RATE_AGE

    assert latest_conversion_rate(history, "EUR", "USD", now=now) == pytest.approx(1.20)


def test_weekend_gap_inside_72_hours_is_accepted():
    # Friday 21:00 UTC close, checked Monday 10:00 UTC: about 61 hours.
    friday_close = pd.Timestamp("2025-01-03 21:00", tz="UTC")
    history = {"EURUSD": _closes([1.10, 1.20], pd.DatetimeIndex([friday_close - pd.Timedelta(hours=1), friday_close]))}
    now = pd.Timestamp("2025-01-06 10:00", tz="UTC")

    assert latest_conversion_rate(history, "EUR", "USD", now=now) == pytest.approx(1.20)


def test_rate_older_than_the_limit_is_refused():
    index = pd.date_range("2025-01-01", periods=2, freq="h", tz="UTC")
    history = {"EURUSD": _closes([1.10, 1.20], index)}
    now = index[-1] + MAX_CONVERSION_RATE_AGE + pd.Timedelta(minutes=1)

    with pytest.raises(ValueError, match="stale FX conversion rate from EUR to USD: EURUSD"):
        latest_conversion_rate(history, "EUR", "USD", now=now)


def test_a_stale_pair_anywhere_on_the_path_refuses_the_rate():
    # EURUSD is fresh but GBPUSD stalled; the EUR->GBP path uses both, so it must be refused.
    fresh = pd.date_range("2025-01-10", periods=2, freq="h", tz="UTC")
    stale = pd.date_range("2025-01-01", periods=2, freq="h", tz="UTC")
    history = {
        "EURUSD": _closes([1.10, 1.20], fresh),
        "GBPUSD": _closes([1.25, 1.50], stale),
    }
    now = fresh[-1] + pd.Timedelta(hours=1)

    with pytest.raises(ValueError, match="GBPUSD last closed"):
        latest_conversion_rate(history, "EUR", "GBP", now=now)


def test_all_feeds_stalled_is_refused_even_though_the_newest_bar_is_old():
    # The old check compared against the newest bar in the history, so a stalled feed passed.
    index = pd.date_range("2025-01-01", periods=2, freq="h", tz="UTC")
    history = {"EURUSD": _closes([1.10, 1.20], index)}
    now = pd.Timestamp("2025-02-01", tz="UTC")

    with pytest.raises(ValueError, match="stale"):
        latest_conversion_rate(history, "EUR", "USD", now=now)
