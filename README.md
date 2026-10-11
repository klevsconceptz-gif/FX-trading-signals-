# klevs FX trading App

An end-to-end FX market intelligence platform that **traces signals across the
entire FX market** (23 currency pairs + gold & silver), **forecasts direction and
volatility** with machine learning, and tells you **when to invest, where to take
profits, and where the stop loss belongs** — with full portfolio risk management
and an event-driven backtester.

> ⚠ **Educational software.** Nothing here is financial advice. This platform is
> analysis/simulation only — it never sends orders to a broker. Trading FX
> involves substantial risk of loss.

---

## What it does

| Stage | Module | Output |
|-------|--------|--------|
| **1 · Trace** | `fxbot/data.py` | OHLC history for the whole universe: live hourly bars (yfinance) or daily ECB rates (Frankfurter), with a deterministic **correlated currency-factor simulator** as the fallback so the entire market stays coherent and the pipeline works offline |
| **2 · Measure** | `fxbot/indicators.py`, `fxbot/strength.py` | 20+ indicators (EMA/SMA/RSI/MACD/Bollinger/ATR/ADX/Stochastic/CCI/Donchian/Ichimoku…), cross-sectional **currency strength** ranking, regime detection (trend / range / volatile), correlation matrix |
| **3 · Predict** | `fxbot/features.py`, `fxbot/ml.py` | Per-pair gradient-boosting ensembles forecasting **direction P(up), expected move and volatility** over the next bars — walk-forward, time-ordered, honest OOS accuracy |
| **4 · Decide** | `fxbot/signals.py` | Six weighted factors (trend, momentum, mean-reversion, breakout, ML, strength differential) → composite score → full **trade plan**: entry, stop-loss, take-profit, stretch target, R:R, partial-profit schedule, narrative |
| **5 · Protect** | `fxbot/risk.py` | ATR-based position sizing (fixed % equity risk), per-currency exposure caps, correlation down-sizing, daily-loss circuit breaker, drawdown throttle, Kelly reference |
| **6 · Validate** | `fxbot/backtest.py` | Event-driven backtester: next-open execution (no lookahead), conservative intrabar fills, spread + slippage, partial profits, breakeven stops, trailing stops → win rate, profit factor, expectancy (R), Sharpe, Sortino, max DD |
| **7 · Present** | `fxbot/server.py`, `static/` | Live dashboard: market pulse, currency strength, signals table, candle charts with plan levels, correlation heatmap, insight feed, backtest lab |

## Quick start

```bash
bash setup.sh                # creates .venv and installs everything
source .venv/bin/activate

# landing page + bot (subscribe monthly → instant access)
python -m uvicorn fxbot.server:app --host 0.0.0.0 --port 8000
#   → http://localhost:8000      landing page (pricing, subscribe)
#   → http://localhost:8000/app  the bot (subscriber-gated)

# terminal market scan (all 32 instruments)
python -m scripts.scan

# backtest the signal engine on one pair
python -m scripts.backtest --symbol EURUSD

# train the ML forecasters and report out-of-sample accuracy
python -m scripts.train
```

## Landing page & monthly subscriptions

`/` is the marketing landing page: hero, features, how-it-works, sample
instruction feed, **monthly pricing tiers** (Starter $29 · Pro $79 · Elite $199),
testimonials and FAQ. **Registration is attached to the paywall** — picking a plan
opens the shared auth form (Register / Log in / Forgot password) and creates your
account plus an active monthly plan; subsequent logins use the same form.

**Login credentials are limited to four fields**: username, email, phone number
and password (any of the first three identifies the account). Password policy:
minimum **10 characters with uppercase, lowercase, number and symbol**, with a
live checklist + strength meter and a show/hide (👁) toggle. Forgot password uses
**hashed email auth**: a 7-digit code is issued to the account email, only its
PBKDF2 hash is stored, it expires in 15 minutes and locks out after 5 wrong
attempts. (Demo build returns the code in the API response instead of sending
email — remove `dev_code` when wiring a real email provider.)

| Endpoint | Purpose |
|----------|---------|
| `POST /api/register` | create account (username, email, phone, password) + monthly plan → access token |
| `POST /api/auth/login` | log in with username **or** email **or** phone + password → re-issued token |
| `POST /api/auth/forgot` | email auth: issue 7-digit reset code (stored hashed only) |
| `POST /api/auth/reset` | verify code + set a new policy-compliant password → token |
| `GET /api/auth/password-policy` | password rules for the UI checklist |
| `POST /api/subscribe` | plan-only demo signup (quick demo access) |
| `GET /api/subscription/{token}` | subscription status (validates the app gate) |

Passwords are hashed with **PBKDF2-HMAC-SHA256 (200k iterations)**, per-user
salt, constant-time comparison. The checkout is a clearly-labelled **demo** (no
payment is processed). To go live, swap `_checkout()` in `fxbot/server.py` for a
Stripe Checkout Session + webhook — the register/login/token flow is
production-shaped already. The dashboard at `/app` is gated: no active
subscription → paywall overlay with "Create account & subscribe", "Explore the
demo", "Log in" and "Forgot password".

