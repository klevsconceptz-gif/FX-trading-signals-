# Deploying KLEVS on Cloudflare (Containers route)

This is the Cloudflare-only deployment path. Every service it uses is a Cloudflare product:

| Piece | Cloudflare service | Where it lives |
|---|---|---|
| Public entry point, TLS, WAF, rate limits | Worker custom domain, zone WAF | `edge/wrangler.jsonc` |
| The FastAPI app (uvicorn, one worker) | Containers, bound to a Durable Object | `Dockerfile`, `edge/src/index.ts` |
| SQLite database, restored and replicated | R2 bucket `klevs-backups` | `fxbot/replica.py`, `edge/src/internal.ts` |
| Reset-code e-mail | Email Service, via a `send_email` binding | `fxbot/mail.py`, `edge/src/internal.ts` |
| Operator console protection (optional) | Cloudflare Access | section 7 |

There is no SMTP server, no VM, no systemd unit, and no Tunnel on this path.

> **Scope and verification.** The sections below were checked as follows.
>
> - **Checked locally:** the Python suite (including the replica, mail and deploy-file tests); the Worker type-check and its 20 unit tests; a `wrangler deploy --dry-run --containers-rollout=none`, which validated the bindings and resolved the container image path.
> - **Not checked here:** the Docker image build (Docker is not installed in the build sandbox, so the dry-run skips the image build), a live deploy, the Container starting in Cloudflare, the container reaching its own public origin, a real R2 bucket, a real Email Service send, and the Cloudflare dashboard screens. Run section 9 in a staging zone before you rely on the setup.
> - **The Worker still needs the other non-Cloudflare runtime data sources** for live prices: Yahoo Finance (via `yfinance`) and Frankfurter (`api.frankfurter.dev`). They are not part of the deploy path, but they are outbound dependencies. See section 10.

---

## 1. How it works

```
Browser ──HTTPS──▶ Worker klevs-edge (custom domain)
                     │  /__internal/*  → token-checked R2 snapshot and e-mail routes
                     │  everything else → KlevsContainer (one instance, sleeps after 15 min idle)
                     ▼
              Container: uvicorn fxbot.server:app (port 8000)
                     │  on start: restore klevs.db from R2 if the local file is missing
                     │  after each write: copy klevs.db to R2 (synchronously)
                     │  reset codes: POST /__internal/email on the same origin
                     ▼
              R2 bucket klevs-backups, key klevs/klevs.db
```

- **One container instance** (`max_instances: 1`). SQLite has one writer, and the market engine keeps its cache in memory, so a second instance would hold a second copy.
- **Idle shutdown.** The container stops after 15 minutes without requests (`sleepAfter = "15m"`). The next request starts it, restores the database from R2, and retrains the models. The cold start includes the restore plus a model retrain. An earlier estimate put the retrain at about 34 seconds. That is not yet measured on Cloudflare, so re-measure it.
- **Internal routes** are the only route the container uses to reach Cloudflare services. They require the `X-Internal-Token` header (constant-time comparison) and return `401` otherwise. An unset token refuses every request.

## 2. Prerequisites

- A Cloudflare account on the **Workers Paid** plan. Containers are not available on Free.
- A zone (domain) on Cloudflare that you will serve the app from.
- Node.js 20 or later, and npm.
- Wrangler, installed with the `edge/` package (`npm ci` in `edge/`).
- Email Service enabled, with a sender domain onboarded. Until the domain is onboarded, the Worker cannot send reset codes.
- **Docker (or a Docker-compatible CLI) on the machine that runs `wrangler deploy`.** Wrangler builds the container image locally before it uploads it, even for `--dry-run`. Set `WRANGLER_DOCKER_BIN` if the CLI is not on `PATH`.

## 3. Cost (verified against the Cloudflare pricing page, 5 Oct 2026)

Source: `developers.cloudflare.com/containers/platform/pricing/`.

- **Workers Paid:** $5 per month. It includes 25 GiB-hours of memory, 375 vCPU-minutes, and 200 GB-hours of disk each month.
- **Overage rates:** memory $0.0000025 per GiB-second; CPU $0.000020 per vCPU-second, billed only while the container is active; disk $0.00000007 per GB-second.
- **Instance `standard-2`** (1 vCPU, 6 GiB, 12 GB disk), which is the provisional size in `wrangler.jsonc`.

If the container runs around the clock, the total is about **$97 per month**: $5 for Workers Paid, plus about $92 for the container (memory $38.66, CPU $51.39, disk $2.13). Idle time reduces this, because memory and disk billing stops when the container sleeps. The sleep setting trades cost against the cold start in section 1.

**Not verified:** Email Service and R2 pricing. Check the Email Service and R2 pricing pages before you budget for them. Email is billed per message beyond an included allowance, and R2 is billed per stored GB and per operation. At this scale both are expected to be small, but that is an estimate, not a checked figure.

## 4. Set up the resources

Run these from the `edge/` directory unless noted. Each step is a one-off.

