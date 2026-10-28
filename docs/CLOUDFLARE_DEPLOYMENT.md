# Deploying KLEVS on the existing Cloudflare route

This guide covers the deployment in this repository: the Worker in `worker/` (D1 for accounts,
payments, access and signal snapshots; KV for short-lived session data), the Pages function in
`functions/`, and a scheduled publisher that generates the signals. The application runs on
Cloudflare's Free plan, with three exceptions listed in section 1. No R2 bucket, no Containers, no
Email Service, no VM, and no Tunnel.

The step-by-step Worker setup (migrations, secrets, deploy) is in
[`worker/README.md`](../worker/README.md). This guide adds the limits, the open items, and the
variable list.

> **Verification status.** Checked locally: the Python suite (including the publisher's refusal and
> SQL rules); the Worker unit tests (`node --test worker/test/*.test.js`, 20 passing, including the
> snapshot freshness and commit rules); the deploy and publish workflows' structure and their secret
> handling; a PBKDF2 timing check (section 2); and a live-data refusal in the build sandbox, which has
> no access to Yahoo Finance. **Not checked here:** a live deploy, the remote D1 migrations, a live
> publish from GitHub, the Pages build, and the Cloudflare dashboard screens.

---

## 1. What runs where

| Function | Runs on | Status on the Free plan |
|---|---|---|
| Static site and `/api` proxy | Pages (`functions/api/[[path]].js`) | Works |
| Sign-up, sign-in, sessions, password change | Worker + D1 | **At risk**: CPU limit (section 2) |
| Payments, access grants, admin console, audit log, admin password reset | Worker + D1 | **At risk**, for the same reason |
| Settings, universe list | Worker + D1 | Works |
| Signal generation (scan, instructions, pair detail) | Scheduled publisher on GitHub Actions (hourly) writes a snapshot to D1; the Worker serves it | Works after the first publish. `503` before it, and `503` once the snapshot is older than the age limit (section 5) |
| Backtests (`/api/backtest`) | Not served by this deployment | `503`. Run `scripts/backtest.py` locally |
| Password reset e-mail | Operator-supplied HTTPS endpoint (`KLEVS_RESET_EMAIL_URL`) | No e-mail until configured. Operators can reset a password in the admin console instead |

Three items are outside the Cloudflare Free plan: the **sign-in CPU limit** (section 2), the
**GitHub Actions runner** that generates the signals (section 4a), and the **live market data
sources** (section 5). Each is a decision for you (section 7).

## 2. The Free-plan CPU limit (open blocker)

Cloudflare's limits page (updated 12 Oct 2026) lists **10 ms of CPU time per HTTP request** on the
Free plan, and 5 minutes on Workers Paid.

Password hashing is the problem. The Worker stores passwords with PBKDF2-SHA256 at 200,000
iterations (`worker/src/security.js`). In a local Node WebCrypto check (median of repeated runs), one
derivation took about **31 ms** at 200,000 iterations, about three times the Free limit. Login,
registration, password changes and admin password resets would therefore likely fail with a CPU-limit
error (Cloudflare error 1102) on the Free plan. The code's minimum iteration count is 100,000, which
still takes roughly 16 ms in the same check, so lowering it to the floor is not enough.

**Not measured on Cloudflare.** Confirm it with the Worker's invocation logs after a staging deploy.
Signal reads are small and are not affected. Options:

1. **Workers Paid**, which raises the limit to 5 minutes of CPU per request (default 30 seconds).
   The plan costs $5 per month. This is a charged dependency.
2. **Keep the Free plan and lower the iteration count to 100,000.** Chosen by the owner on
   2026-10-24. New hashes use 100,000 iterations (`worker/src/security.js`). Existing hashes at
   200,000 still verify, since the stored count is read at login, so no forced migration is needed.
   This weakens password hashing somewhat. At 100,000 one derivation is still about 16 ms locally,
   above the 10 ms limit, so sign-in may still fail on Free. Staging must show whether it does.
3. **Keep the Free plan and accept that sign-in does not work** on the Worker route.

## 3. Prerequisites

- A Cloudflare account and a zone (domain) for the app.
- A GitHub repository with Actions enabled, and a `production` environment (section 4a).
- Node.js 20 or later, with npm, for the Worker and Wrangler.
- Python 3.11 for the publisher (`requirements.txt` and `requirements-live.txt`).