Run the test-suite:

```bash
python -m pytest tests/ -q
```

### Admin account (unrestricted access)

The admin is provisioned by the operator from environment variables — the
credentials are never written into the code or the repository:

```bash
export KFX_ADMIN_USERNAME='Admin@klev.fx'     # required
export KFX_ADMIN_PASSWORD='...'               # required, must meet the password policy
export KFX_ADMIN_EMAIL='admin@klev.fx'        # optional (defaults to the username)
export KFX_ADMIN_PHONE='+1 555 000 0000'      # optional
.venv/bin/python -m uvicorn fxbot.server:app --host 0.0.0.0 --port 8000
```

The admin logs in on the paywall ("Log in") with the username, email or phone.
Their session carries `role: "admin"`, plan `Admin` and `access: "unrestricted"`.
The Admin plan is never offered at self-service signup or checkout. Accounts
are held in memory, so the admin is re-provisioned from the environment on every start.

## Dashboard

`python -m fxbot.server` serves a self-contained trading terminal (no CDN
dependencies) at `http://localhost:8000`:

- **Market pulse** — data source, DXY-style dollar strength, market breadth, mood
- **Currency strength grid** — strongest → weakest across every pair containing each currency
- **Signals table** — every asset with signal side, confidence, regime, predicted
  move, entry / take-profit / stop-loss, R:R, lot size and dollar risk (sortable)
- **Pair detail** — candle chart with EMA20/50/200, Bollinger bands, TP/SL/entry
  lines, RSI panel, full trade-plan narrative and factor breakdown
- **Correlation matrix** — see stacked exposure before you take it
- **Backtest lab** — run any pair with your equity/risk settings, equity curve + trade log

## Example CLI output

```
=== FX MARKET SCAN · source: synthetic (correlated factor model) · 1h ===
scanned 32 instruments | 1 BUY · 1 SELL · 30 watch | mood: mixed | breadth 53% | DXY -0.5

--- CURRENCY STRENGTH (strongest → weakest) ---
  XAG     +3.3 ▲     JPY     +0.8 ▲     EUR     +0.6 ▲   ...
  USD     -0.5 ▼     CAD     -0.5 ▼     AUD     -1.1 ▼

--- ACTIVE SIGNALS ---
PAIR      SIDE  CONF  REGIME          PRICE      ENTRY         TP         SL   R:R    LOTS    RISK$
EUR/AUD   BUY   27%   trend_up       1.6216     1.6216     1.6283     1.6177  1.67    0.19    75.00

--- INSIGHTS ---
  • Currency strength — strongest: XAG, JPY, EUR; weakest: AUD, NOK, ZAR. ...
  • EUR/AUD BUY — composite edge +0.29 (confidence 27%, regime trend up).
    Invest on strength above 1.6216; stop-loss 1.6177 (1.5 ATR away);
    take first profit at 1.6283 (1.7R) and trail toward 1.6336.
```

## The signal engine in one picture

```
            ┌─ trend (EMA20/50/200 + ADX)          0.30 ┐
            ├─ momentum (RSI/MACD/ROC)             0.20 │
            ├─ mean-reversion (%B/Stoch)   ×1.8 in range │
factors ────┼─ breakout (Donchian position)        0.10 ├──► composite ──► ≥0.25 → BUY / ≤-0.25 → SELL
            ├─ ML P(up) gradient boosting           0.20 │                  else WAIT
            └─ currency strength differential      0.10 ┘
                                          (weights × regime)

Trade plan:  entry = next open
             SL    = entry ∓ 1.5·ATR (1.2× in range, widened if vol forecast hot, beyond S/R)
             TP    = entry ± 2.5·ATR (1.8× in range)  — guaranteed ≥ 1.5 R:R
             TP2   = 3R / structure
             plan  = 50% off at +1.5R → stop to breakeven → trail remainder to 3R
             size  = (equity × 0.75%) / |entry − SL|
```

## The instruction lifecycle — buy → predict → sell

Every signal is delivered as an imperative 3-stage instruction, not just data:

| Stage | What the system tells you |
|-------|---------------------------|
| **① WHEN TO BUY** | `BUY NOW — open a long at market ≈ 1.0850` (or `SELL NOW` to short; or `DO NOT BUY YET` on the watchlist) |
| **② PREDICTED OUTCOME** | "Model expects EUR/USD to trade higher (+0.42% over the next 8 bars, P(up)=58%). Plan: +0.62% to take-profit vs −0.37% to stop (1:1.67 R:R). Estimated chance take-profit is hit before the stop ≈ 37%; typically resolved in ~6 bars." |
| **③ WHEN TO SELL / EXIT** | imperative sell steps: take-profit order (sell 50% at +1.5R → stop to breakeven → sell the rest on the trail to 3R) · stop-loss sell-all at the invalidation level · **time stop** (sell after 36 bars if the predicted move never came) · **sell immediately if the signal flips** (the engine tracks prior signals and emits `⛔ SIGNAL FLIPPED — SELL NOW`) |

