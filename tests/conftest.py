"""Shared fixtures: deterministic market data for the test-suite."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# the suite never touches the network: the server defaults to live data, so pin it off
os.environ.setdefault("KFX_LIVE_DATA", "0")
# and it never touches the operator's database: each run gets a throw-away SQLite file
os.environ.setdefault("KLEVS_DATABASE_URL", "sqlite:///" + tempfile.mkdtemp(prefix="klevs-test-") + "/test.db")

from fxbot.config import AppConfig
from fxbot.data import MarketData, SyntheticMarket
from fxbot.universe import BY_SYMBOL


@pytest.fixture(scope="session")
def cfg() -> AppConfig:
    c = AppConfig()
    c.data.bars = 1200
    c.ml.min_train_bars = 300
    return c


@pytest.fixture(scope="session")
def market(cfg) -> MarketData:
    return MarketData(cfg.data)


@pytest.fixture(scope="session")
def eurusd(cfg) -> "pd.DataFrame":
    return SyntheticMarket(cfg.data).ohlc(BY_SYMBOL["EURUSD"], 1200)


@pytest.fixture(scope="session")
def full_history(cfg) -> dict:
    return MarketData(cfg.data).load()