## 4. Deploy the Cloudflare side

### 4a. Automated deploy and signals, with build secrets (recommended)

Two workflows:

- `.github/workflows/cloudflare-deploy.yml` (manual start). It runs the tests, applies the remote D1
  migrations (`0001`, `0002`), deploys the Worker, sets the Worker secrets, deploys the Pages site,
  and checks `/api/health`.
- `.github/workflows/signals.yml` (hourly, and manual start). It runs the publisher (section 5).

**One-time setup**

1. Cloudflare resources: nothing to create by hand. The deploy workflow runs
   `scripts/cloudflare_bootstrap.mjs`, which finds or creates the D1 database `klev`, the KV namespace
   `voom`, and the Pages project `CLOUDFLARE_PAGES_PROJECT` through the Cloudflare REST API. It creates
   only what is missing. If an ID in `worker/wrangler.jsonc` does not match the account, the job stops
   before any migration or deploy. To check first, without creating anything, run locally:
   `CLOUDFLARE_API_TOKEN=… CLOUDFLARE_ACCOUNT_ID=… npm run cloudflare:bootstrap -- --check`.
   If the IDs need updating, run it without `--check` and `--write-config`, then commit the file.
2. In GitHub, create the environment `production`. Add these **environment secrets**:

| Secret | Required | Purpose |
|---|---|---|
| `CLOUDFLARE_API_TOKEN` | Yes | API token for the deploy and the publisher (permissions below) |
| `CLOUDFLARE_ACCOUNT_ID` | Yes | The Cloudflare account that owns the Worker, D1, KV and Pages |
| `KFX_ADMIN_USERNAME` | Set both, or neither | Admin login name. If both admin values are empty, the deploy step skips them. Setting only one fails the job |
| `KFX_ADMIN_PASSWORD` | Set both, or neither | Admin password. Must meet the password policy |
| `KFX_ADMIN_EMAIL` | No | Admin e-mail, if the username is not an address |
| `KFX_ADMIN_PHONE` | No | Admin phone number |
| `KLEVS_RESET_EMAIL_TOKEN` | No | Token for the reset-e-mail endpoint (section 6) |

3. Add these **environment variables** (not secret):

| Variable | Required | Purpose |
|---|---|---|
| `CLOUDFLARE_PAGES_PROJECT` | Yes | `klv-fx-trading-signals` (the Pages project from step 1) |
| `KLEVS_API_ORIGIN` | No | API origin used by the health check. Defaults to the workers.dev URL |

4. Give the API token only what the deploy and the publisher need: Workers Scripts (edit), D1
   (edit), Workers KV storage (edit), Cloudflare Pages (edit), and account settings (read). Scope it
   to the one account.
5. Run **Deploy to Cloudflare** once. It applies migration `0002`, which the publisher needs. Then run
   **Publish signals** (section 5).

**How the secrets are handled.** The admin password is a build secret. It is not compiled into
`dist/` or the Worker bundle. The deploy step passes it to `wrangler secret put` on stdin, and the
log shows only the secret name. Setting only one of the admin pair fails the job. Rotate a secret by
changing its GitHub secret and running the workflow again. The publisher never prints a secret.

**Dependency note.** GitHub Actions runs both workflows, so it is a non-Cloudflare dependency of the
build and signal path. The application itself runs only on Cloudflare. Cloudflare's own
Git-connected builds would remove the GitHub runner for the deploy, but they are not set up here.
Nothing in the signal path can run on Cloudflare's Free compute, so the scheduled job is the chosen
design (section 5).

### 4b. Deploy by hand

Follow [`worker/README.md`](../worker/README.md), section "Production setup and deployment". In
short:

1. Apply the D1 migrations to the remote database: `npm run worker:migrate:remote`.
2. Store the admin bootstrap values as Worker secrets (`KFX_ADMIN_USERNAME`,
   `KFX_ADMIN_PASSWORD`, and `KFX_ADMIN_EMAIL` if the username is not an address). These are
   secrets, not `vars`.
3. Deploy the Worker: `npm run worker:deploy`.
4. Deploy the Pages site. The function proxies `/api/*` to the Worker, and `KLEVS_API_ORIGIN`
   overrides the default origin if you need one.
