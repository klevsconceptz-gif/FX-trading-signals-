"""Central configuration for the FX bot: risk, signal, ML and data settings.

Everything is intentionally dataclass-based so tests and the API can build
custom configurations without touching global state.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class RiskConfig:
    """Account and risk-management parameters."""

    equity: float = 10_000.0            # starting account equity (account currency)
    account_currency: str = "USD"
    risk_per_trade_pct: float = 0.75    # % of equity risked per trade
    max_open_positions: int = 8
    max_positions_per_currency: int = 2  # diversification cap (e.g. not 3x USD)
    max_risk_per_currency_pct: float = 2.0
    min_risk_reward: float = 1.5        # skip setups worse than 1:1.5
    daily_loss_limit_pct: float = 3.0   # circuit breaker: stop trading for the day
    max_drawdown_pct: float = 15.0      # throttle: halve risk beyond this DD
    correlation_risk_scale: float = 0.5  # halve size when correlated with open trade


@dataclass
class SignalConfig:
    """Weights and thresholds for the multi-factor signal engine."""

    # composite score threshold to emit a directional signal
    entry_threshold: float = 0.25
    # factor weights (must roughly sum to 1.0)
    w_trend: float = 0.30
    w_momentum: float = 0.20
    w_reversion: float = 0.10
    w_breakout: float = 0.10
    w_ml: float = 0.20
    w_strength: float = 0.10
    # ATR stop / target multipliers (research: 1.5x-2x SL, 2.5x TP for FX)
    atr_stop_mult: float = 1.5
    atr_target_mult: float = 2.5
    atr_stop_mult_range: float = 1.2
    atr_target_mult_range: float = 1.8
    # partial profit plan: take 50% off at this R multiple, trail the rest
    partial_profit_r: float = 1.5
    partial_profit_fraction: float = 0.5
    # minimum risk-reward to accept a plan (mirrors RiskConfig.min_risk_reward)
    min_risk_reward: float = 1.5
    # regime detection
    adx_trend_threshold: float = 20.0


@dataclass
class MLConfig:
    """Machine-learning forecasting parameters."""

    enabled: bool = True
    horizon: int = 8            # bars ahead for direction prediction
    min_train_bars: int = 800
    test_fraction: float = 0.2  # strictly time-ordered split
    n_estimators: int = 120
    max_depth: int = 3
    learning_rate: float = 0.05
    random_state: int = 7


@dataclass
class DataConfig:
    """Market data settings.

    Providers are tried in order; when none are reachable (e.g. offline
    sandbox) a deterministic, correlated synthetic market is generated so
    the whole pipeline remains fully functional.
    """

    timeframe: str = "1h"        # '1h' or '1d'
    bars: int = 3000             # history length to generate / fetch
    synthetic_seed: int = 20261010
    provider_order: tuple[str, ...] = ("frankfurter", "exchangerate_host", "yfinance")
    request_timeout: float = 8.0
    cache_dir: str = "data/cache"


@dataclass
class AppConfig:
    risk: RiskConfig = field(default_factory=RiskConfig)
    signal: SignalConfig = field(default_factory=SignalConfig)
    ml: MLConfig = field(default_factory=MLConfig)
    data: DataConfig = field(default_factory=DataConfig)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_CONFIG = AppConfig()
