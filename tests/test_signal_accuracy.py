"""Broker accuracy: every price, lot size and risk figure a trader sees must be one the broker accepts.

Covers broker price precision, outward rounding of stops, whole 0.01-lot sizing, the minimum-lot
rejection, metal spread units, and an end-to-end check of every plan in the universe.
"""

import pytest

from fxbot.config import AppConfig, RiskConfig
from fxbot.engine import MarketEngine
from fxbot.risk import RiskManager
from fxbot.signals import TradePlan
from fxbot.universe import BY_SYMBOL, MIN_LOT, UNIVERSE, format_price, price_digits, round_price


def _plan(symbol="EURUSD", side="BUY", entry=1.1000, stop=1.0950, target=1.1125):
    return TradePlan(
        symbol=symbol, display=symbol, side=side, score=0.5, confidence=0.6,
        price=entry, entry=entry, stop=stop, target=target, target2=target,
        risk_reward=1.5, atr=0.003, stop_dist=abs(entry - stop),
        expected_move_pct=0.002, p_up=0.6, regime="trend_up", factors={},
    )


# -- broker quoting -----------------------------------------------------------

@pytest.mark.parametrize("symbol, digits", [
    ("EURUSD", 5), ("GBPCHF", 5), ("USDJPY", 3), ("EURJPY", 3),
    ("XAUUSD", 2), ("XAGUSD", 3),
])
def test_price_digits_follow_broker_quoting(symbol, digits):
    assert price_digits(symbol) == digits


def test_format_price_keeps_every_broker_decimal():
    assert format_price("USDJPY", 149.1234) == "149.123"       # not 149.12 (5 significant digits)
    assert format_price("XAUUSD", 2320.4567) == "2320.46"      # cents kept
    assert format_price("EURUSD", 1.0368041) == "1.03680"


def test_round_price_to_broker_decimals():
    assert round_price("EURUSD", 1.03680449) == 1.0368
    assert round_price("USDJPY", 149.1236) == 149.124


def test_stop_rounds_away_from_entry_never_tighter():
    # BUY stop sits below entry: rounding moves it further down (more room, never less risk)
    assert round_price("EURUSD", 1.036804, away_from=1.04) == 1.03680
    assert round_price("EURUSD", 1.036801, away_from=1.04) == 1.0368
    # SELL stop sits above entry: rounding moves it further up
    assert round_price("EURUSD", 1.036801, away_from=1.03) == 1.03681


def test_metal_spreads_are_in_price_units():
    # 30 pips is 0.30 USD on gold and 0.030 USD on silver; the old values were ~100x too small
    assert BY_SYMBOL["XAUUSD"].typical_spread_pips * BY_SYMBOL["XAUUSD"].pip == pytest.approx(0.30)
    assert BY_SYMBOL["XAGUSD"].typical_spread_pips * BY_SYMBOL["XAGUSD"].pip == pytest.approx(0.030)


# -- sizing ----------------------------------------------------------------------

def test_lots_round_down_to_the_broker_step():
    rm = RiskManager(RiskConfig(equity=10_000, risk_per_trade_pct=0.75))
    plan = _plan(entry=1.10, stop=1.09)   # $75 budget, $0.01 per unit -> 7,500 units = 0.075 lot
    d = rm.size_position(plan)
    assert d.accepted
    assert d.lots == pytest.approx(0.07)                      # whole 0.01 steps, rounded down
    assert d.units == pytest.approx(7_000)
    assert d.risk_amount <= 75.0 + 1e-9                       # the loss at the stop never exceeds the budget


def test_position_below_minimum_lot_is_rejected():
    rm = RiskManager(RiskConfig(equity=100, risk_per_trade_pct=0.75))
    d = rm.size_position(_plan(entry=1.10, stop=1.09))        # $0.75 budget -> 75 units = 0.00075 lot
    assert not d.accepted
    assert "minimum" in d.reason and str(MIN_LOT) in d.reason


