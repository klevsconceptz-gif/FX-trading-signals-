# Deploying KLEVS on the existing Cloudflare route

This guide covers the deployment that already exists in this repository: the Worker in `worker/`
(D1 for accounts, payments and access; KV for short-lived caches) fronted by the Pages function in
`functions/`. It uses only Cloudflare resources that the Free plan includes. No R2 bucket, no
Containers, no Email Service, no VM, and no Tunnel.

The step-by-step Worker setup (migrations, secrets, deploy) is in
[`worker/README.md`](../worker/README.md). This guide adds the limits, the three items that the free
route does not cover, and the full variable list.

> **Verification status.** Checked locally: the Python suite; the Worker unit tests (`node --test
> worker/test/*.test.js`, 8 passing); a PBKDF2 timing check (section 2). **Not checked here:** a live
> deploy, the remote D1 migration, the Pages build, and the Cloudflare dashboard screens.

---

## 1. What runs where

| Function | Runs on | Cloudflare plan needed | Status on the Free plan |
|---|---|---|---|
| Static site and `/api` proxy | Pages (`functions/api/[[path]].js`) | Free | Works |
| Sign-up, sign-in, sessions, password change | Worker + D1 | Free for the requests; **CPU limit is the risk** (section 2) | At risk |
| Payments, access grants, admin console, audit log | Worker + D1 | Same as sign-in | At risk, for the same reason |
| Settings, universe list | Worker + D1/KV | Free | Works |
| Market scan, instructions, pair detail, backtest | **External FastAPI signal engine** (`KFX_SIGNAL_ENGINE_ORIGIN`) | Not on Cloudflare | Returns `503` until an engine is configured |
| Password reset e-mail | **Operator-supplied HTTPS endpoint** (`KLEVS_RESET_EMAIL_URL`) | Not on Cloudflare | Returns no e-mail until configured |

The signal engine is Python (numpy, pandas, scikit-learn). A Worker cannot run it within the Free
plan's CPU limit, so it needs its own host. The reset e-mail is sent by an endpoint you provide, so
the mail provider is your choice.

## 2. The Free-plan CPU limit (open blocker)

Cloudflare's limits page (updated 12 Oct 2026) lists **10 ms of CPU time per HTTP request** on the
Free plan, and 5 minutes on Workers Paid.

Password hashing is the problem. The Worker stores passwords with PBKDF2-SHA256 at 200,000
iterations (`worker/src/security.js`). In a local check (Node WebCrypto, median of five runs), one
derivation took about **38 ms**. That is nearly four times the Free limit. Login, registration and
password changes would therefore likely fail with a CPU-limit error (Cloudflare error 1102) on the
Free plan. The code's minimum iteration count is 100,000, which still takes roughly 19 ms in the
same check, so lowering it is not enough.

**Not measured on Cloudflare.** Confirm it with the Worker's invocation logs after a staging deploy.
If the limit is exceeded, the options are:

1. **Workers Paid**, which raises the limit to 5 minutes of CPU per request (default 30 seconds).
   The plan costs $5 per month. This is a charged dependency.
2. **Keep the Free plan and accept that sign-in does not work** on the Worker route.

Decide before you go live. See section 7.

## 3. Prerequisites

- A Cloudflare account and a zone (domain) for the app.
- Node.js 20 or later, with npm, for the Worker and Wrangler.
- Wrangler, from the root `package.json` (`npm ci` at the repository root).
- Optional, for the signal engine: a host you control that serves HTTPS (section 5).

## 4. Deploy the Cloudflare side

Follow [`worker/README.md`](../worker/README.md), section "Production setup and deployment". In
short:

1. Apply the D1 migration to the remote database: `npm run worker:migrate:remote`.
2. Store the admin bootstrap values as Worker secrets (`KFX_ADMIN_USERNAME`,
   `KFX_ADMIN_PASSWORD`, and `KFX_ADMIN_EMAIL` if the username is not an address). These are
   secrets, not `vars`.
3. Deploy the Worker: `npm run worker:deploy`.
4. Deploy the Pages site. The function proxies `/api/*` to the Worker, and
   `KLEVS_API_ORIGIN` overrides the default origin if you need one.

The Worker is named `klv-fx-trading-signals` in `worker/wrangler.jsonc`. Check the target account
before any remote migration or deploy.

## 5. Optional: the signal engine on an HTTPS host you control

Set the same high-entropy token on both sides:

- On the Worker, as a secret: `npx wrangler secret put KFX_SIGNAL_ENGINE_TOKEN --config worker/wrangler.jsonc`.
- On the FastAPI host, as an environment secret: `KFX_SIGNAL_ENGINE_TOKEN`.

Then set `KFX_SIGNAL_ENGINE_ORIGIN` on the Worker (an HTTPS origin with no path). It must not point
back at the Worker. The FastAPI host runs `uvicorn fxbot.server:app --host 0.0.0.0 --port 8000 --workers 1`.
Until both are set, the market routes return `503`, and every other route still works.

