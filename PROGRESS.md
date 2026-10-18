# Progress Log — klevs FX trading App

Session save point · branch `arena/310b7038-fx-trading-signals` · 2026-10-18
Status: **round 13 complete on the branch** — Cloudflare app Python dependencies installed locally · 139 passed · pip dependency check clean · review fixes saved

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
  composite score → full trade plan (next-bar entry, ATR/structure stop, configured
  first partial target at max(partial R, minimum R:R), and separate stretch target)

### 2. Buy → Predict → Sell instruction lifecycle
- **① WHEN TO BUY**: `BUY NOW @ …` / `SELL NOW (short)` / `DO NOT BUY YET` + trigger
- **② PREDICTED OUTCOME**: signed target/stop moves, R:R, ML expectation with
  side-aligned probability, P(target before stop), expected resolution bars
- **③ WHEN TO SELL**: configured partial fraction at the plan's first target →
  breakeven stop → manage the runner toward target2, plus stop-loss and time stop.
  Opposite signals produce an exit instruction.
- Backtester now enters only on the bar after each signal, uses the plan's first
  and runner targets, converts quote P&L via prior-bar FX rates, exits on opposite
  signals, and includes terminal forced-close P&L in equity metrics.

### 3. Risk management (`risk.py`)
Account-currency sizing with instrument-specific lot sizes; scan recommendations
are reserved together under position-count, per-currency exposure/risk, and
correlation caps. Daily-loss and drawdown breakers require live account P&L and
positions and are not active in this analysis-only app; half-Kelly remains a
reference only.

### 4. klevs FX trading App (dashboard + API)
- **Landing page** (`/`): hero, features, how-it-works, instruction showcase,
  30-day plan cards (Starter $29 · Pro $79 · Elite $199), FAQ. Plan buttons open the
  shared account + payment modal (see round 11)
- **Auth** (`fxbot/auth.py` + `static/auth.{js,css}`): credentials limited to
  username, email, phone number, password (any of the first 3 identifies login);
  PBKDF2-HMAC-SHA256 200k-iters hashing + constant-time verify; password policy
  ≥10 chars with upper/lower/number/symbol — live checklist, strength meter and
  show/hide 👁 toggle; forgot password = hashed email auth (7-digit code, hash-only
  storage, 15-min TTL, 5-attempt lockout, delivered by SMTP, never returned in an
  API response unless `KLEVS_DEV_RESET_CODES=1`)
- **Paywall**: `/app` requires paid access; market endpoints return 401/403 without it
- API (current): `/api/health` · `/api/universe` · `/api/scan` · `/api/instructions` ·
  `/api/pair/{symbol}` · `POST /api/backtest` · `POST /api/register` ·
  `POST /api/auth/login` · `POST /api/auth/forgot` · `POST /api/auth/reset` ·
  `GET /api/auth/password-policy` · payment and operator routes (round 11).
  Retired: `POST /api/login` (email-only), `POST /api/subscribe`,
  `GET /api/subscription/{token}`, the demo checkout and `dev_code` in responses
- Dark trading terminal: live **instruction feed** (auto-refresh 5 min), market
  pulse (DXY/breadth/mood), currency-strength grid, sortable signals table,
  canvas candle charts with plan levels + RSI, correlation heatmap, backtest lab
- Branding: exact name **klevs FX trading App** (title, header KFX, footer, API)
- Thread-safe engine (lock), no-store headers, self-contained (no CDN)

### 5. Tooling & quality
- CLIs: `scripts/scan.py` · `scripts/backtest.py` · `scripts/train.py`
- Latest review run: **139 passed** (live-data dependencies installed; 1 existing
  Starlette/httpx deprecation warning); includes conversion, portfolio allocation,
  backtest timing/exits, ML purge, session revocation, and concurrent payment-confirmation regressions.
- `ruff.toml` (F,E9,I,B,SIM,C4,RUF) — All checks passed
- `RESOURCES.md` — GitHub/paper/data-source research survey with design mapping

### 6. Admin account
- Operator-provisioned admin (`KFX_ADMIN_USERNAME` / `KFX_ADMIN_PASSWORD` [+ `_EMAIL`, `_PHONE`]) seeded at server start via `_bootstrap_admin_from_env()` → `AccountStore.seed_admin()`. No credentials in code or git.
- Admin role/plan (`role: admin`, plan `admin`, `access: unrestricted`) never selectable at register or at payment (422).
- Admin username `Admin@klev.fx` is accepted through the admin path only (user usernames stay `[A-Za-z0-9_.-]`).
- Tests: 95/95 (10 new admin tests). Ruff clean. Live check: username + email login 200, wrong password 401, admin plan self-select 422. Server stopped after.

