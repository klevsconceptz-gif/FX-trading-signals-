# Requirements: deploying KLEVS on the existing Cloudflare route

The checklist for the deployment described in `docs/CLOUDFLARE_DEPLOYMENT.md`. The application runs
on the Cloudflare Free plan, except where the table says otherwise. The signal generator runs as a
scheduled job outside Cloudflare, because it cannot run inside a Worker request.

## 1. Cloudflare account and plans

| Need | Plan | Used for | Status |
|---|---|---|---|
| Workers | Free (100,000 requests/day, 10 ms CPU per request) | The API Worker (`worker/`) | Checked on Cloudflare's limits page (12 Oct 2026). CPU is the risk for sign-in: see the deployment guide, section 2 |
| Workers | Workers Paid ($5 per month, 5 minutes CPU per request) | Only if sign-in needs more CPU than the Free plan allows | **A charged dependency.** Decision pending |
| D1 | Included with the account | Accounts, sessions, payments, access, audit log, signal snapshots (`signal_cache`) | **Free-tier allowances not verified here** |
| KV | Included with the account | Short-lived session data | **Free-tier allowances not verified here** |
| Pages | Included with the account | Static site and `/api` proxy | Not verified here |
| Zone and DNS | Your existing zone | Hostname for the Pages site and the API proxy | Required |

Not used on this route: R2, Containers, Email Service, Durable Objects, Tunnel.

## 2. Domain and DNS

- A zone on Cloudflare for the hostname that serves the app.
- The Worker's `workers_dev` is on in `worker/wrangler.jsonc`, so the API also has a
  `*.workers.dev` URL. The Pages function calls that URL by default.

## 3. Signal generation (scheduled publisher)

The signal engine (`fxbot/`, Python) runs as a scheduled publisher rather than inside the Worker.

| Item | Why | Requirements | Status |
|---|---|---|---|
| Publisher `scripts/publish_signals.py`, run by `.github/workflows/signals.yml` | A full scan takes about 27 seconds, which cannot run in a Worker request | Python 3.11 with `requirements.txt` and `requirements-live.txt`; about one run per hour | Built. Not run live yet |
| Scheduled runs | GitHub runs scheduled workflows only from the default branch | The change merged to `main` | Pending merge. Start manually before then |

## 4. Build and deploy pipeline

| Item | Where | Used for | Status |
|---|---|---|---|
| GitHub Actions: `.github/workflows/cloudflare-deploy.yml` | GitHub | Tests, migrations, Worker and secrets, Pages deploy (manual start) | Built. Not run live yet |
| GitHub Actions: `.github/workflows/signals.yml` | GitHub | The hourly signal publish | Built. Not run live yet |
| GitHub environment `production` | GitHub | Holds the build secrets; can require a reviewer | Set up by you (deployment guide, section 4a) |
| Build secrets: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `KFX_ADMIN_USERNAME`, `KFX_ADMIN_PASSWORD`, and optional admin and reset values | GitHub environment secrets | Passed to `wrangler secret put` on stdin, and to Wrangler for the publish. Never written into the build output | Set up by you |
| Variables: `CLOUDFLARE_PAGES_PROJECT` (`klv-fx-trading-signals`, required), `KLEVS_API_ORIGIN` (optional) | GitHub environment variables | Pages target, health check | Set up by you |
| Cloudflare API token | Cloudflare | Permissions: Workers Scripts, D1, KV, Pages (edit), account read. One account | Created by you |
| Pages project | Cloudflare | Hosts `dist/`, proxies `/api` to the Worker | Created once with `wrangler pages project create` |

**Non-Cloudflare dependencies:** GitHub Actions (runners for the deploy and the hourly publish),
`actions/*`, the npm and PyPI registries, and the live data sources below. The application itself
runs only on Cloudflare.

## 5. Application software

- Worker: Node.js 20 or later. Dependencies are in the root `package.json` (`wrangler`).
- Worker tests: `node --test worker/test/*.test.js` (20 tests).
- Publisher and FastAPI app: Python 3.11 with `requirements.txt`. `requirements-live.txt` adds `yfinance`.
- Python tests: `pytest`, and `ruff check fxbot tests scripts`.

## 6. Data stores

| Store | Where | Notes |
|---|---|---|
| D1 database `klev` | Cloudflare D1 | Schema in `worker/migrations/0001_initial.sql` (accounts, payments, access, audit) and `0002_signal_cache.sql` (signal snapshots). Migrations via `npm run worker:migrate:remote` |
| KV namespace `voom` | Cloudflare KV | Session metadata, short-lived |
| `signal_cache` table in D1 | Cloudflare D1 | One row per output (`scan`, `instructions`, `pair:<SYMBOL>`), overwritten on each publish. The Worker serves only the rows of the committed run, and only while they are fresh |
| SQLite `klevs.db` | FastAPI host disk | Only if the FastAPI app is used. Not replicated by this route |

## 7. Configuration and secrets

See the variable reference in the deployment guide, section 8. Secrets are set with
`wrangler secret put` or as GitHub `production` secrets, and are never committed.
`KLEVS_DEV_RESET_CODES` must stay unset in production.

## 8. Live market data (runtime sources)

| Destination | Made by | Used for | Status |
|---|---|---|---|
| Yahoo Finance, through `yfinance` (a community client) | Publisher on the GitHub runner | Hourly bars for the signals | **Chosen (live).** Check the terms of use before publishing (deployment guide, section 5) |
| `api.frankfurter.dev` | Publisher on the GitHub runner | Daily reference rates | **Chosen (live).** Same check |
| `KLEVS_RESET_EMAIL_URL` (your HTTPS endpoint) | Worker | Reset e-mail, with a bearer token | Optional. Not chosen |
| SMTP server (`KLEVS_SMTP_HOST`) | FastAPI app | Reset e-mail, if the FastAPI app is used for it | Optional. Not chosen |

The publisher refuses to publish any synthetic fallback, so a failed live fetch leaves the last
live snapshot in place.

## 9. Decisions

- [x] Signal generation: a scheduled publisher writes snapshots to D1, and the Worker serves them.
  Chosen. This replaces the earlier plan to port the engine to JavaScript, which has not started.
- [x] Live market data: Yahoo Finance and Frankfurter. Chosen. Terms still to check (section 8).
- [x] Scheduler: GitHub Actions, hourly. Chosen.
- [x] Pages project name: `klv-fx-trading-signals`. Chosen.
- [ ] Sign-in on the Free plan. Lower the PBKDF2 iteration count (and migrate existing hashes), pay for Workers Paid, or accept that sign-in fails (deployment guide, section 2).
- [ ] Reset e-mail: choose a provider and endpoint. Without one, operators reset passwords in the admin console.
- [ ] Hostname and zone for the Pages site and the API proxy.
- [ ] Create the GitHub `production` environment, its secrets and variables, and the Pages project.

## 10. Go-live gate

- [ ] `node --test worker/test/*.test.js` (20) and the Python suite pass. The deploy workflow's test job passes.
- [ ] The remote D1 migrations `0001` and `0002` apply to `klev`.
- [ ] A staging deploy: sign-up and sign-in succeed (no CPU-limit error in the logs).
- [ ] **Publish signals** succeeds on live data, and `/api/health` reports `signals.state: fresh`.
- [ ] A paid test account gets `200` from `/api/scan`, `/api/instructions` and `/api/pair/EURUSD`. Before the first publish, it gets `503`.
- [ ] Admin sign-in opens `/admin`. An admin password reset works and ends the user's sessions.
- [ ] Reset e-mail arrives (if reset e-mail is enabled).
- [ ] The terms of the live data sources have been checked and accepted.
