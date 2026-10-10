"""The orchestrator: full-market scan pipeline.

    load universe → indicators → currency strength & regime
    → ML forecasts → signal engine → risk manager → market report

`MarketEngine.scan()` is the single entry point used by both the CLI and
the dashboard API.  It returns a JSON-serialisable ``MarketReport``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
import pandas as pd

from .config import AppConfig
from .data import MarketData
from .ml import MarketForecaster
from .risk import RiskManager, kelly_fraction
from .signals import TradePlan, build_plan
from .strength import CurrencyStrength, compute_strength, correlation_matrix, detect_regime
from .universe import BY_SYMBOL


@dataclass
class MarketReport:
    generated_at: float
    data_source: str
    timeframe: str
    summary: dict[str, Any] = field(default_factory=dict)
    currency_strength: list[dict[str, Any]] = field(default_factory=list)
    dollar_index: float = 0.0
    breadth: float = 0.0
    signals: list[dict[str, Any]] = field(default_factory=list)
    watchlist: list[dict[str, Any]] = field(default_factory=list)
    instructions: list[dict[str, Any]] = field(default_factory=list)
    insights: list[str] = field(default_factory=list)
    correlation: dict[str, Any] = field(default_factory=dict)
    risk: dict[str, Any] = field(default_factory=dict)
    ml: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "data_source": self.data_source,
            "timeframe": self.timeframe,
            "summary": self.summary,
            "currency_strength": self.currency_strength,
            "dollar_index": self.dollar_index,
            "breadth": self.breadth,
            "signals": self.signals,
            "watchlist": self.watchlist,
            "instructions": self.instructions,
            "insights": self.insights,
            "correlation": self.correlation,
            "risk": self.risk,
            "ml": self.ml,
        }


class MarketEngine:
    def __init__(self, cfg: Optional[AppConfig] = None, prefer_live: bool = False):
        self.cfg = cfg or AppConfig()
        self.prefer_live = prefer_live
        self.data = MarketData(self.cfg.data)
        self.forecaster = MarketForecaster(self.cfg.ml)
        self.risk = RiskManager(self.cfg.risk)
        self._cache: Optional[MarketReport] = None
        self._history: dict[str, pd.DataFrame] = {}
        self._strength: Optional[CurrencyStrength] = None

    # ------------------------------------------------------------------

    def load_data(self) -> dict[str, pd.DataFrame]:
        self._history = self.data.load(prefer_live=self.prefer_live)
        return self._history

    def scan(self, use_cache: bool = False) -> MarketReport:
        if use_cache and self._cache is not None:
            return self._cache
        t0 = time.time()
        history = self._history or self.load_data()

        # 1. cross-sectional currency strength over the whole market
        strength = compute_strength(history)
        self._strength = strength

        # 2. per-symbol ML forecasts
        forecasts = self.forecaster.run(history)

        # 3. correlation matrix (for the dashboard + risk scaling)
        corr = correlation_matrix(history)
        corr_lookup: dict[tuple[str, str], float] = {}
        if not corr.empty:
            for i, a in enumerate(corr.columns):
                for b in corr.columns[i + 1:]:
                    corr_lookup[(a, b)] = float(corr.loc[a, b])
                    corr_lookup[(b, a)] = float(corr.loc[a, b])

        # 4. signal engine → trade plans → risk sizing
        prev_sides: dict[str, str] = {}
        if self._cache is not None:
            for p in self._cache.signals + self._cache.watchlist:
                prev_sides[p["symbol"]] = p["side"]

        plans: list[TradePlan] = []
        flips: list[str] = []
        for symbol, df in history.items():
            try:
                plan = build_plan(
                    symbol, df, self.cfg.signal,
                    forecast=forecasts.get(symbol),
                    strength=strength,
                )
            except (ValueError, IndexError, KeyError):
                continue
            # exit instruction when a previous signal has flipped sides
            prev = prev_sides.get(symbol)
            if prev in ("BUY", "SELL") and plan.side in ("BUY", "SELL") and prev != plan.side:
                close_word = "SELL" if prev == "BUY" else "BUY TO COVER"
                plan.actions.insert(
                    0,
                    f"⛔ SIGNAL FLIPPED ({prev} → {plan.side}): if you still hold the "
                    f"recommended {prev} position — {close_word} NOW and exit at market.",
                )
                plan.notes.insert(0, f"Signal flipped from {prev} to {plan.side} — exit the old trade.")
                flips.append(f"{plan.display}: previous {prev} signal has flipped to {plan.side} — "
                             f"close the old position NOW and stand aside (or reverse).")
            if plan.side in ("BUY", "SELL"):
                decision = self.risk.can_open(plan)
                plan.notes.append(
                    f"Size: {plan.lots:.2f} lots ({plan.units:,.0f} units) — "
                    f"risking ${plan.risk_amount:,.2f} ({self.cfg.risk.risk_per_trade_pct}% of equity)."
                    if decision.accepted else f"Risk veto: {decision.reason}"
                )
            plans.append(plan)

        plans.sort(key=lambda p: (p.side == "WAIT", -p.confidence))
        active = [p for p in plans if p.side != "WAIT"]
        watch = [p for p in plans if p.side == "WAIT"][:10]

        # 5. distilled instruction stream: when to buy → predicted outcome →
        #    when to sell — one command object per instrument
        instructions = self._build_instructions(active, watch)

        # 5. portfolio-level report + insights
        report = MarketReport(
            generated_at=time.time(),
            data_source=self.data.source_label,
            timeframe=self.cfg.data.timeframe,
            currency_strength=[
                {"currency": cur, "score": round(float(sc), 2)}
                for cur, sc in strength.scores.items()
            ],
            dollar_index=round(strength.dollar_index, 2),
            breadth=round(strength.breadth, 3),
            signals=[p.to_dict() for p in active],
            watchlist=[p.to_dict() for p in watch],
            instructions=instructions,
            correlation=_corr_payload(corr),
            risk=self._risk_payload(active, corr_lookup),
            ml=self._ml_payload(forecasts),
        )
        report.summary = self._summary(report, plans)
        report.insights = flips + self._insights(report, plans, strength)
        report.generated_at = time.time()
        self._cache = report
        return report

    # ------------------------------------------------------------------

    def _build_instructions(self, active: list[TradePlan],
                            watch: list[TradePlan]) -> list[dict[str, Any]]:
        """The user-facing command stream: tells when to buy, predicts the
        outcome, and instructs exactly when/how to sell."""
        out: list[dict[str, Any]] = []
        for p in active:
            if p.side == "BUY":
                verdict, action = "BUY NOW", "buy"
                one_line = (
                    f"BUY {p.display} NOW @ {p.entry:.5g} → predicted "
                    f"{p.target / p.entry - 1:+.2%} to {p.target:.5g} "
                    f"(P≈{p.p_target_first:.0%}) → SELL {p.target:.5g} (50% at +1.5R, "
                    f"rest trailed to {p.target2:.5g}) / SELL ALL at stop {p.stop:.5g} "
                    f"/ time stop {p.time_stop_bars} bars"
                )
            else:
                verdict, action = "SELL NOW", "sell"
                one_line = (
                    f"SELL {p.display} NOW (short) @ {p.entry:.5g} → predicted "
                    f"{p.target / p.entry - 1:+.2%} to {p.target:.5g} "
                    f"(P≈{p.p_target_first:.0%}) → BUY BACK {p.target:.5g} (50% at +1.5R, "
                    f"rest trailed to {p.target2:.5g}) / BUY BACK ALL at stop {p.stop:.5g} "
                    f"/ time stop {p.time_stop_bars} bars"
                )
            out.append({
                "uid": p.symbol,
                "symbol": p.symbol,
                "display": p.display,
                "verdict": verdict,
                "action": action,
                "confidence": round(p.confidence, 3),
                "entry": round(p.entry, 6),
                "prediction": p.predicted_outcome,
                "p_target_first": round(p.p_target_first, 3),
                "expected_bars": p.expected_bars,
                "one_line": one_line,
                "exit_orders": [
                    {"label": "take profit", "price": round(p.target, 6),
                     "instruction": f"sell {self.cfg.signal.partial_profit_fraction * 100:.0f}% at "
                                    f"{p.target:.5g} (+{self.cfg.signal.partial_profit_r:.1f}R), "
                                    f"then move the stop to breakeven"},
                    {"label": "sell the rest", "price": round(p.target2, 6),
                     "instruction": f"trail the remainder toward {p.target2:.5g}"},
                    {"label": "stop loss", "price": round(p.stop, 6),
                     "instruction": f"sell ALL at {p.stop:.5g} if the prediction is wrong"},
                    {"label": "time stop", "price": None,
                     "instruction": f"close at market if unresolved after "
                                    f"{p.time_stop_bars} bars — the predicted move never came"},
                    {"label": "flip exit", "price": None,
                     "instruction": "sell immediately if the system flips to a contrary signal"},
                ],
                "actions": p.actions,
            })
        for p in watch[:5]:
            out.append({
                "uid": p.symbol,
                "symbol": p.symbol,
                "display": p.display,
                "verdict": "DO NOT BUY",
                "action": "wait",
                "confidence": round(p.confidence, 3),
                "entry": round(p.entry, 6),
                "prediction": p.predicted_outcome,
                "p_target_first": 0.0,
                "expected_bars": 0,
                "one_line": (
                    f"DO NOT BUY {p.display} — no edge (score {p.score:+.2f}). "
                    f"Trigger: score crosses ±{self.cfg.signal.entry_threshold} → "
                    f"the system will instruct BUY or SELL."
                ),
                "exit_orders": [],
                "actions": p.actions,
            })
        return out

    def _risk_payload(self, plans: list[TradePlan],
                      corr_lookup: dict[tuple[str, str], float]) -> dict[str, Any]:
        total_risk = sum(p.risk_amount for p in plans if p.side != "WAIT")
        max_rr = max((p.risk_reward for p in plans if p.side != "WAIT"), default=0.0)
        from .risk import kelly_fraction as kf
        wins = [p for p in plans if p.side != "WAIT"]
        est_wr = float(np.mean([p.confidence for p in wins])) * 0.5 + 0.25 if wins else 0.5
        return {
            "equity": self.cfg.risk.equity,
            "risk_per_trade_pct": self.cfg.risk.risk_per_trade_pct,
            "planned_risk_total": round(total_risk, 2),
            "planned_risk_pct": round(total_risk / self.cfg.risk.equity * 100, 2),
            "max_open_positions": self.cfg.risk.max_open_positions,
            "max_risk_reward": round(max_rr, 2),
            "kelly_reference_pct": round(kf(est_wr) * 100, 2),
            "daily_loss_limit_pct": self.cfg.risk.daily_loss_limit_pct,
            "max_drawdown_pct": self.cfg.risk.max_drawdown_pct,
        }

    def _ml_payload(self, forecasts) -> dict[str, Any]:
        if not forecasts:
            return {"enabled": False, "note": "ML disabled or insufficient data"}
        accs = [f.accuracy for f in forecasts.values() if f.accuracy is not None]
        bull = sum(1 for f in forecasts.values() if f.p_up > 0.55)
        bear = sum(1 for f in forecasts.values() if f.p_up < 0.45)
        return {
            "enabled": True,
            "models_trained": len(forecasts),
            "avg_accuracy": round(float(np.mean(accs)), 3) if accs else None,
            "bullish_models": bull,
            "bearish_models": bear,
            "neutral_models": len(forecasts) - bull - bear,
            "horizon_bars": self.cfg.ml.horizon,
        }

    def _summary(self, report: MarketReport, plans: list[TradePlan]) -> dict[str, Any]:
        active = [p for p in plans if p.side != "WAIT"]
        buys = [p for p in active if p.side == "BUY"]
        sells = [p for p in active if p.side == "SELL"]
        regimes: dict[str, int] = {}
        for p in plans:
            regimes[p.regime] = regimes.get(p.regime, 0) + 1
        best = active[0] if active else None
        return {
            "pairs_scanned": len(plans),
            "buy_signals": len(buys),
            "sell_signals": len(sells),
            "watchlist_count": len(plans) - len(active),
            "regimes": regimes,
            "avg_confidence": round(float(np.mean([p.confidence for p in active])), 2) if active else 0.0,
            "best_setup": best.to_dict() if best else None,
            "market_mood": (
                "risk-on" if report.breadth > 0.58 else
                "risk-off" if report.breadth < 0.42 else "mixed"
            ),
        }

    def _insights(self, report: MarketReport, plans: list[TradePlan],
                  strength: CurrencyStrength) -> list[str]:
        """Natural-language 'when to invest / take profit / stop loss' feed."""
        out: list[str] = []
        strongest = strength.ranking[:3]
        weakest = strength.ranking[-3:][::-1]
        out.append(
            f"Currency strength — strongest: {', '.join(strongest)}; "
            f"weakest: {', '.join(weakest)}. "
            f"Prefer longs in strong currencies vs weak ones."
        )
        mood = report.summary.get("market_mood", "mixed")
        out.append(
            f"Market breadth is {report.breadth:.0%} positive across "
            f"{report.summary.get('pairs_scanned', 0)} instruments → {mood} tape. "
            + ("Risk-on flows favour commodity FX (AUD/NZD/CAD) and Yen crosses."
               if mood == "risk-on"
               else "Risk-off flows favour USD/CHF/JPY safe havens."
               if mood == "risk-off"
               else "No dominant theme — trade individual setups, keep size modest.")
        )
        active = [p for p in plans if p.side != "WAIT"]
        for p in active[:3]:
            action = "BUY" if p.side == "BUY" else "SELL (short)"
            out.append(
                f"WHEN TO BUY/SELL — {p.display}: the system instructs {action} NOW near "
                f"{p.entry:.5g} (confidence {p.confidence:.0%}, composite edge {p.score:+.2f}, "
                f"regime {p.regime.replace('_', ' ')})."
            )
            out.append(
                f"PREDICTED OUTCOME — {p.display}: {p.predicted_outcome}"
            )
            out.append(
                f"WHEN TO SELL/EXIT — {p.display}: take profit (sell) {p.target:.5g} "
                f"= 1:{p.risk_reward:.2f}R; stop-loss (sell all) {p.stop:.5g} "
                f"({p.stop_dist / p.atr:.1f} ATR away); sell the rest on the trail to "
                f"{p.target2:.5g}; time-stop close after {p.time_stop_bars} bars; "
                f"sell immediately if the signal flips."
            )
        if not active:
            out.append("INSTRUCTION: no qualified setups — DO NOT BUY yet. "
                       "Capital preservation is a position; watch the watchlist for triggers.")
        vol_hot = [p for p in plans if p.regime == "volatile"]
        if len(vol_hot) > max(2, len(plans) * 0.3):
            out.append(
                f"⚠ {len(vol_hot)} of {len(plans)} markets are in a high-volatility regime: "
                f"reduce size 25-50%, widen stops to 2 ATR, and avoid mean-reversion entries."
            )
        strong_set, weak_set = set(strongest), set(weakest)
        stacked = {}
        for p in active:
            for cur in (p.symbol[:3], p.symbol[3:6]):
                stacked[cur] = stacked.get(cur, 0) + 1
        for cur, n in sorted(stacked.items(), key=lambda kv: -kv[1]):
            if n >= 3:
                out.append(
                    f"⚠ {cur} appears in {n} active setups — correlated exposure. "
                    f"Cap at {self.cfg.risk.max_positions_per_currency} positions per currency."
                )
                break
        dd_note = (
            f"Risk engine: risking {self.cfg.risk.risk_per_trade_pct}% per trade on "
            f"${self.cfg.risk.equity:,.0f} equity, daily loss limit "
            f"{self.cfg.risk.daily_loss_limit_pct}%, max drawdown throttle "
            f"{self.cfg.risk.max_drawdown_pct}%."
        )
        out.append(dd_note)
        return out

    # ------------------------------------------------------------------
    # helpers used by the API
    # ------------------------------------------------------------------

    def pair_frame(self, symbol: str) -> Optional[pd.DataFrame]:
        history = self._history or self.load_data()
        return history.get(symbol)

    def pair_report(self, symbol: str) -> Optional[dict[str, Any]]:
        """Deep-dive report for one pair (chart payload + plan + context)."""
        history = self._history or self.load_data()
        df = history.get(symbol)
        if df is None:
            return None
        from .indicators import compute_all
        ind = compute_all(df).dropna(subset=["ema20"])
        strength = self._strength or compute_strength(history)
        forecast = self.forecaster.forecasts.get(symbol)
        plan = build_plan(symbol, df, self.cfg.signal,
                          forecast=forecast, strength=strength)
        regime = detect_regime(df, self.cfg.signal.adx_trend_threshold)

        tail = ind.tail(300)
        payload = {
            "symbol": symbol,
            "display": BY_SYMBOL[symbol].display if symbol in BY_SYMBOL else symbol,
            "timeframe": self.cfg.data.timeframe,
            "regime": regime.name,
            "adx": round(regime.adx, 1),
            "vol_ratio": round(regime.vol_ratio, 2),
            "plan": plan.to_dict(),
            "forecast": None if forecast is None else {
                "p_up": round(forecast.p_up, 3),
                "expected_move_pct": round(forecast.expected_move_pct, 5),
                "vol_ratio": round(forecast.vol_ratio, 2),
                "accuracy": forecast.accuracy,
                "feature_importance": forecast.feature_importance,
            },
            "candles": [
                {
                    "t": int(ts.timestamp()),
                    "o": round(float(r["open"]), 6),
                    "h": round(float(r["high"]), 6),
                    "l": round(float(r["low"]), 6),
                    "c": round(float(r["close"]), 6),
                }
                for ts, r in tail.iterrows()
            ],
            "overlays": {
                "ema20": [round(float(v), 6) if np.isfinite(v) else None for v in tail["ema20"]],
                "ema50": [round(float(v), 6) if np.isfinite(v) else None for v in tail["ema50"]],
                "ema200": [round(float(v), 6) if np.isfinite(v) else None for v in tail["ema200"]],
                "bb_upper": [round(float(v), 6) if np.isfinite(v) else None for v in tail["bb_upper"]],
                "bb_lower": [round(float(v), 6) if np.isfinite(v) else None for v in tail["bb_lower"]],
            },
            "rsi": [round(float(v), 2) if np.isfinite(v) else None for v in tail["rsi14"]],
        }
        return payload


def _corr_payload(corr: pd.DataFrame) -> dict[str, Any]:
    if corr.empty:
        return {"labels": [], "matrix": []}
    labels = list(corr.columns)
    matrix = [[round(float(corr.loc[a, b]), 3) for b in labels] for a in labels]
    return {"labels": labels, "matrix": matrix}
