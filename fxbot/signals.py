"""Multi-factor signal engine.

Every instrument receives a composite score in ``[-1, +1]`` built from six
independent factors (each itself in ``[-1, +1]``):

============  ==========================================================
Factor        Meaning
============  ==========================================================
trend         EMA20/50/200 alignment + ADX-confirmed directional regime
momentum      RSI position, MACD histogram & slope, ROC
reversion     Bollinger %B and Stochastic extremes (fade in range regimes)
breakout      Donchian channel position (ride breakouts in trend regimes)
ml            P(up) from the gradient-boosting forecaster
strength      base-currency strength minus quote-currency strength
============  ==========================================================

A composite beyond ``entry_threshold`` emits a BUY / SELL trade plan with
volatility-adaptive levels:

* stop-loss  = entry ∓ k_sl · ATR  (1.5x trend / 1.2x range; research
  consensus for FX intraday/swing), clamped beyond recent structure;
* take-profit = entry ± k_tp · ATR (2.5x trend / 1.8x range) with a
  guaranteed minimum risk-reward of 1.5;
* a partial-profit plan (50% at +1.5R, breakeven stop, trail the rest).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

import numpy as np
import pandas as pd

from .config import SignalConfig
from .indicators import compute_all, support_resistance
from .ml import Forecast
from .strength import Regime, CurrencyStrength, detect_regime
from .universe import BY_SYMBOL


# ---------------------------------------------------------------------------
# factor scores
# ---------------------------------------------------------------------------

def _trend_score(ind: pd.DataFrame) -> float:
    close = float(ind["close"].iloc[-1])
    e20, e50, e200 = (float(ind[k].iloc[-1]) for k in ("ema20", "ema50", "ema200"))
    if not np.isfinite(e200):
        e200 = e50
    score = 0.0
    score += np.clip((close - e50) / (abs(close) * 0.01 + 1e-12), -1, 1) * 0.35
    score += np.clip((e20 - e50) / (abs(close) * 0.005 + 1e-12), -1, 1) * 0.35
    score += np.clip((e50 - e200) / (abs(close) * 0.01 + 1e-12), -1, 1) * 0.30
    adx_val = float(ind["adx"].iloc[-1])
    if not np.isfinite(adx_val):
        adx_val = 0.0
    confidence = float(np.clip(adx_val / 40.0, 0.35, 1.0))   # weak ADX → dampen
    out = float(np.clip(score, -1, 1) * confidence)
    return out if np.isfinite(out) else 0.0


def _momentum_score(ind: pd.DataFrame) -> float:
    rsi = float(ind["rsi14"].iloc[-1])
    hist = float(ind["macd_hist"].iloc[-1])
    slope = float(ind["macd_hist"].iloc[-1] - ind["macd_hist"].iloc[-4]) \
        if len(ind) > 4 else 0.0
    roc_v = float(ind["roc10"].iloc[-1])
    atr_v = float(ind["atr14"].iloc[-1]) or 1e-9

    s_rsi = np.clip((rsi - 50.0) / 30.0, -1, 1) if np.isfinite(rsi) else 0.0
    s_macd = np.clip(hist / (atr_v * 0.75), -1, 1) if np.isfinite(hist) else 0.0
    s_slope = np.clip(slope / (atr_v * 0.75), -1, 1) if np.isfinite(slope) else 0.0
    s_roc = np.clip(roc_v / 1.5, -1, 1) if np.isfinite(roc_v) else 0.0
    out = float(np.clip(0.35 * s_rsi + 0.30 * s_macd + 0.15 * s_slope + 0.20 * s_roc, -1, 1))
    return out if np.isfinite(out) else 0.0


def _reversion_score(ind: pd.DataFrame) -> float:
    pct_b = float(ind["bb_pct_b"].iloc[-1])
    k = float(ind["stoch_k"].iloc[-1])
    if not np.isfinite(pct_b):
        return 0.0
    # fades extremes: %B > 1 → overbought → negative score
    s_bb = -np.clip((pct_b - 0.5) * 2.0, -1, 1)
    s_st = -np.clip((k - 50.0) / 50.0, -1, 1)
    return float(np.clip(0.6 * s_bb + 0.4 * s_st, -1, 1))


def _breakout_score(ind: pd.DataFrame) -> float:
    up = float(ind["donchian_upper"].iloc[-1])
    lo = float(ind["donchian_lower"].iloc[-1])
    close = float(ind["close"].iloc[-1])
    if not np.isfinite(up) or up == lo:
        return 0.0
    pos = (close - lo) / (up - lo) * 2.0 - 1.0
    # strongest near the edges (breaking out), neutral mid-channel
    return float(np.sign(pos) * abs(pos) ** 1.5)


def _strength_score(symbol: str, strength: Optional[CurrencyStrength]) -> float:
    if strength is None:
        return 0.0
    ins = BY_SYMBOL.get(symbol)
    if ins is None:
        return 0.0
    base = float(strength.scores.get(ins.base, 0.0))
    quote = float(strength.scores.get(ins.quote, 0.0))
    return float(np.clip((base - quote) / 80.0, -1, 1))


# ---------------------------------------------------------------------------
# trade plan
# ---------------------------------------------------------------------------

@dataclass
class TradePlan:
    symbol: str
    display: str
    side: str                 # 'BUY' | 'SELL' | 'WAIT'
    score: float              # composite in [-1, 1]
    confidence: float         # 0..1
    price: float
    entry: float
    stop: float
    target: float
    target2: float            # stretch target (3R / structure)
    risk_reward: float
    atr: float
    stop_dist: float
    expected_move_pct: float
    p_up: float
    regime: str
    factors: dict[str, float]
    notes: list[str] = field(default_factory=list)
    # --- the explicit user-facing instruction lifecycle ---
    actions: list[str] = field(default_factory=list)   # imperative steps
    predicted_outcome: str = ""                        # "what the model expects"
    p_target_first: float = 0.0                        # P(target hit before stop)
    expected_bars: int = 0                             # typical resolution time
    time_stop_bars: int = 0                            # exit if unresolved by then
    # position sizing (filled by the risk manager)
    units: float = 0.0
    lots: float = 0.0
    risk_amount: float = 0.0
    position_value: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("score", "confidence", "price", "entry", "stop", "target",
                  "target2", "risk_reward", "atr", "stop_dist",
                  "expected_move_pct", "p_up", "p_target_first",
                  "units", "lots", "risk_amount", "position_value"):
            v = d[k]
            if isinstance(v, float):
                d[k] = round(v, 6) if abs(v) < 1e6 else v
        return d


def build_plan(symbol: str, ind: pd.DataFrame, cfg: SignalConfig,
               forecast: Optional[Forecast] = None,
               strength: Optional[CurrencyStrength] = None,
               precomputed: bool = False) -> TradePlan:
    if precomputed and "ema50" in ind.columns:
        pass
    else:
        ind = compute_all(ind.dropna(subset=["close"]))
    if len(ind.dropna(subset=["ema50"])) < 50:
        raise ValueError(f"{symbol}: not enough data")

    regime = detect_regime(ind, cfg.adx_trend_threshold)
    trending = regime.name in ("trend_up", "trend_down")
    display_name = BY_SYMBOL[symbol].display if symbol in BY_SYMBOL else symbol

    factors = {
        "trend": _trend_score(ind),
        "momentum": _momentum_score(ind),
        "reversion": _reversion_score(ind),
        "breakout": _breakout_score(ind),
        "ml": forecast.direction_score if forecast else 0.0,
        "strength": _strength_score(symbol, strength),
    }

    # in ranging markets the reversion factor earns more weight, and vice versa
    w = {
        "trend": cfg.w_trend,
        "momentum": cfg.w_momentum,
        "reversion": cfg.w_reversion,
        "breakout": cfg.w_breakout,
        "ml": cfg.w_ml,
        "strength": cfg.w_strength,
    }
    if not trending:
        w["reversion"] *= 1.8
        w["trend"] *= 0.7
    factors = {k: (float(v) if np.isfinite(v) else 0.0) for k, v in factors.items()}
    total_w = sum(w.values())
    score = sum(factors[k] * w[k] for k in factors) / total_w
    if not np.isfinite(score):
        score = 0.0

    # agreement bonus: many factors pointing the same way
    aligned = sum(1 for v in factors.values() if v * score > 0.15)
    confidence = float(np.clip(abs(score) * 0.75 + aligned * 0.05, 0.0, 1.0))
    if forecast and forecast.accuracy:
        confidence = float(np.clip(confidence * (0.85 + 0.3 * forecast.accuracy), 0, 1))

    close = float(ind["close"].iloc[-1])
    atr_v = float(ind["atr14"].iloc[-1])
    if not np.isfinite(atr_v) or atr_v <= 0:
        atr_v = close * 0.002

    sr = support_resistance(ind.tail(300))
    notes: list[str] = []
    actions: list[str] = []

    if abs(score) < cfg.entry_threshold:
        side = "WAIT"
        stop = target = target2 = close
        rr = 0.0
        p_target = 0.0
        expected_bars = 0
        notes.append(
            f"No edge right now — composite score {score:+.2f} below the "
            f"{cfg.entry_threshold} threshold. Keep on the watchlist."
        )
        actions = [
            f"DO NOT BUY YET — the system is not instructing an entry on {display_name}.",
            f"Trigger to act: composite score crosses +{cfg.entry_threshold} → the system "
            f"will instruct BUY; crosses −{cfg.entry_threshold} → instructs SELL (short).",
        ]
        predicted_outcome = (
            f"No position predicted to be taken. Model leans "
            f"{'up' if forecast and forecast.p_up > 0.52 else 'down' if forecast and forecast.p_up < 0.48 else 'neutral'} "
            f"(P(up)={forecast.p_up:.0%})" if forecast else "No model forecast available."
        )
    else:
        side = "BUY" if score > 0 else "SELL"
        k_sl = cfg.atr_stop_mult if trending else cfg.atr_stop_mult_range
        k_tp = cfg.atr_target_mult if trending else cfg.atr_target_mult_range
        # dynamic widening if the vol model forecasts a hotter market
        if forecast and forecast.vol_ratio > 1.25:
            k_sl *= float(np.clip(forecast.vol_ratio, 1.0, 1.5))
            notes.append(f"Volatility forecast {(forecast.vol_ratio - 1) * 100:+.0f}% "
                         f"— stop widened to {k_sl:.2f} ATR.")
        if side == "BUY":
            stop = close - k_sl * atr_v
            target = close + k_tp * atr_v
            # structure refinement: pull stop below nearest support if sensible
            supports = [s for s in sr["support"] if s < close]
            if supports:
                struct = supports[-1] - 0.25 * atr_v
                if close - struct < k_sl * atr_v * 1.4:
                    stop = struct
            # enforce min R:R by stretching the target if needed
            risk = close - stop
            rr = (target - close) / risk if risk > 0 else 0.0
            if rr < cfg.min_risk_reward:
                target = close + cfg.min_risk_reward * risk
                rr = cfg.min_risk_reward
            target2 = close + 3.0 * risk
            resistances = [r for r in sr["resistance"] if r > target]
            if resistances:
                target2 = max(target2, resistances[-1])
        else:
            stop = close + k_sl * atr_v
            target = close - k_tp * atr_v
            resistances = [r for r in sr["resistance"] if r > close]
            if resistances:
                struct = resistances[-1] + 0.25 * atr_v
                if struct - close < k_sl * atr_v * 1.4:
                    stop = struct
            risk = stop - close
            rr = (close - target) / risk if risk > 0 else 0.0
            if rr < cfg.min_risk_reward:
                target = close - cfg.min_risk_reward * risk
                rr = cfg.min_risk_reward
            target2 = close - 3.0 * risk
            supports = [s for s in sr["support"] if s < target]
            if supports:
                target2 = min(target2, supports[-1])

        # ---- outcome prediction -------------------------------------------
        # P(target before stop) on a driftless walk = 1/(1+R:R), nudged by the
        # model's directional probability — an estimate, not a guarantee.
        base_p = 1.0 / (1.0 + rr) if rr > 0 else 0.4
        edge = (forecast.p_up - 0.5) if forecast else 0.0
        drift_adj = edge if side == "BUY" else -edge
        p_target = float(np.clip(base_p + 0.5 * drift_adj, 0.05, 0.92))
        # expected boundary-resolution time for a random walk: d1*d2 / σ²
        expected_bars = int(max(4.0, round(abs(close - stop) * abs(target - close) / (atr_v ** 2))))

        dir_word = "higher" if side == "BUY" else "lower"
        move_pct = abs(target / close - 1.0) * 100.0
        if forecast:
            p_side = forecast.p_up if side == "BUY" else 1.0 - forecast.p_up
            model_line = (
                f"ML model: {forecast.expected_move_pct * 100:+.2f}% expected over the "
                f"next few bars (P(up)={forecast.p_up:.0%} → P(this {side.lower()})="
                f"{p_side:.0%}"
                + (f", OOS accuracy {forecast.accuracy:.0%}" if forecast.accuracy else "")
                + ")"
            )
        else:
            model_line = "ML model: not trained on this pair — plan relies on technical factors only"
        predicted_outcome = (
            f"Plan targets a move {dir_word} to the take-profit at {target:.5g} "
            f"(+{move_pct:.2f}%) vs the stop at {stop:.5g} "
            f"(−{abs(close - stop) / close * 100:.2f}%), risk-reward 1:{rr:.2f}. "
            f"{model_line}. Estimated chance the take-profit is reached before the "
            f"stop ≈ {p_target:.0%}; typically resolved in ~{expected_bars} bars."
        )

        # ---- explicit action sequence --------------------------------------
        if side == "BUY":
            actions = [
                f"① BUY NOW — open a long at market ≈ {close:.5g} (enter on the next candle).",
                f"② TAKE-PROFIT (sell): sell {cfg.partial_profit_fraction * 100:.0f}% at "
                f"{target:.5g} (+{cfg.partial_profit_r:.1f}R), then move the stop to breakeven.",
                f"③ SELL THE REST: trail toward {target2:.5g} (stretch target / structure).",
                f"④ STOP-LOSS (sell all): {stop:.5g} — if price touches this the prediction "
                f"is wrong; exit immediately and take the loss.",
                f"⑤ TIME STOP (sell): if the trade is unresolved after "
                f"{cfg.time_stop_bars} bars, close at market — the predicted move did not "
                f"materialize.",
                f"⑥ SELL EARLY if the system flips to a contrary signal on this pair.",
            ]
        else:
            actions = [
                f"① SELL NOW — open a short at market ≈ {close:.5g} (enter on the next candle).",
                f"② TAKE-PROFIT (buy to cover): cover {cfg.partial_profit_fraction * 100:.0f}% at "
                f"{target:.5g} (+{cfg.partial_profit_r:.1f}R), then move the stop to breakeven.",
                f"③ COVER THE REST: trail toward {target2:.5g} (stretch target / structure).",
                f"④ STOP-LOSS (buy to cover all): {stop:.5g} — if price touches this the "
                f"prediction is wrong; exit immediately and take the loss.",
                f"⑤ TIME STOP (cover): if the trade is unresolved after "
                f"{cfg.time_stop_bars} bars, close at market — the predicted move did not "
                f"materialize.",
                f"⑥ COVER EARLY if the system flips to a contrary signal on this pair.",
            ]

        notes.append(
            f"Regime: {regime.name.replace('_', ' ')} (ADX {regime.adx:.0f}). "
            f"{'Trend-following' if trending else 'Mean-reversion'} playbook."
        )
        notes.append(
            f"Enter on the next candle near {close:.5g}. "
            f"Stop-loss {stop:.5g} ({abs(close - stop) / atr_v:.2f} ATR) — "
            f"the trade thesis is invalidated there."
        )
        notes.append(
            f"Take profit {cfg.partial_profit_fraction * 100:.0f}% at {target:.5g} "
            f"(+{cfg.partial_profit_r:.1f}R), move stop to breakeven, "
            f"trail the rest toward {target2:.5g}."
        )
        if forecast:
            notes.append(
                f"ML model: P(up)={forecast.p_up:.0%} over the next few bars"
                + (f", out-of-sample accuracy {forecast.accuracy:.0%}." if forecast.accuracy else ".")
            )

    return TradePlan(
        symbol=symbol,
        display=display_name,
        side=side,
        score=float(score),
        confidence=confidence,
        price=close,
        entry=close,
        stop=float(stop),
        target=float(target),
        target2=float(target2),
        risk_reward=float(rr),
        atr=atr_v,
        stop_dist=float(abs(close - stop)),
        expected_move_pct=float(forecast.expected_move_pct) if forecast else 0.0,
        p_up=float(forecast.p_up) if forecast else 0.5,
        regime=regime.name,
        factors={k: float(v) for k, v in factors.items()},
        notes=notes,
        actions=actions,
        predicted_outcome=predicted_outcome,
        p_target_first=float(p_target),
        expected_bars=int(expected_bars),
        time_stop_bars=int(cfg.time_stop_bars),
    )