This host is outside Cloudflare, so it is a non-Cloudflare dependency. Its cost and location are
yours to choose.

## 6. Reset e-mail (optional)

Set `KLEVS_RESET_EMAIL_URL` (an HTTPS endpoint) and `KLEVS_RESET_EMAIL_TOKEN` on the Worker. The
endpoint must use HTTPS. The Worker calls it with `POST {to, subject, text}` and the header
`Authorization: Bearer <token>`. Without both values, the
Worker returns no e-mail and the reset cannot finish. The endpoint and its provider are yours to
choose, and so is the cost.

## 7. Decisions before go-live

- **Sign-in on the Free plan.** Pay for Workers Paid, or accept that sign-in fails (section 2).
- **Signal engine host.** Choose a host and a provider, or leave the market routes off.
- **Reset e-mail provider.** Choose one, or leave password reset off.
- **Live market data.** The FastAPI app fetches Yahoo Finance (`yfinance`) and Frankfurter
  (`api.frankfurter.dev`) when `KFX_LIVE_DATA` is on. These are external runtime data sources. Set
  `KFX_LIVE_DATA=0` to use the synthetic market only.
- **Admin credentials.** An operator password was once shown in chat. Rotate it before go-live with
  `wrangler secret put KFX_ADMIN_PASSWORD`.

## 8. Variable reference

Worker (set with `wrangler secret put` or in `vars`):

| Name | Kind | Purpose |
|---|---|---|
| `KFX_ADMIN_USERNAME` | secret | Admin account, provisioned on each start |
| `KFX_ADMIN_PASSWORD` | secret | Admin password, applied on each start |
| `KFX_ADMIN_EMAIL` | secret | Admin e-mail, if the username is not an address |
| `KFX_ADMIN_PHONE` | secret | Optional admin phone number |
| `KFX_SCAN_CACHE_SECONDS` | var | Scan cache time in KV (default 60) |
| `KFX_SIGNAL_ENGINE_ORIGIN` | var | HTTPS origin of the FastAPI signal engine |
| `KFX_SIGNAL_ENGINE_TOKEN` | secret | Shared with the FastAPI host |
| `KLEVS_RESET_EMAIL_URL` | var | HTTPS endpoint that sends reset e-mail |
| `KLEVS_RESET_EMAIL_TOKEN` | secret | Token for the reset-e-mail endpoint |
| `KLEVS_API_ORIGIN` | Pages variable | Overrides the Worker origin the Pages function proxies to |

FastAPI signal engine (set on the host that runs `fxbot.server`):

| Name | Purpose |
|---|---|
| `KFX_SIGNAL_ENGINE_TOKEN` | Must match the Worker secret. Accepted only on the signal routes |
| `KFX_LIVE_DATA` | `1` (default) for live market data, `0` for the synthetic market |
| `KFX_ADMIN_USERNAME`, `KFX_ADMIN_PASSWORD`, `KFX_ADMIN_EMAIL`, `KFX_ADMIN_PHONE` | Admin bootstrap for the FastAPI app |
| `KLEVS_DATABASE_URL` | Database URL for the FastAPI app. Unset uses SQLite at `data/klevs.db` |
| `KLEVS_DEV_RESET_CODES` | Development only. Must stay unset in production (it returns reset codes) |
| `KLEVS_SMTP_HOST`, `KLEVS_SMTP_PORT`, `KLEVS_SMTP_USER`, `KLEVS_SMTP_PASSWORD`, `KLEVS_SMTP_FROM` | Reset e-mail for the FastAPI app, over STARTTLS. An external mail provider |

## 9. Verify

1. `curl -s https://<your-host>/api/health` returns `200`.
2. Sign in as the admin. The console at `/admin` opens and lists payments.
3. Sign up with a test account. If it fails with a CPU-limit error, see section 2.
4. Call a market route. Before the signal engine is set, expect `503`. Afterwards, expect data.
5. Request a reset code. Expect an e-mail only if `KLEVS_RESET_EMAIL_URL` is set.

## 10. Troubleshooting

| Symptom | Likely cause |
|---|---|
| Sign-in or sign-up fails with error 1102 / "exceeded resource limits" | CPU limit on the Free plan (section 2) |
| Market routes return `503` | `KFX_SIGNAL_ENGINE_ORIGIN` or `KFX_SIGNAL_ENGINE_TOKEN` is not set (section 5) |
| Reset code never arrives | `KLEVS_RESET_EMAIL_URL` or `KLEVS_RESET_EMAIL_TOKEN` is not set (section 6) |
| Admin login fails | `KFX_ADMIN_USERNAME` or `KFX_ADMIN_PASSWORD` is wrong. Both must be set together |
| `/api/*` returns a Pages error | `KLEVS_API_ORIGIN` points at a Worker that is not deployed |
