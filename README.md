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
| **5 · Protect** | `fxbot/risk.py` | Account-currency position sizing with instrument lot sizes; proposed-scan position-count, per-currency exposure/risk and correlation caps; Kelly reference. Daily-loss and drawdown breakers need live account state and are not active in this analysis-only app. |
| **6 · Validate** | `fxbot/backtest.py` | Event-driven backtester: next-open execution (no lookahead), lagged FX conversion for quote-currency P&L, conservative intrabar fills, plan-matched partial/runner targets, opposite-signal exits, terminal close included in equity metrics. |
| **7 · Present** | `fxbot/server.py`, `static/` | Live dashboard: market pulse, currency strength, signals table, candle charts with plan levels, correlation heatmap, insight feed, backtest lab |

## Quick start

```bash
bash setup.sh                # creates .venv and installs everything
source .venv/bin/activate

# landing page + bot + operator console
python -m uvicorn fxbot.server:app --host 0.0.0.0 --port 8000
#   → http://localhost:8000        landing page (plans, account, payment)
#   → http://localhost:8000/app    the bot (paid access only)
#   → http://localhost:8000/admin  operator console (verify payments, grant access)

# terminal market scan (all 32 instruments)
python -m scripts.scan

# backtest the signal engine on one pair
python -m scripts.backtest --symbol EURUSD

# train the ML forecasters and report out-of-sample accuracy
python -m scripts.train
```

## Cloudflare deployment

The deployment uses the existing Cloudflare route: the Worker in [`worker/`](worker/README.md)
(D1 and KV) and the Pages function in `functions/`. It runs on the Cloudflare Free plan except
for two open items: sign-in may exceed the Free plan's 10 ms CPU limit, and the Python signal
engine must run on a separate HTTPS host. See
[`docs/CLOUDFLARE_DEPLOYMENT.md`](docs/CLOUDFLARE_DEPLOYMENT.md) and
[`docs/CLOUDFLARE_REQUIREMENTS.md`](docs/CLOUDFLARE_REQUIREMENTS.md).

### Worker route

A D1/KV-backed Worker entrypoint and Wrangler config are in [`worker/`](worker/README.md). The Pages Function at `functions/api/[[path]].js` proxies same-origin API requests to `https://klv-fx-trading-signals.klevco.workers.dev` by default for production and preview builds; set the Pages variable `KLEVS_API_ORIGIN` to override it. The Worker binds D1 and KV using the configured resource IDs and implements account, admin, payment, access, and audit APIs; signal scan, instructions, pair detail, and backtest routes require the separate `KFX_SIGNAL_ENGINE_ORIGIN` adapter described in the Worker guide. Remote migrations, Cloudflare secrets, and deployment must still be applied in an authenticated Cloudflare environment.

## Landing page, payments & paid access

Access works like this — there is **no card checkout and no payment processor**:

1. The buyer creates an account on the landing page (username, email, phone number, password)
   and picks a plan: **Starter $29 · Pro $79 · Elite $199**, each for **30 days**.
2. The buyer pays the operator **directly** by **crypto** (to a wallet address the operator
   configured, on a network they name) or by **bank wire** (to the operator's account).
3. The buyer submits the transaction hash or wire reference. The payment waits in the
   operator queue as `submitted`.
4. The **operator verifies the payment** in `/admin` (wallet or bank statement) and clicks
   *Confirm & grant*. Only then is 30 days of access written to the database. Confirmation is
   an atomic one-time transition, so duplicate/concurrent clicks cannot create duplicate grants.
   A rejection carries a reason that the buyer sees, and the buyer can try again.
5. Each further confirmed payment adds 30 days, stacked onto any active access. Nothing
   renews automatically.

The `/app` dashboard is gated on that access: without it, the paywall shows "Choose a plan &
pay". Market endpoints (`/api/universe`, `/api/scan`, `/api/instructions`, `/api/pair/…`,
`/api/backtest`) return `401` without a session and `403` without paid access.

**Accounts.** Login credentials are limited to four fields: username, email, phone number and
password. Login accepts any of the first three as the identifier. Password policy: minimum
**10 characters with uppercase, lowercase, number and symbol**. Passwords are stored as
**PBKDF2-HMAC-SHA256 (200k iterations)** with a per-user salt. Forgot password sends a 7-digit
code to the account email. Only its hash is stored, it expires in 15 minutes and it locks out
after 5 wrong attempts. The code is never returned in an API response unless the local-only
flag `KLEVS_DEV_RESET_CODES=1` is set. Set the SMTP variables `KLEVS_SMTP_*` to deliver the
code by email. Login locks out for 15 minutes after 10 failures. Session tokens are stored
only as SHA-256 hashes. Rotating the environment-provisioned admin password revokes all
existing admin sessions.