1. **Install the Worker dependencies.**
   ```sh
   cd edge
   npm ci
   ```
2. **Log in to Cloudflare** (opens a browser flow on your machine).
   ```sh
   npx wrangler login
   ```
3. **Create the R2 bucket** that holds the database snapshot.
   ```sh
   npx wrangler r2 bucket create klevs-backups
   ```
4. **Onboard the sender domain** in the dashboard: *Email Service → Domains*. Then set the sender address in `wrangler.jsonc` under `vars.KLEVS_MAIL_FROM`, for example `noreply@yourdomain.example`.
5. **Set your hostname.** In `wrangler.jsonc`, replace the placeholder `klevs.example.com` in both `routes[0].pattern` and `vars.KLEVS_PUBLIC_ORIGIN` with your hostname. The origin must not end with a slash.
6. **Set the secrets.** Each command prompts for the value. Do not put values in files or in your shell history.
   ```sh
   npx wrangler secret put INTERNAL_TOKEN        # a long random string, e.g. 48 bytes from a password manager
   npx wrangler secret put KFX_ADMIN_USERNAME    # the operator username
   npx wrangler secret put KFX_ADMIN_PASSWORD    # the operator password (policy: see docs/ADMIN_LOGIN.md)
   npx wrangler secret put KFX_ADMIN_EMAIL       # optional: needed if the username is not an e-mail address
   ```
   `KFX_ADMIN_PASSWORD` is applied on every container start and overwrites the stored password. To rotate it, change the secret and redeploy or restart the container.
7. **Keep `KLEVS_DEV_RESET_CODES` unset.** It returns reset codes in API responses, which is for local development only. The Worker never sets it.

## 5. Deploy

```sh
cd edge
npm test                      # Worker unit tests (20)
npm run deploy:dry-run        # validates bindings; add --containers-rollout=none if Docker is not installed
npm run deploy                # builds the image and deploys the Worker and the container
```

Wrangler builds the container from `../Dockerfile`. The build context is the repository root, and `.dockerignore` keeps secrets, databases and the test suite out of the image.

## 6. Environment reference

Every variable the app reads, and where it comes from on this route.

| Variable | Set by | Required | Purpose |
|---|---|---|---|
| `KFX_ADMIN_USERNAME` | secret | yes, for an operator account | Operator login name |
| `KFX_ADMIN_PASSWORD` | secret | yes, with the username | Operator password. Authoritative on every start |
| `KFX_ADMIN_EMAIL` | secret | optional | Operator e-mail, needed when the username is not an address |
| `KFX_ADMIN_PHONE` | secret | optional | Operator phone number |
| `KFX_LIVE_DATA` | Worker (default `1`) | no | `1` uses live prices, `0` uses the synthetic market only |
| `KLEVS_INTERNAL_TOKEN` | Worker, from the `INTERNAL_TOKEN` secret | set automatically | Authenticates the container to `/__internal/*` |
| `KLEVS_REPLICA_URL` | Worker, from `KLEVS_PUBLIC_ORIGIN` | set automatically | Snapshot route on the public origin |
| `KLEVS_EMAIL_URL` | Worker, from `KLEVS_PUBLIC_ORIGIN` | set automatically | E-mail route on the public origin |
| `KLEVS_DATABASE_URL` | nothing | leave unset | Defaults to SQLite at `/app/data/klevs.db`. PostgreSQL is not used on this route |
| `KLEVS_DEV_RESET_CODES` | nothing | must stay unset | Development only. Would expose reset codes |
| `KFX_SIGNAL_ENGINE_TOKEN` | nothing | not used here | Only for the D1/KV Worker route in `worker/README.md` |

Empty values are dropped, so the app sees them as unset.

## 7. Protect the edge

- **Login rate limit.** On the zone, add a WAF rate-limiting rule for the path `/api/auth/*`, per IP. The Free plan allows one path-based rule with a 10-second window. Paid plans allow more rules and longer windows. Check the WAF rate-limiting page for your plan, since the limits change.
- **Operator console.** Put Cloudflare Access in front of `/admin` and `/api/admin/*` on your hostname. Access adds a second sign-in before the app's own login. The Free plan of Zero Trust covers up to 50 users.
- **The 429 response** from the rate limit can reveal whether an account exists. The rate-limit rule does not fix that. Treat it as an open gap.
- **Public health endpoint.** `/api/health` returns the risk and signal configuration (equity, risk per trade, weights). It is public. Restrict it at the edge if that configuration is sensitive.

## 8. Database: replication and recovery

