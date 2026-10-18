"""Risk management: account-currency sizing, portfolio caps and circuit breakers.

The stop distance is denominated in the instrument's quote currency. It must be
converted into the account currency before calculating base units. Portfolio
limits apply to the proposed/open positions tracked by this manager.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from .config import RiskConfig
from .signals import TradePlan
from .universe import BY_SYMBOL


@dataclass
class RiskDecision:
    accepted: bool
    units: float
    lots: float
    risk_amount: float
    position_value: float
    scale: float            # applied size multiplier (correlation / currency / drawdown)
    reason: str


class RiskManager:
    def __init__(self, cfg: Optional[RiskConfig] = None):
        self.cfg = cfg or RiskConfig()
        self.equity = self.cfg.equity
        self.peak_equity = self.cfg.equity
        self.day_start_equity = self.cfg.equity
        self.open_positions: list[TradePlan] = []
        self.daily_pnl = 0.0
        self.halted = False

    # -- sizing -----------------------------------------------------------

    def size_position(
        self,
        plan: TradePlan,
        correlation_scale: float = 1.0,
        drawdown_scale: float = 1.0,
        quote_to_account_rate: Optional[float] = None,
    ) -> RiskDecision:
        instrument = BY_SYMBOL.get(plan.symbol)
        if instrument is None:
            return RiskDecision(False, 0, 0, 0, 0, 0.0, "unknown instrument")

        rate = quote_to_account_rate
        if rate is None:
            rate = 1.0 if instrument.quote == self.cfg.account_currency.upper() else None
        if rate is None or not np.isfinite(rate) or rate <= 0:
            return RiskDecision(False, 0, 0, 0, 0, 0.0,
                                f"missing {instrument.quote}/{self.cfg.account_currency} conversion rate")

        stop_dist = abs(plan.entry - plan.stop)
        if stop_dist <= 0 or not np.isfinite(stop_dist):
            return RiskDecision(False, 0, 0, 0, 0, 0.0, "invalid stop distance")

        risk_amount = self.equity * self.cfg.risk_per_trade_pct / 100.0
        risk_amount *= correlation_scale * drawdown_scale
        loss_per_unit = stop_dist * float(rate)  # account currency per base unit
        units = risk_amount / loss_per_unit
        lots = units / instrument.lot_size_units
        position_value = units * plan.entry * float(rate)

        plan.units = float(units)
        plan.lots = float(lots)
        plan.risk_amount = float(risk_amount)
        plan.position_value = float(position_value)
        return RiskDecision(True, float(units), float(lots), float(risk_amount),
                            float(position_value), correlation_scale * drawdown_scale, "sized")

    # -- portfolio guards ---------------------------------------------------

    def _currency_exposure(self, plan: TradePlan) -> tuple[int, int]:
        instrument = BY_SYMBOL.get(plan.symbol)
        if instrument is None:
            return 0, 0
        base, quote = instrument.base, instrument.quote
        base_n = quote_n = 0
        for position in self.open_positions:
            other = BY_SYMBOL.get(position.symbol)
            if other is None:
                continue
            currencies = {other.base, other.quote}
            base_n += base in currencies
            quote_n += quote in currencies
        return base_n, quote_n

    def correlation_scale(
        self,
        plan: TradePlan,
        corr_lookup: Optional[dict[tuple[str, str], float]] = None,
    ) -> float:
        """Downsize when currencies overlap or the trade is highly correlated."""
        scale = 1.0
        base_n, quote_n = self._currency_exposure(plan)
        overlap = max(base_n, quote_n)
        if overlap >= self.cfg.max_positions_per_currency:
            return 0.0
        if overlap > 0:
            scale *= self.cfg.correlation_risk_scale
        if corr_lookup:
            for open_plan in self.open_positions:
                key = tuple(sorted((plan.symbol, open_plan.symbol)))
                correlation = corr_lookup.get(key)
                if correlation is not None and abs(correlation) > 0.75:
                    scale *= self.cfg.correlation_risk_scale
        return scale

    def drawdown_scale(self) -> float:
        drawdown = 1.0 - self.equity / max(self.peak_equity, 1e-9)
        if drawdown * 100.0 > self.cfg.max_drawdown_pct:
            return 0.5
        return 1.0

    def can_open(
        self,
        plan: TradePlan,
        corr_lookup: Optional[dict[tuple[str, str], float]] = None,
        quote_to_account_rate: Optional[float] = None,
    ) -> RiskDecision:
        if self.halted:
            return RiskDecision(False, 0, 0, 0, 0, 0.0, "daily loss limit hit — trading halted")
        if len(self.open_positions) >= self.cfg.max_open_positions:
            return RiskDecision(False, 0, 0, 0, 0, 0.0, "max open positions reached")
        if plan.side not in ("BUY", "SELL"):
            return RiskDecision(False, 0, 0, 0, 0, 0.0, "no active signal")
        if plan.risk_reward and plan.risk_reward < self.cfg.min_risk_reward:
            return RiskDecision(False, 0, 0, 0, 0, 0.0,
                                f"R:R {plan.risk_reward:.2f} below minimum {self.cfg.min_risk_reward}")

        instrument = BY_SYMBOL.get(plan.symbol)
        if instrument is None:
            return RiskDecision(False, 0, 0, 0, 0, 0.0, "unknown instrument")
        rate = quote_to_account_rate
        if rate is None:
            rate = 1.0 if instrument.quote == self.cfg.account_currency.upper() else None
        if rate is None or not np.isfinite(rate) or rate <= 0:
            return RiskDecision(False, 0, 0, 0, 0, 0.0,
                                f"missing {instrument.quote}/{self.cfg.account_currency} conversion rate")

        portfolio_scale = self.correlation_scale(plan, corr_lookup)
        if portfolio_scale <= 0:
            return RiskDecision(False, 0, 0, 0, 0, 0.0, "currency exposure cap reached")
        drawdown_scale = self.drawdown_scale()

        base_risk = self.equity * self.cfg.risk_per_trade_pct / 100.0
        base_risk *= portfolio_scale * drawdown_scale
        if base_risk <= 0 or not np.isfinite(base_risk):
            return RiskDecision(False, 0, 0, 0, 0, 0.0, "invalid risk budget")

        currency_cap = self.equity * self.cfg.max_risk_per_currency_pct / 100.0
        currencies = {instrument.base, instrument.quote}
        available = []
        for currency in currencies:
            used = sum(
                position.risk_amount
                for position in self.open_positions
                if (other := BY_SYMBOL.get(position.symbol)) is not None
                and currency in {other.base, other.quote}
            )
            available.append(currency_cap - used)
        available_risk = min(available, default=currency_cap)
        if available_risk <= 0:
            return RiskDecision(False, 0, 0, 0, 0, 0.0, "per-currency risk cap reached")
        currency_scale = min(1.0, available_risk / base_risk)

        decision = self.size_position(
            plan,
            correlation_scale=portfolio_scale * currency_scale,
            drawdown_scale=drawdown_scale,
            quote_to_account_rate=float(rate),
        )
        if not decision.accepted:
            return decision
        if currency_scale < 1.0:
            decision.reason = "sized to remaining per-currency risk budget"
        return decision

    # -- lifecycle ----------------------------------------------------------

    def open(
        self,
        plan: TradePlan,
        corr_lookup: Optional[dict[tuple[str, str], float]] = None,
        quote_to_account_rate: Optional[float] = None,
    ) -> RiskDecision:
        decision = self.can_open(plan, corr_lookup, quote_to_account_rate)
        if decision.accepted:
            self.open_positions.append(plan)
        return decision

    def close(self, plan: TradePlan, pnl: float) -> None:
        self.equity += pnl
        self.daily_pnl += pnl
        self.peak_equity = max(self.peak_equity, self.equity)
        self.open_positions = [position for position in self.open_positions if position is not plan]
        if self.daily_pnl <= -self.day_start_equity * self.cfg.daily_loss_limit_pct / 100.0:
            self.halted = True

    def new_day(self) -> None:
        self.daily_pnl = 0.0
        self.day_start_equity = self.equity
        self.halted = False

    # -- account-level metrics ---------------------------------------------

    @property
    def drawdown_pct(self) -> float:
        return max(0.0, (1.0 - self.equity / max(self.peak_equity, 1e-9)) * 100.0)

    def exposure_summary(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for plan in self.open_positions:
            out[plan.symbol] = out.get(plan.symbol, 0.0) + plan.position_value
        return out


def kelly_fraction(win_rate: float, avg_win_r: float = 1.5, avg_loss_r: float = 1.0) -> float:
    """Fractional Kelly (capped) — used only as a reference in reports."""
    if win_rate <= 0 or win_rate >= 1 or avg_loss_r <= 0 or avg_win_r <= 0:
        return 0.0
    b = avg_win_r / avg_loss_r
    if b <= 0:
        return 0.0
    edge = win_rate * b - (1.0 - win_rate)
    return float(np.clip(edge / b, 0.0, 0.25)) * 0.5   # half-Kelly, hard-capped