**Database.** Accounts, sessions, payments, access grants and the operator's payment details
live in a database set by `KLEVS_DATABASE_URL`:

- Default: SQLite at `data/klevs.db`, created with owner-only permissions (git-ignored).
- Production: a PostgreSQL URL, for example
  `KLEVS_DATABASE_URL='postgresql+psycopg://klevs:…@db-host:5432/klevs?sslmode=require'`
  (install `psycopg[binary]` alongside the app). The schema is the same; only the URL changes.

| Endpoint | Who | Purpose |
|----------|-----|---------|
| `POST /api/register` | public | create account (username, email, phone, password) → session token |
| `POST /api/auth/login` | public | log in with username **or** email **or** phone + password (wrong password and unknown account both return `401`) |
| `POST /api/auth/forgot` | public | issue a 7-digit reset code (generic response) |
| `POST /api/auth/reset` | public | verify the code and set a new policy-compliant password |
| `GET /api/auth/password-policy` | public | password rules for the UI checklist |
| `GET /api/me` · `POST /api/auth/logout` | signed-in user | account, access state, own payments |
| `GET /api/payment-options` | signed-in user | plans, and which payment methods the operator has set up |
| `POST /api/payments` | signed-in user | create an invoice `KFX-XXXXXX` for `{plan, method, network}` |
| `GET /api/payments` | signed-in user | the user's own invoices |
| `POST /api/payments/{id}/reference` | signed-in user | submit the transaction hash / wire reference |
| `GET /api/admin/payments[?status=]` | operator | payment queue |
| `POST /api/admin/payments/{id}/confirm` · `…/reject` | operator | verify (grants 30 days) or reject with a reason |
| `GET /api/admin/users` | operator | accounts and access state |
| `POST /api/admin/users/{id}/grant` · `…/revoke` | operator | manual grant (note required) or revoke (reason required) |
| `GET` / `PUT /api/admin/payment-settings` | operator | crypto wallets (network + address) and wire details |
| `GET /api/admin/audit-log[?limit=]` | operator | recent payment decisions, access grants/revocations, and payment-setting changes |

The `/admin` **Activity log** tab and audit endpoint show who changed what, when, and the operator's
note. Wallet addresses and bank-account values are never copied into audit events. Existing payment
decisions are reconstructed from their decision records; older manual access records may not have
an exact action timestamp, and settings history begins when audit logging is deployed.

Run the test-suite:

```bash
python -m pytest tests/ -q
```

### Operator setup (admin account and payment details)

For the sign-in flow, Cloudflare Worker secrets, and troubleshooting, see the
[Admin login and access guide](docs/ADMIN_LOGIN.md).

The operator account is provisioned from environment variables. The credentials are never
written into the code or the repository:

```bash
export KFX_ADMIN_USERNAME='Admin@klev.fx'     # required
export KFX_ADMIN_PASSWORD='...'               # required, must meet the password policy
export KFX_ADMIN_EMAIL='admin@klev.fx'        # optional (defaults to the username)
export KFX_ADMIN_PHONE='+1 555 000 0000'      # optional
export KLEVS_DATABASE_URL='...'               # optional; defaults to data/klevs.db
.venv/bin/python -m uvicorn fxbot.server:app --host 0.0.0.0 --port 8000
```

Then open `http://localhost:8000/admin`, sign in, and go to **Payment details**:

- **Crypto wallets** — one address per network (for example `BTC`, `ETH`, `USDT-TRC20`).
  Buyers pay these addresses directly.
- **Bank wire details** — account name, account number, routing/SWIFT, IBAN, and the
  instructions buyers should follow.

A method with no details stays hidden from buyers and shows "not set up yet". The operator
enters these values; nothing is pre-filled.

The operator's own session has `role: "admin"`, plan `Admin` and unrestricted access. The
Admin role cannot be created or bought through self-service registration or payment.

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

Trade plan:  signal uses the completed close; entry = following bar open
             SL    = signal close ∓ ATR multiple (widened for hot volatility / structure)
             TP    = max(configured partial R, minimum R:R) × stop distance
             TP2   = stretch target from ATR / structure
             plan  = configured fraction off at TP → stop to breakeven → manage to TP2
             size  = account-currency risk budget ÷ (stop distance × quote conversion)
             lots  = base units ÷ instrument-specific lot size