The backtester enforces the same lifecycle it preaches: partial-profit sells,
breakeven stops, trailing sells, time-stop exits and flip exits are all simulated.

## Project layout

```
fxbot/
  config.py       # risk / signal / ML / data settings (dataclasses)
  universe.py     # 32 instruments: 7 majors, 15 crosses, 8 exotics, XAU, XAG
  data.py         # provider chain + correlated factor-model market simulator
  indicators.py   # vectorized TA library (no C dependencies)
  features.py     # ML feature engineering (strictly backward-looking)
  ml.py           # direction + return + volatility forecasters (walk-forward)
  strength.py     # currency strength meter, regimes, correlation matrix
  signals.py      # multi-factor signal engine → trade plans
  risk.py         # position sizing, exposure caps, circuit breakers
  backtest.py     # event-driven backtester + performance metrics
  engine.py       # orchestrator: scan → predict → plan → insights
  server.py       # FastAPI JSON API + dashboard
static/           # self-contained web terminal (vanilla JS + canvas)
scripts/          # scan.py · backtest.py · train.py CLIs
tests/            # 43 tests: indicators, signals, risk, backtest, ML, API
RESOURCES.md      # research survey (GitHub repos, papers, data sources)
```

## Configuration

All knobs live in `fxbot/config.py`:

- **RiskConfig** — equity (default $10,000), risk/trade 0.75%, max 8 open
  positions, ≤2 per currency, min R:R 1.5, 3% daily loss limit, 15% DD throttle
- **SignalConfig** — factor weights, entry threshold 0.25, ATR multipliers
  (SL 1.5 / TP 2.5 trend, 1.2 / 1.8 range), partial-profit plan
- **MLConfig** — horizon 8 bars, HistGradientBoosting, time-ordered 80/20 split
- **DataConfig** — timeframe `1h` or `1d`, 3000 bars, provider order, synthetic seed

Every scan and backtest is deterministic for a given seed — reproducible research.

## API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | marketing landing page (subscribe monthly) |
| GET | `/app` | the bot dashboard (subscriber-gated) |
| GET | `/api/health` | status + config |
| GET | `/api/universe` | instrument list |
| GET | `/api/scan?refresh=true` | full-market report (signals, insights, strength, correlations) |
| GET | `/api/instructions` | distilled buy → predict → sell command stream |
| GET | `/api/pair/{symbol}` | candles, indicators, trade plan, forecast |
| POST | `/api/backtest` | `{symbol, equity, risk_per_trade_pct, bars}` → metrics, equity curve, trades |
| POST | `/api/subscribe` | monthly subscription signup (demo checkout) |
| POST | `/api/register` | account + plan signup (see the subscription section) |
| POST | `/api/auth/login` | log in with username, email or phone + password |
| POST | `/api/auth/forgot` · `/api/auth/reset` | hashed email reset-code flow |
| GET | `/api/auth/password-policy` | password rules |
| GET | `/api/subscription/{token}` | subscription status |

`POST /api/login` (email-only) was retired: it allowed sign-in without a password.

## Live data

Live mode is **on by default** in the server and CLI. Providers:

| Provider | Data | Needs |
|----------|------|-------|
| `yfinance` (Yahoo Finance) | hourly FX + gold/silver (730 days) | `pip install -r requirements-live.txt` |
| `frankfurter` (ECB reference rates, `api.frankfurter.dev/v2`) | daily fiat FX only | nothing (no key) |

Rules: a provider is used only when its bar size matches the timeframe (daily
rates are never stretched into hourly bars); live data is all-or-nothing per
refresh so real and simulated series are never mixed; repeated failures pause a
provider for 10 minutes. `Refresh` (or `/api/scan?refresh=true`) reloads the data.
The dashboard chip and `data_source` field show `live (…)` or
`synthetic (live unavailable: <reason>)`.

```bash
pip install -r requirements-live.txt   # once
KFX_LIVE_DATA=1 .venv/bin/python -m uvicorn fxbot.server:app --host 0.0.0.0 --port 8000
.venv/bin/python -m scripts.scan --live   # CLI
```

Set `KFX_LIVE_DATA=0` to force the reproducible synthetic market (used by the test-suite).
The former `exchangerate.host` provider was removed: its API now requires a paid access key.
Swap in broker-grade feeds (OANDA v20, MetaTrader 5) by implementing the provider
interface in `fxbot/data.py`.

## Research

See [RESOURCES.md](RESOURCES.md) for the full survey of GitHub repos
(backtrader, backtesting.py, trading-signals, FXBot, currency-strength
indicators…), academic work (Yıldırım et al. 2021 LSTM ensemble, gradient-boosting
FX predictors), industry risk-management practice (ATR stop matrices, position
sizing formulas), and free data sources — with the design decision each one
informed.

## Disclaimer

Markets involve substantial risk of loss. Past backtest performance does not
guarantee future results. This software is provided for education and research
only, without warranty of any kind. Never trade money you cannot afford to lose.