5. Publish signals (section 5), with `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` set in your
   shell: `python -m scripts.publish_signals`.

The Worker is named `fx-trading-signals` in `worker/wrangler.jsonc`. Check the target account
before any remote migration or deploy.

## 5. Signals: the scheduled publisher

The Python engine takes about 27 seconds per full-market scan (measured in the build sandbox on the
synthetic market). A Worker request cannot run that within its CPU limit. So the signals are
generated by a job that runs outside the Worker and writes the result to D1:

1. `.github/workflows/signals.yml` runs `scripts/publish_signals.py` every hour, at minute 7.
2. The publisher scans the market on **live data** (Yahoo Finance through `yfinance`, and the
   Frankfurter API). The data source is `KFX_LIVE_DATA=1`, set in the workflow.
3. **The publisher refuses to publish anything that is not live.** If live data is unavailable, the
   run fails and the last live snapshot stays in place. The synthetic fallback never reaches D1.
4. It writes three kinds of rows to the D1 table `signal_cache` (migration `0002`): one per
   instrument (`pair:<SYMBOL>`), the instructions, and the full scan. All rows of one run share a run
   ID. The `scan` row is written last and marks the run as committed.
5. The Worker serves paid users from those rows. It returns `503` when no snapshot has been
   published, when the snapshot is older than `KFX_SIGNAL_MAX_AGE_SECONDS` (default 7200 seconds, two
   hours), or when a pair or the instructions belong to a run other than the committed one. It never
   shows old advice as current.

**Limits to know before you rely on it**

- **Freshness** is the schedule: up to about an hour old, plus any runner delay.
- **GitHub runs scheduled workflows only from the default branch.** The hourly schedule takes effect
  once this change is merged to `main`. Until then, start **Publish signals** manually.
- **Backtests are not served.** `/api/backtest` returns `503` on this deployment.
- **The `refresh` parameter no longer forces a rescan.** The schedule runs the scan.
- **Live data terms.** `yfinance` is a community client for Yahoo Finance data, not an official
  Yahoo API. Frankfurter is a public reference-rate API. Check that their terms allow what you
  publish, and keep the risk disclaimers on every page that shows advice (the app footer states
  "generates signals and a risk matrix only · does not place trades · not financial advice").
- **Schema first.** Migration `0002` must be applied before the first publish. The deploy workflow
  applies it; by hand, run `npm run worker:migrate:remote`.

**Check a publish locally without touching D1:** `python -m scripts.publish_signals --dry-run --out
signals.sql` builds the SQL and does not apply it. On a machine with no access to Yahoo Finance, the
run exits with `refusing to publish: ... not live`. That is the expected safe result.

## 6. Reset e-mail (optional), and the admin reset

Set `KLEVS_RESET_EMAIL_URL` (an HTTPS endpoint with no credentials in the URL) and
`KLEVS_RESET_EMAIL_TOKEN` on the Worker. The Worker calls the endpoint with `POST {to, subject, text}`
and the header `Authorization: Bearer <token>`. The e-mail carries the 7-digit reset code. If the
endpoint is unset or fails, the reset request still returns the same neutral message, the code is
never delivered, and the reset cannot finish. The Worker logs a warning. The endpoint and its
provider are yours to choose, and so is the cost.

**Without an e-mail endpoint**, an operator resets the password in the admin console (**Reset a
password**). Enter the username or e-mail, the new password you choose, and a reason. The reason goes
to the audit trail, and the password is never logged. The reset ends every session of that account.
Give the new password to the user through a channel you trust. Sessions of that account end at once. The reset is not available for the
bootstrap admin (`KFX_ADMIN_PASSWORD`), which you change as a Worker secret. Hashing the password runs
on the Worker, so it counts toward the CPU limit in section 2.

## 7. Decisions before go-live

- **Sign-in on the Free plan.** Decided: the iteration count is lowered to 100,000 (section 2).
  Existing hashes stored at 200,000 still verify, because the stored count is read at login. The
  open question is whether one derivation at 100,000 (about 16 ms locally) fits the Free limit on
  Cloudflare. Confirm in staging.
- **Live data terms.** Confirm that publishing Yahoo Finance data (through `yfinance`) and Frankfurter
  rates is acceptable for your product (section 5).
