# Progress Log — klevs FX trading App

Session save point · branch `arena/641dff05-fx-trading-signals` · 2026-10-10
Status: **complete & green** — 69/69 tests · ruff clean · 32-instrument stress verified

---

## ✅ Delivered

### 1. Whole-market prediction engine (`fxbot/`)
- **Universe**: 32 instruments — 7 majors, 15 crosses, 8 exotics, XAU/XAG
- **Data layer**: live provider chain (Frankfurter/ECB → exchangerate.host →
  yfinance) with a deterministic, correlated **currency-factor simulator**
  fallback (coherent crosses, regime-switching volatility, trend persistence)
- **Indicators** (`indicators.py`): SMA/EMA/WMA, RSI-Wilder, MACD, Bollinger,
  Keltner, Donchian, ATR-Wilder, ADX/DI, Stochastic, CCI, ROC, Momentum, OBV,
  Ichimoku, swing structure, support/resistance — pure pandas, no C deps
- **ML forecasting** (`ml.py`): per-pair HistGradientBoosting ensembles
  (direction P(up), expected move, volatility) — walk-forward, time-ordered
  splits, permutation feature importance, honest OOS accuracy reporting
- **Signal engine** (`signals.py`): 6 weighted factors (trend, momentum,
  mean-reversion, breakout, ML, currency-strength) × regime adaptation →
  composite score → full trade plan (entry, ATR TP/SL ≥ 1.5 R:R, structure
  refinement, partial-profit schedule)

### 2. Buy → Predict → Sell instruction lifecycle
- **① WHEN TO BUY**: `BUY NOW @ …` / `SELL NOW (short)` / `DO NOT BUY YET` + trigger
- **② PREDICTED OUTCOME**: signed target/stop moves, R:R, ML expectation with
  side-aligned probability, P(target before stop), expected resolution bars
- **③ WHEN TO SELL**: take-profit ladder (50% at +1.5R → breakeven → trail to 3R),
  stop-loss sell-all, **time stop** (36 bars), **flip exit** (`⛔ SIGNAL FLIPPED —
  SELL NOW` tracked across scans)
- Backtester enforces the same lifecycle (partials, breakeven, trailing,
  time_stop, target exits)

### 3. Risk management (`risk.py`)
ATR position sizing (fixed % equity risk), per-currency exposure caps,
correlation down-sizing, daily-loss circuit breaker, drawdown throttle,
half-Kelly reference (edge-case safe).

### 4. klevs FX trading App (dashboard + API)
- **Landing page** (`/`): hero, features, how-it-works, instruction showcase,
  **monthly pricing** (Starter $29 · Pro $79 · Elite $199), testimonials, FAQ,
  demo-checkout subscribe modal (Stripe-ready `_checkout()` hook)
- **Registration attached to the paywall**: plan CTA / paywall "Create account &
  subscribe" opens the shared auth modal (Register / Log in / Forgot password) in
  both `/` and `/app`; registering creates the account AND activates the plan
- **Auth** (`fxbot/auth.py` + `static/auth.{js,css}`): credentials limited to
  username, email, phone number, password (any of the first 3 identifies login);
  PBKDF2-HMAC-SHA256 200k-iters hashing + constant-time verify; password policy
  ≥10 chars with upper/lower/number/symbol — live checklist, strength meter and
  show/hide 👁 toggle; forgot password = hashed email auth (7-digit code, hash-only
  storage, 15-min TTL, 5-attempt lockout, code delivered via email in production,
  `dev_code` in demo response)
- **Subscriber gate**: `/app` checks the subscription token and shows a paywall
  overlay (Create account & subscribe / Explore demo / Log in / Forgot password)
- Dark trading terminal: live **instruction feed** (auto-refresh 5 min), market
  pulse (DXY/breadth/mood), currency-strength grid, sortable signals table,
  canvas candle charts with plan levels + RSI, correlation heatmap, backtest lab
- API: `/api/health` · `/api/universe` · `/api/scan` · `/api/instructions` ·
  `/api/pair/{symbol}` · `POST /api/backtest` · `POST /api/register` ·
  `POST /api/auth/login` · `POST /api/auth/forgot` · `POST /api/auth/reset` ·
  `GET /api/auth/password-policy` · `POST /api/subscribe` ·
  `GET /api/subscription/{token}` — all validated (insecure email-only
  `POST /api/login` retired)
- Branding: exact name **klevs FX trading App** (title, header KFX, footer, API)
- Thread-safe engine (lock), no-store headers, self-contained (no CDN)

### 5. Tooling & quality
- CLIs: `scripts/scan.py` · `scripts/backtest.py` · `scripts/train.py`
- **69 tests** (indicators, signals & instruction lifecycle, risk, backtest
  invariants/no-lookahead, ML, API, subscription, **auth/register/reset**) — all passing
- `ruff.toml` (F,E9,I,B,SIM,C4,RUF) — All checks passed
- `RESOURCES.md` — GitHub/paper/data-source research survey with design mapping

## 🐛 Bug-hunt history (all fixed & regression-tested)
1. Signal engine missing indicator computation (silent empty scans)
2. RSI on flat series returned 100 instead of 50 (false BUY on dead markets)
3. NaN ADX poisoning composite scores (false SELL on flat markets)
4. Backtest wiped banked partial profits on breakeven-stop exits; 3R target
   never taken; R-multiple mis-accounting
5. Synthetic market too mean-reverting for trend strategies (retuned factor
   model: realistic vol, near-zero short-horizon autocorr, multi-week trends)
6. `POST /api/backtest` HTTP 500 crash for bars < 250 → now 422 validation
7. `kelly_fraction()` ZeroDivisionError with zero avg-win size
8. Thread-race on shared engine under parallel requests → locked
9. Metals instruments built with misaligned positional args (TypeError)
10. Lint sweep: unused imports/locals, annotations, f-strings, import order

## ▶️ Resume commands
```bash
bash setup.sh                               # rebuild .venv (not stored in git)
.venv/bin/python -m pytest tests/ -q        # 69 tests
.venv/bin/python -m scripts.scan            # whole-market scan
.venv/bin/python -m scripts.backtest --symbol EURUSD
.venv/bin/python -m uvicorn fxbot.server:app --host 0.0.0.0 --port 8000
#   → /  landing page (subscribe monthly)   → /app  the bot
```

## ⚠ Disclaimer
Educational software — analysis & simulation only. Not financial advice.
No orders are ever sent to a broker.
