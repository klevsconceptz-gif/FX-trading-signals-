"""Risk management: position sizing, exposure caps and circuit breakers.

Core formula (the research consensus):

    position units = (equity × risk%) / |entry − stop|

i.e. decide the stop first (ATR/structure-based), then size so the dollar
loss at the stop equals the fixed fraction of equity.  A wider stop
automatically yields a smaller position.

Portfolio layer: max open positions, per-currency exposure caps (so three
USD-heavy trades don't stack), correlation scaling and drawdown throttle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from .config import RiskConfig
from .signals import TradePlan


@dataclass
class RiskDecision:
    accepted: bool
    units: float
    lots: float
    risk_amount: float
    position_value: float
    scale: float            # applied size multiplier (correlation / drawdown)
    reason: str


class RiskManager:
    def __init__(self, cfg: Optional[RiskConfig] = None):
        self.cfg = cfg or RiskConfig()
        self.equity = self.cfg.equity
        self.peak_equity = self.cfg.equity
        self.open_positions: list[TradePlan] = []
        self.daily_pnl = 0.0
        self.halted = False

    # -- sizing -----------------------------------------------------------

    def size_position(self, plan: TradePlan,
                      correlation_scale: float = 1.0,
                      drawdown_scale: float = 1.0) -> RiskDecision:
        risk_pct = self.cfg.risk_per_trade_pct / 100.0
        risk_amount = self.equity * risk_pct * correlation_scale * drawdown_scale
        stop_dist = abs(plan.entry - plan.stop)
        if stop_dist <= 0 or not np.isfinite(stop_dist):
            return RiskDecision(False, 0, 0, 0, 0, 0.0, "invalid stop distance")

        units = risk_amount / stop_dist
        # FX convention: 1 standard lot = 100k units of base
        lots = units / 100_000.0
        position_value = units * plan.entry

        plan.units = units
        plan.lots = lots
        plan.risk_amount = risk_amount
        plan.position_value = position_value
        return RiskDecision(True, units, lots, risk_amount, position_value,
                            correlation_scale * drawdown_scale, "sized")

    # -- portfolio guards ---------------------------------------------------

    def _currency_exposure(self, plan: TradePlan) -> tuple[int, int]:
        base = plan.symbol[:3]
        quote = plan.symbol[3:6] if len(plan.symbol) >= 6 else ""
        base_n = quote_n = 0
        for p in self.open_positions:
            if p.symbol[:3] == base:
                base_n += 1
            if quote and (p.symbol[3:6] == quote or p.symbol[:3] == quote):
                quote_n += 1
        return base_n, quote_n

    def correlation_scale(self, plan: TradePlan,
                          corr_lookup: Optional[dict[tuple[str, str], float]] = None) -> float:
        """Downsize when the new trade overlaps currencies or correlates highly."""
        scale = 1.0
        base_n, quote_n = self._currency_exposure(plan)
        overlap = max(base_n, quote_n)
        if overlap >= self.cfg.max_positions_per_currency:
            return 0.0
        if overlap > 0:
            scale *= self.cfg.correlation_risk_scale
        if corr_lookup:
            for open_p in self.open_positions:
                key = tuple(sorted((plan.symbol, open_p.symbol)))
                c = corr_lookup.get(key)
                if c is not None and abs(c) > 0.75:
                    scale *= self.cfg.correlation_risk_scale
        return scale

    def drawdown_scale(self) -> float:
        dd = 1.0 - self.equity / max(self.peak_equity, 1e-9)
        if dd * 100.0 > self.cfg.max_drawdown_pct:
            return 0.5   # throttle after the max drawdown is exceeded
        return 1.0

    def can_open(self, plan: TradePlan) -> RiskDecision:
        if self.halted:
            return RiskDecision(False, 0, 0, 0, 0, 0.0, "daily loss limit hit — trading halted")
        if len(self.open_positions) >= self.cfg.max_open_positions:
            return RiskDecision(False, 0, 0, 0, 0, 0.0, "max open positions reached")
        if plan.side not in ("BUY", "SELL"):
            return RiskDecision(False, 0, 0, 0, 0, 0.0, "no active signal")
        if plan.risk_reward and plan.risk_reward < self.cfg.min_risk_reward:
            return RiskDecision(False, 0, 0, 0, 0, 0.0,
                                f"R:R {plan.risk_reward:.2f} below minimum {self.cfg.min_risk_reward}")
        c_scale = self.correlation_scale(plan)
        if c_scale <= 0:
            return RiskDecision(False, 0, 0, 0, 0, 0.0,
                                "currency exposure cap reached")
        d_scale = self.drawdown_scale()
        decision = self.size_position(plan, c_scale, d_scale)
        return decision

    # -- lifecycle ----------------------------------------------------------

    def open(self, plan: TradePlan) -> RiskDecision:
        decision = self.can_open(plan)
        if decision.accepted:
            self.open_positions.append(plan)
        return decision

    def close(self, plan: TradePlan, pnl: float) -> None:
        self.equity += pnl
        self.daily_pnl += pnl
        self.peak_equity = max(self.peak_equity, self.equity)
        self.open_positions = [p for p in self.open_positions if p.symbol != plan.symbol]
        if self.daily_pnl <= -self.equity * self.cfg.daily_loss_limit_pct / 100.0:
            self.halted = True

    def new_day(self) -> None:
        self.daily_pnl = 0.0
        self.halted = False

    # -- account-level metrics ---------------------------------------------

    @property
    def drawdown_pct(self) -> float:
        return max(0.0, (1.0 - self.equity / max(self.peak_equity, 1e-9)) * 100.0)

    def exposure_summary(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for p in self.open_positions:
            out[p.symbol] = out.get(p.symbol, 0.0) + p.position_value
        return out


def kelly_fraction(win_rate: float, avg_win_r: float = 1.5, avg_loss_r: float = 1.0) -> float:
    """Fractional Kelly (capped) — used only as a reference in reports."""
    if win_rate <= 0 or win_rate >= 1 or avg_loss_r <= 0:
        return 0.0
    b = avg_win_r / avg_loss_r
    edge = win_rate * b - (1.0 - win_rate)
    return float(np.clip(edge / b, 0.0, 0.25)) * 0.5   # half-Kelly, hard-capped