- **Restore on start.** When the container starts and no local `klevs.db` exists, it downloads the snapshot from R2, checks it with `PRAGMA quick_check`, and renames it into place. A missing snapshot is normal on the first start. Any other failure stops startup, so the app never serves an empty database by accident.
- **Replicate on write.** After each commit that changes data (including bulk updates and deletes), the container uploads a consistent copy to R2 before the request returns.
- **Failure handling.** If the upload fails, the replica is marked `pending`, and a background thread retries every 15 seconds. `/api/health` reports `replica: disabled`, `ok` or `pending`.
- **Known limit.** A write that succeeds while R2 is unreachable is acknowledged to the user before it is replicated. If the container is evicted before the retry succeeds, that write is lost. Watch for `pending` in `/api/health` and alert on it if you cannot accept that loss window.
- **Restore test.** Once a week, in staging, delete the container's data (redeploy, or restart with the snapshot removed from R2), and confirm the accounts and payments come back.

## 9. Verify the deployment

Run these against your hostname, not `workers.dev` (which is disabled).

1. `curl -s https://<your-host>/api/health` returns `200`, with `"replica": "ok"` after the first write.
2. Log in as the operator through the sign-in dialog. `/admin` opens the console.
3. Register a test account, request a password reset, and confirm the e-mail arrives. This is the only check of the Email Service path.
4. Redeploy the container. The accounts and payments from before the redeploy are still there.
5. Confirm with the dashboard that the container runs one instance and that `max_instances` is `1`.
6. Confirm that `/__internal/snapshot` returns `401` without the token.

If step 3 fails, check the Worker logs for `e-mail relay refused` (sender domain not onboarded, or daily quota) and `e-mail relay could not be reached`.

## 10. Open items before go-live

- **Container reaching its public origin.** The container calls `https://<host>/__internal/*` over the public internet. This is the assumed design, and it is not yet checked on Cloudflare. If the container cannot reach that origin, switch the two URLs to a route that stays inside Cloudflare, and retest section 9.
- **Instance size.** `standard-2` (1 vCPU, 6 GiB) is provisional. Measure peak memory during a model retrain and a scan, then adjust `instance_type`.
- **Live data sources.** Yahoo Finance (`yfinance`) and Frankfurter (`api.frankfurter.dev`) are outbound dependencies of the signal engine. They are not part of the deploy path, and they are pending your decision. Set `KFX_LIVE_DATA=0` to run on the synthetic market only.
- **Admin credentials.** An operator password was once committed to the repository and displayed in chat. Rotate it before go-live. Set the new value with `wrangler secret put KFX_ADMIN_PASSWORD`.
- **Public health detail.** See section 7.

## 11. Operations

- **Logs.** Worker and container logs are in the Cloudflare dashboard (observability is enabled in `wrangler.jsonc`). The app logs to stdout. The `klevs.replica`, `klevs.mail` and `klevs.auth` loggers report replication, relay and reset-code events without recipients or message bodies.
- **Rollback.** `npx wrangler rollback` returns the Worker to the previous version. Check that the container image rolls back with it.
- **Changing secrets** does not require a rebuild. `wrangler secret put` followed by a redeploy or a container restart is enough.
- **Stopping the app.** `npx wrangler delete` removes the Worker. The R2 bucket and its snapshot remain until you delete them.

## 12. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `wrangler deploy` says the Docker CLI is needed | Install Docker, start the daemon, or set `WRANGLER_DOCKER_BIN`. To deploy only the Worker for now, use `--containers-rollout=none` |
| `/api/health` shows `replica: disabled` | `KLEVS_REPLICA_URL` or `KLEVS_INTERNAL_TOKEN` is missing. Check `KLEVS_PUBLIC_ORIGIN` and the `INTERNAL_TOKEN` secret |
| `/api/health` shows `replica: pending` | The R2 upload is failing. Check that the bucket exists and the Worker has the `BACKUPS` binding |
| Container fails to start with a restore error | The snapshot in R2 failed the integrity check. Restore a known-good snapshot to `klevs/klevs.db`, or delete it to start empty (this loses data) |
| Reset codes never arrive | The sender domain is not onboarded, or the daily quota is used up. Check Email Service and the Worker logs |
| Login returns 401 for the operator | `KFX_ADMIN_PASSWORD` is wrong, or the container has not restarted since the secret changed |
| First request after idle is slow | Expected: cold start, database restore and model retrain |

## Appendix: files on this route

- `Dockerfile`, `.dockerignore`: the app image. Non-root, one uvicorn worker, port 8000.
- `edge/wrangler.jsonc`: bindings, container, vars. Placeholder hostname until you set yours.
- `edge/src/index.ts`: the Worker and the `KlevsContainer` class.
- `edge/src/container-env.ts`: the environment passed to the container.
- `edge/src/internal.ts`: the token-checked snapshot and e-mail routes.
- `edge/test/`: Worker unit tests (`npm test` in `edge/`).
- `fxbot/replica.py`, `fxbot/mail.py`: the app side of the snapshot and e-mail routes.
- `tests/test_replica.py`, `tests/test_mail.py`, `tests/fake_worker.py`: Python tests against an in-process fake of the Worker.
- `tests/test_deploy_files.py`: drift checks for the files above.
- `worker/`, `functions/`, `worker/README.md`: the separate D1/KV Worker route, documented in its own README. It needs the external signal engine described there, which is a non-Cloudflare dependency.
