# Research & Resources

Survey of the internet and GitHub for FX trading bots, prediction models, signal
techniques, risk management and data sources — and how each finding was applied
in this platform.

---

## 1 · GitHub trading-bot & backtesting repos

| Stars | Repo | What it offers | How we used it |
|------:|------|----------------|----------------|
| 23.4k | [mementum/backtrader](https://github.com/mementum/backtrader) | Event-driven Python backtesting framework | Validated our bar-by-bar, next-open execution model (no lookahead) |
| 9.0k | [kernc/backtesting.py](https://github.com/kernc/backtesting.py) | Vectorized/visual backtester | Metric set we mirror: win rate, profit factor, expectancy, drawdown |
| 2.5k | [blankly-finance/blankly](https://github.com/blankly-finance/blankly) | Build/backtest/deploy algos in a few lines | Multi-asset abstraction pattern (stocks/crypto/forex) |
| 312 | [trentstauff/FXBot](https://github.com/trentstauff/FXBot) | Fully automated OANDA forex bot | Architecture reference for FX execution layering |
| 66 | [Mo-Khalifa96/Forex-Trading-Bot](https://github.com/Mo-Khalifa96/Forex-Trading-Bot) | Python + MetaTrader5 bot | MT5 integration path (kept out of core; analysis-only here) |
| 51 | [Jessika-Art/Trading-Bot-MT5-Forex](https://github.com/Jessika-Art/Trading-Bot-MT5-Forex) | MT5 Python bot | Position-management conventions (SL/TP orders, trailing) |
| — | [phuceh/fx-trading-bot](https://github.com/phuceh/fx-trading-bot) | Backtesting engine w/ EMA/RSI/ATR, risk manager, synthetic data | Closest structural cousin — our architecture generalizes it to the whole market |

## 2 · Technical-indicator libraries

| Stars | Repo | Notes |
|------:|------|-------|
| — | [bennycode/trading-signals](https://github.com/bennycode/trading-signals) (TS) | Production-grade indicator + strategy + ProtectedStrategy kill-switch design (SL/TP) |
| 667 | [kylejusticemagnuson/pyti](https://github.com/kylejusticemagnuson/pyti) | Pure-python indicators |
| 543 | [nardew/talipp](https://github.com/nardew/talipp) | Incremental (streaming) TA library |
| 377 | [TulipCharts/tulipy](https://github.com/TulipCharts/tulipy) | Tulip Indicators bindings (C) |
| 220 | [gregyjames/ZenithTA](https://github.com/gregyjames/ZenithTA) | Rust/numpy high-performance TA |
| 78 | [Bitvested/ta.py](https://github.com/Bitvested/ta.py) | Zero-dependency, 151 indicators |
| — | TA-Lib, pandas-ta, `ta` (PyPI) | De-facto standards |

**Decision:** implemented our own dependency-light `fxbot/indicators.py` (SMA, EMA,
WMA, RSI-Wilder, MACD, Bollinger, Keltner, Donchian, ATR-Wilder, ADX/DI,
Stochastic, CCI, ROC, Momentum, OBV, Ichimoku, swing structure, support/resistance)
so the platform installs anywhere without C builds.

## 3 · FX prediction / ML research

| Repo / paper | Finding | Applied as |
|--------------|---------|------------|
| [shenrunzhang/forex](https://github.com/shenrunzhang/forex) — LSTM ensemble (58% directional accuracy) implements [Yıldırım et al. 2021, *Forecasting directional movement of Forex data using LSTM with technical and macroeconomic indicators*](https://doi.org/10.1186/s40854-020-00220-2) | Ensemble of technical + macro models; entropy-based no-trade threshold | Our ML factor is one input among six; `P(up)` near 0.5 damps the composite instead of forcing trades |
| [michaeljf07/forex-predictor](https://github.com/michaeljf07/forex-predictor) | RF/GBM/Ridge on 90+ technical + cross-market features; strict time-ordered splits | Feature set design (returns lags, oscillator geometry, vol state, session effects); walk-forward accuracy reporting |
| [cenkcorapci/forex-prediction](https://github.com/topics/forex-prediction) | Deep-learning comparison on 15m USD/EUR | Horizon selection; caution on overfit deep nets for FX |
| [hayatoy/ml-forex-prediction](https://github.com/topics/forex-prediction) | Classic sklearn FX prediction | Baseline: gradient boosting remains the strongest small-data baseline |
| [Ilia-Abolhasani/forex-price-prediction](https://github.com/Ilia-Abolhasani/forex-price-prediction) | LSTM XAU/USD with explicit TP/SL outputs | Confirms "prediction → TP/SL decision" product shape |

**Decision:** gradient-boosted trees (sklearn `HistGradientBoosting`) — no
TensorFlow/CUDA requirement, honest time-ordered OOS accuracy (~45-55% on
synthetic random-walk-dominated data — the honest reality of FX direction
prediction; the edge comes from combining it with structure & regime factors).

## 4 · Risk management & TP/SL placement (industry practice)

| Source | Rule of thumb | Implemented in |
|--------|---------------|----------------|
| [TraderNest ATR stop framework](https://tradernest.ai/blog/atr-stop-loss) | SL = entry ∓ k·ATR; k=1.5-2.0 intraday FX, 2-3 swing; **position size = (equity × risk%) / (ATR × k)** | `fxbot/signals.py` (level engine), `fxbot/risk.py` (sizing) |
| [DXP Analytics stop-placement matrix](https://www.dxpa.in/resources/stop-loss-placement-guide) | 1.5× ATR → 15-20% noise stop-out rate, 2:1-3:1 achievable R:R; partial exits at 1.5R then trail (better Sharpe) | `atr_stop_mult=1.5`, `partial_profit_r=1.5`, breakeven stop, trail to 3R |
| [TradeZero ATR sizing](https://tradezero.com/blog/the-atr-position-sizing-formula-how-to-size-every-trade-around-volatility) | Stop first, size second; same dollar risk in high-vol weeks = smaller position | RiskManager: `units = risk_amount / |entry − stop|` |
| [AlphaEx Capital](https://www.alphaexcapital.com/stocks/technical-analysis-for-stock-trading/trading-strategies-using-technical-analysis/atr-based-stop-loss) | 1% risk/trade cap, 2%/day circuit breaker | `risk_per_trade_pct=0.75`, `daily_loss_limit_pct=3` |
| [Reddit r/Daytrading sizing calculator](https://www.reddit.com/r/Daytrading/comments/1gpy56b/atradvrisk_based_position_sizing_calculator/) | TP multiplier > SL multiplier (1.5-2.5× ATR); min R:R 1.5 | `min_risk_reward=1.5`, `atr_target_mult=2.5` |

## 5 · Currency strength & correlation (whole-market view)

| Source | Concept | Implemented in |
|--------|---------|----------------|
| [EarnForex/Currency-Strength-Matrix](https://github.com/EarnForex/Currency-Strength-Matrix) | Relative strength of majors computed across all their pairs | `fxbot/strength.py::compute_strength` |
| [Admiral Markets strength-meter guide](https://admiralmarkets.com/education/articles/forex-indicators/how-to-trade-using-a-forex-currency-strength-meter) | Correlation bands (±50 medium, ±76 strong); EURUSD/USDJPY ≈ -0.93 | Correlation heatmap + `correlation_risk_scale=0.5` on overlapping trades |
| [Valtorim/MT5-Portfolio-Correlation-Matrix](https://github.com/topics/mql4) | Hidden cross-pair exposure in portfolios | Per-currency position cap (`max_positions_per_currency=2`) |
| [ArpitaMohanty11/CURRENCY-ANALYSIS](https://github.com/ArpitaMohanty11/CURRENCY-ANALYSIS) | TradingView-style terminal: CSI matrix, triggers, reports | Dashboard layout: strength grid + signals table + insight feed |

## 6 · Free FX data sources (provider chain in `fxbot/data.py`)

1. **[Frankfurter.app](https://www.frankfurter.app)** — free ECB reference rates, no API key (daily).
2. **[exchangerate.host](https://exchangerate.host)** — free timeseries endpoint, no key.
3. **[yfinance](https://github.com/ranaroussi/yfinance)** — `EURUSD=X` etc., intraday up to 730d (optional dependency).
4. **MetaTrader5 (PyPI `MetaTrader5`)** — broker-grade tick/OHLC (Windows-only; not bundled).
5. **[OANDA v20 API](https://developer.oanda.com)** — what production bots (e.g. FXBot) use for live execution.
6. **Synthetic correlated factor model** (this repo) — deterministic fallback that
   generates the *entire* market coherently (each pair = exp(L_base − L_quote)
   over latent currency factors driven by shared risk/commodity shocks with
   regime-switching vol). Keeps every feature of the platform fully functional
   offline, with reproducible backtests.

## 7 · Broader reading

- [wangzhe3224/awesome-systematic-trading](https://github.com/wangzhe3224/awesome-systematic-trading) — curated systematic-trading resource list (data, backtesters, execution, research).
- [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) — agent-based "trading assistant" direction of travel.
- [cuemacro/finmarketpy](https://github.com/cuemacro/finmarketpy) — quant market analysis (finmarketpy + chartpy + findatapy).
- bennycode trading-signals monorepo docs — Strategy/ProtectedStrategy pattern with SL/TP kill switches, useful for a future live-execution layer.

---

## Key take-away decisions baked into this codebase

1. **Never trust a single factor** — composite of trend, momentum, mean-reversion,
   breakout, ML probability and cross-sectional currency strength, weighted by regime.
2. **Volatility-adaptive everything** — ATR-based stops/targets, position size
   inverse to stop distance, stop widened when the vol model forecasts heat.
3. **Plan the exit before the entry** — min 1.5R, partial profit at 1.5R →
   breakeven → trail to 3R.
4. **Portfolio-level risk** — per-currency caps, correlation down-sizing, daily
   loss circuit-breaker, drawdown throttle, half-Kelly reference only.
5. **Honest evaluation** — time-ordered splits, next-open execution, conservative
   intrabar fills (stop wins ties), spread + slippage costs.