- **Reset e-mail provider.** Choose one, or rely on the admin console reset.

## 8. Variable reference

Worker (set with `wrangler secret put` or in `vars`):

| Name | Kind | Purpose |
|---|---|---|
| `KFX_ADMIN_USERNAME` | secret | Admin account, provisioned on each start |
| `KFX_ADMIN_PASSWORD` | secret | Admin password, applied on each start |
| `KFX_ADMIN_EMAIL` | secret | Admin e-mail, if the username is not an address |
| `KFX_ADMIN_PHONE` | secret | Optional admin phone number |
| `KFX_SIGNAL_MAX_AGE_SECONDS` | var | Oldest signal snapshot the Worker serves (default 7200, maximum 86400) |
| `KLEVS_RESET_EMAIL_URL` | var | HTTPS endpoint that sends reset e-mail |
| `KLEVS_RESET_EMAIL_TOKEN` | secret | Token for the reset-e-mail endpoint |
| `KLEVS_API_ORIGIN` | Pages variable | Overrides the Worker origin the Pages function proxies to |

Publisher (GitHub `production` environment; `KFX_LIVE_DATA` is set in the workflow):

| Name | Kind | Purpose |
|---|---|---|
| `CLOUDFLARE_API_TOKEN` | secret | Lets Wrangler write the signal snapshot to D1 |
| `CLOUDFLARE_ACCOUNT_ID` | secret | The account that owns the D1 database |

FastAPI app (set on the host that runs `fxbot.server`, for local or self-hosted use):

| Name | Purpose |
|---|---|
| `KFX_LIVE_DATA` | `1` (default) for live market data, `0` for the synthetic market |
| `KFX_ADMIN_USERNAME`, `KFX_ADMIN_PASSWORD`, `KFX_ADMIN_EMAIL`, `KFX_ADMIN_PHONE` | Admin bootstrap for the FastAPI app |
| `KFX_SIGNAL_ENGINE_TOKEN` | Used by the FastAPI internal engine route only. The Cloudflare deployment does not use it |
| `KLEVS_DATABASE_URL` | Database URL for the FastAPI app. Unset uses SQLite at `data/klevs.db` |
| `KLEVS_DEV_RESET_CODES` | Development only. Must stay unset in production (it returns reset codes) |
| `KLEVS_SMTP_HOST`, `KLEVS_SMTP_PORT`, `KLEVS_SMTP_USER`, `KLEVS_SMTP_PASSWORD`, `KLEVS_SMTP_FROM` | Reset e-mail for the FastAPI app, over STARTTLS. An external mail provider |

## 9. Verify

1. `curl -s https://<your-host>/api/health` returns `200`. Its `signals.state` is `missing` before the
   first publish and `fresh` after it.
2. Sign in as the admin. The console at `/admin` opens and lists payments.
3. Sign up with a test account. If it fails with a CPU-limit error, see section 2.
4. Give a test account paid access. `/api/scan` returns `503` before the first publish and `200` after.
5. Request a reset code. Expect an e-mail only if `KLEVS_RESET_EMAIL_URL` is set.

## 10. Troubleshooting

| Symptom | Likely cause |
|---|---|
| Sign-in or sign-up fails with error 1102 / "exceeded resource limits" | CPU limit on the Free plan (section 2) |
| Scan, instructions or pair return `503` "no signal snapshot has been published" | The publisher has not run, or migration `0002` is not applied (section 5) |
| `503` "signal snapshot is too old" | The scheduled publish has failed for longer than `KFX_SIGNAL_MAX_AGE_SECONDS`. Check the **Publish signals** run log |
| `503` "signal snapshot is being updated" | A publish is in progress. Retry in a moment |
| Publish fails with "refusing to publish ... not live" | Live data was unavailable from the runner. The last live snapshot stays in place |
| `/api/backtest` returns `503` | Backtests are not served on this deployment (section 5) |
| Reset code never arrives | `KLEVS_RESET_EMAIL_URL` or `KLEVS_RESET_EMAIL_TOKEN` is not set (section 6) |
| Admin login fails | `KFX_ADMIN_USERNAME` or `KFX_ADMIN_PASSWORD` is wrong. Both must be set together |
| `/api/*` returns a Pages error | `KLEVS_API_ORIGIN` points at a Worker that is not deployed |
