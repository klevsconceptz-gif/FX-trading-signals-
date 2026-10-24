# Requirements: deploying KLEVS on the existing Cloudflare route

The checklist for the deployment described in `docs/CLOUDFLARE_DEPLOYMENT.md`. The deploy path
uses only the Cloudflare resources the Free plan includes, except where the table says otherwise.

## 1. Cloudflare account and plans

| Need | Plan | Used for | Status |
|---|---|---|---|
| Workers | Free (100,000 requests/day, 10 ms CPU per request) | The API Worker (`worker/`) | Checked on Cloudflare's limits page (12 Oct 2026). CPU is the risk: see the deployment guide, section 2 |
| Workers | Workers Paid ($5 per month, 5 minutes CPU per request) | Only if sign-in needs more CPU than the Free plan allows | **A charged dependency.** Decision pending |
| D1 | Included with the account | Accounts, sessions, payments, access, audit log | **Free-tier allowances not verified here** |
| KV | Included with the account | Short-lived session and scan caches | **Free-tier allowances not verified here** |
| Pages | Included with the account | Static site and `/api` proxy | Not verified here |
| Zone and DNS | Your existing zone | Hostname for the Pages site and the API proxy | Required |

Not used on this route: R2, Containers, Email Service, Durable Objects, Tunnel.

## 2. Domain and DNS

- A zone on Cloudflare for the hostname that serves the app.
- The Worker's `workers_dev` is on in `worker/wrangler.jsonc`, so the API also has a
  `*.workers.dev` URL. The Pages function calls that URL by default.

## 3. Compute outside Cloudflare (optional)

| Item | Why | Requirements | Status |
|---|---|---|---|
| FastAPI signal engine | Market scan, instructions, pair detail and backtest use Python libraries that a Worker cannot run | An HTTPS host you control, 1 vCPU and 2 GB or more (to be measured), Python 3.11 | Not chosen. Without it those routes return `503` |

## 4. Outbound network access (egress)

| Destination | Made by | Used for | Status |
|---|---|---|---|
| `KFX_SIGNAL_ENGINE_ORIGIN` (your HTTPS host) | Worker | Signal routes, with `X-KLEVS-ENGINE-TOKEN` | Optional. Not chosen |
| `KLEVS_RESET_EMAIL_URL` (your HTTPS endpoint) | Worker | Reset e-mail, with a bearer token | Optional. Not chosen |
| `api.frankfurter.dev` | FastAPI engine | Daily reference rates (live data) | Runtime data source. Pending your decision. Set `KFX_LIVE_DATA=0` to turn it off |
| Yahoo Finance, through `yfinance` | FastAPI engine | Hourly bars (live data) | Runtime data source. Pending your decision |
| SMTP server (`KLEVS_SMTP_HOST`) | FastAPI engine | Reset e-mail, if the FastAPI app is used for it | Optional. Not chosen |

## 4a. Build and deploy pipeline

| Item | Where | Used for | Status |
|---|---|---|---|
| GitHub Actions workflow `.github/workflows/cloudflare-deploy.yml` | GitHub | Runs the tests and the deploy, manual start only | Built. Not run live yet |
| GitHub environment `production` | GitHub | Holds the build secrets; can require a reviewer | Set up by you (deployment guide, section 4a) |
| Build secrets: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `KFX_ADMIN_USERNAME`, `KFX_ADMIN_PASSWORD`, and optional admin, engine and reset tokens | GitHub environment secrets | Passed to `wrangler secret put` on stdin, never into the build output | Set up by you |
| Variables: `CLOUDFLARE_PAGES_PROJECT` (required), `KLEVS_API_ORIGIN` (optional) | GitHub environment variables | Pages target, health check | Set up by you |
| Cloudflare API token | Cloudflare | Deploy permissions: Workers Scripts, D1, KV, Pages (edit), account read. One account | Created by you |
| Pages project | Cloudflare | Hosts `dist/`, proxies `/api` to the Worker | Created once with `wrangler pages project create` |

**Non-Cloudflare dependency in the deploy path:** GitHub Actions (runner), `actions/*` and the npm
and PyPI registries. The application itself runs only on Cloudflare. Cloudflare's own Git-connected
builds would remove the GitHub runner, but they are not set up here.

## 5. Application software

- Worker: Node.js 20 or later. Dependencies are in the root `package.json` (`wrangler`).
- Worker tests: `node --test worker/test/*.test.js` (12 tests).
- FastAPI engine: Python 3.11 with `requirements.txt`. `requirements-live.txt` adds `yfinance`.
- Python tests: `pytest`, and `ruff check fxbot tests`.

## 6. Data stores

| Store | Where | Notes |
|---|---|---|
| D1 database `klev` | Cloudflare D1 | Schema in `worker/migrations/0001_initial.sql`. Migrations via `npm run worker:migrate:remote` |
| KV namespace `voom` | Cloudflare KV | Session metadata and scan cache, both short-lived |
| SQLite `klevs.db` | FastAPI host disk | Only if the FastAPI app is used. Not replicated by this route |

## 7. Configuration and secrets

See the variable reference in the deployment guide, section 8. Secrets are set with
`wrangler secret put` and never committed. `KLEVS_DEV_RESET_CODES` must stay unset in production.

## 8. Decisions needed from you

- [ ] Sign-in on the Free plan. Your earlier answer was to lower PBKDF2 iterations. I still need the exact number, and any stored hashes will be rehashed on next sign-in. The code floor is 100,000, and that count is about 16 ms locally, still above the 10 ms Free limit. Alternatively, pay for Workers Paid (deployment guide, section 2).
- [ ] Signal engine. Your earlier answer was to port it to JavaScript. Not started: the engine is about 4,700 lines of Python, and the ML model (scikit-learn gradient boosting, pickled) would need exporting. Confirm the scope, or host the Python engine instead.
- [ ] Deploy runner: GitHub Actions (built), or Cloudflare's own Git-connected builds (not built).
- [ ] Reset e-mail: choose a provider and endpoint. Without one, operators reset passwords in the admin console.
- [ ] Live market data: keep Yahoo Finance and Frankfurter, or use the synthetic market only.
- [ ] Hostname and zone for the Pages site and the API proxy.
- [ ] Create the GitHub `production` environment and its build secrets. Then run the workflow once.
- [ ] Rotate the admin password that was once shown in chat.

## 9. Go-live gate

- [ ] `node --test worker/test/*.test.js` (12) and the Python suite pass. The deploy workflow's test job passes.
- [ ] The remote D1 migration applies to `klev`.
- [ ] A staging deploy: sign-up and sign-in succeed (no CPU-limit error in the logs).
- [ ] Admin sign-in opens `/admin`.
- [ ] Market routes return data (with the signal engine) or `503` (without it).
- [ ] Reset e-mail arrives (if reset e-mail is enabled).