### 7. Full system check (round 9)
- Tests 95/95 in the working venv **and** in a fresh venv built from `requirements.txt` (pandas 3.0, sklearn 1.9, fastapi 0.143). Ruff clean. Compileall + `node --check` on all static JS OK.
- API sweep (TestClient, 52 s): all 32 instruments → 200; backtest bounds (249→422, 250→200, 20001→422, unknown symbol→404); auth edge cases (SQL-like identifiers, 129-char passwords, wrong types, duplicate usernames, weak passwords, retired `/api/login`); 96/96 concurrent requests → 200. Zero 500s.
- Live boot (default config): all pages, static assets and API routes return expected codes. Server stopped afterwards.
- Fixed docs: README API table listed the retired `/api/login` (now lists the auth routes); PROGRESS test count 69 → 79.
- Known: first `/api/scan` after a cold boot takes ~34 s (trains 32 ML models); cached afterwards (ms). Candidate fix: warm the cache in a background thread at startup.

### 8. Live market data (round 10)
- Status: implemented and unit-tested; **not verified against live providers from this sandbox** (they are unreachable here). Run `KFX_LIVE_DATA=1 .venv/bin/python -m scripts.scan --live` on a machine with internet access to confirm live output.
- Optional install: `requirements-live.txt` (yfinance), attempted by `setup.sh`; the app falls back to the labelled synthetic market if it is missing or unreachable.
- Sandbox check: Frankfurter, exchangerate.host and Yahoo Finance are **unreachable** from this sandbox (TLS fails); PyPI, npm and GitHub work. No live FX data can be fetched here, so this session's data is synthetic — labelled as such everywhere.
- Bugs fixed:
  - Server never tried live data (engine built with `prefer_live=False`) → now on by default, `KFX_LIVE_DATA=0` to disable.
  - `scan(refresh=true)` never reloaded data → now reloads.
  - Frankfurter used a retired URL/shape → now `api.frankfurter.dev/v2/rates`.
  - Daily ECB rates were upsampled into hourly bars → providers gated by native bar size.
  - Mixed live/synthetic series possible → all-or-nothing per refresh.
  - exchangerate.host requires a paid key and its response format is undocumented → removed.
  - No backoff on dead hosts → per-provider circuit breaker (3 failures → 10 min pause).
- Provider chain: `yfinance` (hourly, `requirements-live.txt`) → `frankfurter` (daily only).
- Tests: `tests/test_live_data.py` (16 offline tests: parsing, native-bar gating, all-or-nothing, tz/dedupe/trim, breaker, server refresh).
- Offline behaviour verified live: fallback in <1 s with reason shown in `data_source`.
- Known: the synthetic market is deterministic per seed and bar count, so it does not move in real time between refreshes.

### 9. Payments, operator console and database (round 11)
- **Stripe unlinked.** The demo checkout, `_checkout()` hook and `/api/subscribe` are removed.
  No payment path can grant access without an operator decision.
- **Payment methods**: crypto (operator-supplied wallet address per network) or bank wire
  (operator-supplied account details). Each invoice snapshots the pay-to details it was
  created with. The operator enters the values in `/admin` → Payment details; nothing is
  pre-filled, and a method with no details is hidden from buyers.
- **Lifecycle**: `awaiting_payment` → `submitted` (hash or wire reference) → `confirmed`
  (grants 30 days, stacked onto any active access) or `rejected` (reason shown to the buyer).
  Superseded unpaid invoices become `cancelled`.
- **Operator console** (`/admin`, `static/admin.{html,js}`): sign-in gate for the operator
  only; payment queue with confirm/reject; accounts and access with manual grant and revoke
  (note required); payment details. Every write is server-enforced and audited.
- **Database** (`fxbot/db.py`, SQLAlchemy 2): tables `users`, `user_sessions`, `payments`,
  `access_grants`, `settings`. `KLEVS_DATABASE_URL` selects SQLite (default
  `data/klevs.db`, owner-only, git-ignored) or PostgreSQL. Accounts are no longer held
  in memory and no longer re-created from environment variables on each start (the operator
  account is still provisioned from `KFX_ADMIN_*`). Session tokens are stored as SHA-256 hashes.