```

## The instruction lifecycle — buy → predict → sell

Every signal is delivered as an imperative 3-stage instruction, not just data:

| Stage | What the system tells you |
|-------|---------------------------|
| **① WHEN TO BUY** | `BUY NOW — open a long at market ≈ 1.0850` (or `SELL NOW` to short; or `DO NOT BUY YET` on the watchlist) |
| **② PREDICTED OUTCOME** | "Model expects EUR/USD to trade higher (+0.42% over the next 8 bars, P(up)=58%). Plan: +0.62% to take-profit vs −0.37% to stop (1:1.67 R:R). Estimated chance take-profit is hit before the stop ≈ 37%; typically resolved in ~6 bars." |
| **③ WHEN TO SELL / EXIT** | imperative sell steps: take-profit order at the plan's configured first target → stop to breakeven → manage the remainder toward the stretch target · stop-loss sell-all at the invalidation level · **time stop** (sell after the configured number of bars) · **sell immediately if the signal flips** |

The backtester fills entries on the bar after the signal, uses the same first and
stretch targets as the plan, converts quote-currency P&L using rates from before
the exit bar, and includes opposite-signal, time-stop, and terminal exits. These
are simulations, not broker orders or guarantees.

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
tests/            # pytest regression suite: indicators, signals, risk, backtest, ML, auth, billing, API
RESOURCES.md      # research survey (GitHub repos, papers, data sources)
```

## Configuration

All knobs live in `fxbot/config.py`:

- **RiskConfig** — account currency (default USD), configured equity ($10,000),
  base risk/trade 0.75%, max 8 proposed positions, ≤2 per currency, max 2% risk
  per currency, min R:R 1.5. Daily-loss and drawdown settings are not live-enforced
  because this app has no broker/account P&L connection.
- **SignalConfig** — factor weights, entry threshold 0.25, ATR stop/runner-target
  multipliers (SL 1.5 / runner 2.5 trend, 1.2 / 1.8 range), partial-profit R and fraction
- **MLConfig** — horizon 8 bars, HistGradientBoosting, time-ordered 80/20 split;
  forward-label overlap is purged at the train/test boundary
- **DataConfig** — timeframe `1h` or `1d`, 3000 bars, provider order, synthetic seed

Every scan and backtest is deterministic for a given seed — reproducible research.

## API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | marketing landing page (plans, account, payment) |
| GET | `/app` | the bot dashboard (paid access) |
| GET | `/admin` | operator console (operator sign-in) |
| GET | `/api/health` | status + config |
| GET | `/api/universe` | instrument list |
| GET | `/api/scan?refresh=true` | full-market report (signals, insights, strength, correlations) |
| GET | `/api/instructions` | distilled buy → predict → sell command stream |
| GET | `/api/pair/{symbol}` | candles, indicators, trade plan, forecast |
| POST | `/api/backtest` | `{symbol, equity, risk_per_trade_pct, bars}` → metrics, equity curve, trades |
| POST | `/api/register` · `/api/auth/login` | account creation and sign-in (see the payments section) |
| POST | `/api/auth/forgot` · `/api/auth/reset` | hashed email reset-code flow |
| GET | `/api/auth/password-policy` | password rules |
| GET | `/api/me` | account + access state (bearer token) |
| GET · POST | `/api/payment-options` · `/api/payments` | plans, methods, invoices (bearer token) |
| POST | `/api/payments/{id}/reference` | submit a transaction hash / wire reference |
| GET · POST | `/api/admin/…` | operator queue, verification, grants, payment details (operator only) |

The market endpoints above require paid access. `POST /api/login` (email-only) and
`POST /api/subscribe` were retired: they allowed access without verification.

## Live data

Live mode is **on by default** in the server and CLI. Providers:

| Provider | Data | Needs |
|----------|------|-------|
| `yfinance` (Yahoo Finance) | hourly FX + gold/silver (730 days) | `pip install -r requirements-live.txt` and network access to Yahoo Finance |
| `frankfurter` (ECB reference rates, `api.frankfurter.dev/v2`) | daily fiat FX only | nothing (no key), network access to frankfurter.dev |

Live data is **optional**. `setup.sh` tries to install `requirements-live.txt` and carries on
if that fails. If no provider can be reached, the app uses the **synthetic** market and labels
it `synthetic (live unavailable: …)`. Nothing is presented as live when it is not.

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