# -- end to end over the whole universe ---------------------------------------

@pytest.fixture(scope="module")
def scanned():
    engine = MarketEngine(AppConfig(), prefer_live=False)
    report = engine.scan(reload=True).to_dict()
    plans = {ins.symbol: engine.pair_report(ins.symbol)["plan"] for ins in UNIVERSE}
    return report, plans


def test_every_plan_price_is_a_valid_broker_price(scanned):
    _, plans = scanned
    for symbol, plan in plans.items():
        digits = price_digits(symbol)
        for key in ("entry", "stop", "target", "target2"):
            value = plan[key]
            assert round(value, digits) == pytest.approx(value, abs=1e-12), f"{symbol} {key}={value}"


def test_active_plans_keep_direction_and_reward_to_risk(scanned):
    _, plans = scanned
    active = [p for p in plans.values() if p["side"] in ("BUY", "SELL")]
    assert active, "the synthetic scan is expected to produce at least one active plan"
    for plan in active:
        e, s, t = plan["entry"], plan["stop"], plan["target"]
        if plan["side"] == "BUY":
            assert s < e < t, plan["symbol"]
        else:
            assert t < e < s, plan["symbol"]
        rr = abs(t - e) / abs(e - s)
        assert rr >= 1.5 - 1e-9, f"{plan['symbol']} rr={rr}"


def test_active_plans_size_to_whole_lots_within_budget(scanned):
    _, plans = scanned
    budget = AppConfig().risk.equity * AppConfig().risk.risk_per_trade_pct / 100.0
    for plan in plans.values():
        if plan["side"] not in ("BUY", "SELL"):
            continue
        assert plan["lots"] >= MIN_LOT - 1e-12, plan["symbol"]
        assert round(plan["lots"] / MIN_LOT) * MIN_LOT == pytest.approx(plan["lots"]), plan["symbol"]
        assert plan["risk_amount"] <= budget + 1e-9, plan["symbol"]


def test_instruction_text_shows_broker_precision(scanned):
    report, plans = scanned
    for item in report["instructions"]:
        if item["verdict"] not in ("BUY NOW", "SELL NOW"):
            continue
        symbol = item["symbol"]
        assert format_price(symbol, plans[symbol]["target"]) in item["one_line"], symbol
        assert format_price(symbol, plans[symbol]["stop"]) in item["one_line"], symbol


def test_worker_universe_matches_python_universe():
    """The Worker serves /api/universe from its own copy; pip and spread must match the engine."""
    import re
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "worker" / "src" / "universe.js").read_text(encoding="utf-8")
    rows = re.findall(
        r'symbol: "([A-Z]+)".*?pip: ([0-9.]+), typical_spread_pips: ([0-9.]+)', text
    )
    assert {r[0] for r in rows} == set(BY_SYMBOL), "Worker and engine list different instruments"
    for symbol, pip, spread in rows:
        ins = BY_SYMBOL[symbol]
        assert float(pip) == pytest.approx(ins.pip), symbol
        assert float(spread) == pytest.approx(ins.typical_spread_pips), symbol


def test_active_instructions_carry_per_lot_risk_for_user_sizes(scanned):
    """Each user's chosen lot size is shown in the browser from these per-lot figures."""
    report, _ = scanned
    active = [i for i in report["instructions"] if i["verdict"] in ("BUY NOW", "SELL NOW")]
    assert active
    for item in active:
        assert item["lots"] >= MIN_LOT - 1e-12, item["symbol"]
        assert item["risk_per_lot"] * item["lots"] == pytest.approx(item["risk_amount"], abs=0.02), item["symbol"]
        assert item["reward_per_lot"] == pytest.approx(item["risk_per_lot"] * 1.5, rel=0.01), item["symbol"]
        assert item["lot_units"] == BY_SYMBOL[item["symbol"]].lot_size_units