- **Auth routes**: `/api/me`, `/api/auth/logout`, bearer-token dependencies `current_user`,
  `require_access` (paid), `require_admin`. Login with a wrong password and an unknown account
  return the same `401`.
- **Tests**: `tests/test_billing.py` (14: full crypto and wire lifecycles, reject and retry,
  stacking and expiry, invoice isolation, operator-only routes, audit identity),
  `tests/test_api.py` (gating 401/403, page routes), `tests/test_auth.py` (30). Suite total: 115 passed.
- **Browser check** (not stored in the repo): a jsdom run against a live server drove the
  landing page, buyer registration, invoice, reference submission, operator confirm and
  reject, the paid and unpaid app, and the operator sign-in gate. 35/35 checks passed.
- **Zero state fixed** (checked in a run): before the first scan the Data chip read only "…"
  and the feed was empty for the whole ~44 s cold scan. It now reads "scanning…" with a note,
  shows "unavailable" plus the server error if the scan fails, and resolves to the real data
  source (`synthetic`).
- **Payment view race fixed**: the plan view could be clicked before its payment methods had
  loaded. It now shows a loading state and keeps "Create invoice" disabled until they arrive.
- **System check (round 11, second pass)**: found and fixed
  - password reset did not revoke existing sessions → every session is now deleted on reset;
  - `/api/auth/forgot` response carried a `delivered` field only for real accounts (an
    enumeration oracle) → removed from the response, logged server-side instead;
  - reset failures revealed account state ("no reset requested", "expired", "too many
    attempts") → one generic message for all of them;
  - the no-pending-reset path used `KeyError`, so the UI showed the message wrapped in quotes.
  - Regression tests added to `tests/test_auth.py` (33 passing in the file).
  - Known trade-off: after 10 failed logins the login route returns 429 for an existing account
    but 401 for an unknown one, which reveals existence once lockout triggers. Removing it
    would weaken the lockout, so it is left as a conscious choice (per-IP rate limiting would be
    the fix).
- **Checks, third pass**: ruff, JS syntax, HTML structure, 114 tests, the security probe (21/21),
  the scan CLI (live request falls back to labelled synthetic), the backtest CLI and API
  (identical results), and a clean `setup.sh` rebuild all pass.
  - Backtest baseline: the "sanctioned" figures in earlier notes (EURUSD 77 trades, PF 1.67,
    +15.0 %; XAUUSD 87 / 1.97 / +25.0 %; GBPJPY 102 / 1.43 / +13.8 %) could **not** be reproduced
    exactly from any commit in that review snapshot (`376873b` through that round's HEAD all gave
    EURUSD 78 trades, PF 1.815, +17.5 %). Those figures predate the next-open and lifecycle fixes;
    the updated deterministic baseline is recorded in the current `tests/test_backtest.py`.
  - Synthetic bar timestamps still end at the current hour (`fxbot/data.py`), so trade dates in
    the output shift between runs even though the metrics do not. Anchoring to a fixed date is a
    product decision left open.
  - `ruff format --check` would reformat 26 files. The repo does not use the formatter, so it
    was not applied.
- **Open items (product decisions and inputs needed)**: the operator must provide the wallet
  addresses and bank details; crypto amounts are fixed USD prices, so the operator must
  convert and verify the amount received by hand; no automatic on-chain or bank verification
  exists; the Pro/Elite feature list still names items that are not built (for example
  webhooks and multi-account profiles).

### 10. Deployment packaging and hosting options
- **Guide**: `docs/CLOUDFLARE_DEPLOYMENT.md`. Architecture: Cloudflare DNS, TLS, WAF and
  rate limiting in front; a Cloudflare Tunnel to an origin host running the app in Docker
  or under systemd; no inbound ports. Covers PostgreSQL, Tunnel setup, zone settings,
  the `/api/auth/*` rate-limit rule per plan, Cloudflare Access in front of `/admin` and
  `/api/admin/*`, origin lockdown, verification, backups and troubleshooting.
- **Requirements**: `docs/CLOUDFLARE_REQUIREMENTS.md` lists the accounts and plans, origin
  specs (memory still to be measured), the egress allow-list (including TCP/UDP 7844 for
  the tunnel), configuration, data stores, decisions still needed, and a go-live gate.
- **Files**: `Dockerfile` (non-root, one uvicorn worker, health check on `/api/health`),
  `.dockerignore` (excludes `.env`, databases, models, Git data), `deploy/klevs.env.example`
  (every `KLEVS_`/`KFX_` variable, no secrets), `deploy/klevs.service` (systemd, bound to
  `127.0.0.1`), `tests/test_deploy_files.py` (drift checks for the above).
- **Verified locally**: production uvicorn command starts; `/api/health`, admin login and
  `/admin` respond; a restart with a new `KFX_ADMIN_PASSWORD` rotates the admin password
  (the env value is authoritative on every start). Full suite passes.
- **Not verified**: Docker image build, `cloudflared` connector, live PostgreSQL, Cloudflare
  dashboard screens. The guide says so.
- **Estimated monthly cost** (third-party price trackers, not vendor-verified for the
  origin VM and database): Cloudflare path ~$74 (Pro $20/yr billing, Tunnel and Access
  free up to 50 users, 2 vCPU/4 GB VM ~$24, managed PostgreSQL 2 GB ~$30). Render ~$90
  (Pro workspace $25, 1 CPU/2 GB web $25, 1 CPU/2 GB Postgres $40; prices from
  render.com/pricing). AWS-native ~$90 (EC2 t3.medium ~$30, RDS db.t4g.small ~$23,
  ALB ~$22, WAF ~$8, storage and transfer extra).
- **Gaps to close before launch**: login rate limiting moves into the app if the edge
  rule is not used; `/admin` protection needs Access, an app-level allowlist or an SSO
  proxy; `/api/health` exposes risk configuration publicly; the 429 lockout response can
  reveal whether an account exists; the admin password committed in an earlier commit
  must be rotated.
- **Repo note**: the local branch was re-pointed to `origin/arena/641dff05-fx-trading-signals`
  (`3a5413f`) after the local clone was found at `main`'s base commit. The working tree was
  verified identical to that commit before the reset.

### 12. Review remediation (2026-10-18)
- Removed same-bar backtest lookahead: signals use completed bars and entries fill at the next open.
- Added shortest-path FX conversion; risk sizing and backtest P&L use account currency and
  instrument-specific lot sizes. Backtest conversion rates are lagged to avoid peeking.
- Allocated scan recommendations as a proposed portfolio with position-count, currency,
  per-currency-risk, and correlation limits; vetoed setups now say not to trade. Pair detail
  reuses the portfolio-approved plan.
- Aligned the configured first partial target and instruction text with backtest fills; added
  opposite-signal exits and recorded final forced-close P&L in terminal equity.
- Purged forward-label overlap at the ML split; bootstrap admin password rotation revokes
  existing sessions; payment confirmation now uses an atomic conditional state change.
- Regression coverage added across risk/conversion, engine allocation, backtest lifecycle,
  ML split, auth sessions, and concurrent payment confirmation. At remediation completion,
  137 passed and 2 optional-provider tests were skipped; Ruff, compileall, JavaScript syntax,
  CLI GBPJPY backtest, and diff checks passed. The dashboard refreshes every five minutes and
  ignores overlapping refresh requests.

### 13. Cloudflare application dependencies (2026-10-18)
- Recreated the local Python 3.11 `.venv` and installed core requirements, live-data
  requirements (`yfinance`), and the PostgreSQL binary driver (`psycopg[binary]`).
- Verified imports and PostgreSQL dialect resolution; `pip check` reports no broken requirements.
- Latest pytest run with live-data dependencies installed: **139 passed**, one existing
  Starlette/httpx deprecation warning (the optional provider tests now run instead of skipping).
- Reproduce the install with:
  `.venv/bin/python -m pip install -r requirements.txt -r requirements-live.txt 'psycopg[binary]>=3.1'`
- Docker and `cloudflared` are not installed in this sandbox. They are origin-host/container
  requirements, not Python packages; no Cloudflare account, tunnel, token, or production host
  was configured. Follow `docs/CLOUDFLARE_DEPLOYMENT.md` on the origin host.

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
.venv/bin/python -m pytest tests/ -q        # full battery (see round 11 for counts)
.venv/bin/python -m scripts.scan            # whole-market scan
.venv/bin/python -m scripts.backtest --symbol EURUSD
.venv/bin/python -m uvicorn fxbot.server:app --host 0.0.0.0 --port 8000
#   → /  landing page (plans & payment)   → /app  the bot   → /admin  operator console
```

## ⚠ Disclaimer
Educational software — analysis & simulation only. Not financial advice.
No orders are ever sent to a broker.
