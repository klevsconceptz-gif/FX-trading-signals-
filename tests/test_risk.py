"""Risk-manager tests: sizing formula, caps, circuit breakers."""

import pytest

from fxbot.config import RiskConfig
from fxbot.risk import RiskManager, kelly_fraction
from fxbot.signals import TradePlan


def _plan(side="BUY", entry=1.1000, stop=1.0950, target=1.1125, rr=2.5, symbol="EURUSD"):
    return TradePlan(
        symbol=symbol, display=symbol, side=side, score=0.5, confidence=0.6,
        price=entry, entry=entry, stop=stop, target=target, target2=target + 0.01,
        risk_reward=rr, atr=0.003, stop_dist=abs(entry - stop),
        expected_move_pct=0.002, p_up=0.6, regime="trend_up", factors={},
    )


def test_position_sizing_formula():
    rm = RiskManager(RiskConfig(equity=10_000, risk_per_trade_pct=1.0))
    plan = _plan(entry=1.10, stop=1.09)   # $0.01 risk per unit
    d = rm.size_position(plan)
    assert d.accepted
    assert d.risk_amount == pytest.approx(100.0)          # 1% of 10k
    assert d.units == pytest.approx(100.0 / 0.01)         # 10,000 units
    assert d.lots == pytest.approx(0.1)


def test_wider_stop_smaller_position():
    rm = RiskManager(RiskConfig(equity=50_000, risk_per_trade_pct=1.0))
    narrow = rm.size_position(_plan(entry=1.10, stop=1.098))
    wide = rm.size_position(_plan(entry=1.10, stop=1.09))
    assert wide.units < narrow.units
    assert wide.risk_amount == pytest.approx(narrow.risk_amount)


def test_max_open_positions_cap():
    rm = RiskManager(RiskConfig(equity=10_000, max_open_positions=2))
    assert rm.open(_plan(symbol="EURUSD")).accepted
    assert rm.open(_plan(symbol="GBPUSD")).accepted
    d = rm.open(_plan(symbol="AUDUSD"))
    assert not d.accepted
    assert "max open" in d.reason


def test_currency_exposure_cap():
    rm = RiskManager(RiskConfig(equity=10_000, max_positions_per_currency=2))
    assert rm.open(_plan(symbol="EURUSD")).accepted      # USD 1
    assert rm.open(_plan(symbol="EURGBP")).accepted      # EUR 2
    d = rm.open(_plan(symbol="EURAUD"))                  # EUR would be 3rd
    assert not d.accepted
    assert "currency exposure" in d.reason


def test_daily_loss_circuit_breaker():
    rm = RiskManager(RiskConfig(equity=10_000, daily_loss_limit_pct=3.0))
    plan = _plan()
    rm.open(plan)
    rm.close(plan, pnl=-400.0)   # -4% day
    assert rm.halted
    d = rm.can_open(_plan(symbol="GBPUSD"))
    assert not d.accepted
    assert "halted" in d.reason
    rm.new_day()
    assert not rm.halted


def test_rr_veto():
    rm = RiskManager(RiskConfig(equity=10_000, min_risk_reward=1.5))
    d = rm.can_open(_plan(rr=1.1))
    assert not d.accepted
    assert "R:R" in d.reason


def test_drawdown_throttle():
    rm = RiskManager(RiskConfig(equity=10_000, max_drawdown_pct=10.0))
    rm.equity = 8_500.0
    rm.peak_equity = 10_000.0
    d = rm.can_open(_plan())
    assert d.accepted
    assert d.scale == 0.5   # halved size past the drawdown threshold


def test_kelly_fraction():
    assert kelly_fraction(0.0) == 0.0
    assert kelly_fraction(1.0) == 0.0
    assert kelly_fraction(0.6, 1.5, 1.0) > 0
    assert kelly_fraction(0.6, 1.5, 1.0) <= 0.125   # half of 0.25 cap
