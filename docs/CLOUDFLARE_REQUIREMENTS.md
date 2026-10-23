# Requirements: deploying KLEVS on Cloudflare

This is the checklist for the Cloudflare-only route described in `docs/CLOUDFLARE_DEPLOYMENT.md`. Use it before you start and again before go-live.

## 1. Cloudflare account and plans

| Need | Plan | Why | Price (checked 5 Oct 2026, Cloudflare pricing and Containers pricing pages) |
|---|---|---|---|
| Workers and Containers | **Workers Paid** | Containers are not on the Free plan | $5 per month, with included allowances |
| Containers usage | Workers Paid (overage) | `standard-2` runs at 1 vCPU and 6 GiB | About $92 per month if it runs around the clock (see the deployment guide, section 3) |
| Email Service | Included with the account, check current limits | Reset-code e-mail through the `send_email` binding | **Not verified:** check the Email Service pricing and limits page |
| R2 bucket for the database snapshot | Included with the account, check current limits | Holds `klevs/klevs.db` | **Not verified:** check the R2 pricing page |
| Zone WAF rate limiting | Free allows one path-based rule. Paid plans allow more rules and longer windows | Limits login attempts on `/api/auth/*` | Check the WAF rate-limiting page for your plan |
| Cloudflare Access (optional) | Zero Trust Free covers up to 50 users | A second sign-in in front of `/admin` | Check the Zero Trust plans page |

Docker, or a Docker-compatible CLI, must be installed on the machine that runs `wrangler deploy`. Wrangler builds the container image locally.

## 2. Domain and DNS

- A zone on Cloudflare for the hostname you will serve the app from.
- The hostname becomes a Worker custom domain (`routes` in `edge/wrangler.jsonc`). `workers_dev` is off, so the app is not on `*.workers.dev`.
- A sender domain onboarded in Email Service, with SPF and DKIM records that Cloudflare creates. The sender address is `KLEVS_MAIL_FROM`.

## 3. Compute: the container

- **Image.** Built from the repository `Dockerfile` (Python 3.11 slim, non-root, one uvicorn worker, port 8000). Wrangler builds it; no registry account is needed.
- **Instance size.** `standard-2` (1 vCPU, 6 GiB memory, 12 GB disk) is provisional. Measure peak memory during a model retrain and a scan before you settle on it.
- **Concurrency.** `max_instances` is 1. SQLite has one writer, and the market cache lives in memory.
- **Idle behaviour.** The container sleeps after 15 minutes without requests. A cold start restores the database and retrains the models, so the first request after idle is slow.

## 4. Outbound network access (egress)

The container makes these outbound calls.

| Destination | Protocol | Used for | Status |
|---|---|---|---|
| Your Worker's public origin (`KLEVS_PUBLIC_ORIGIN`), `/__internal/snapshot` and `/__internal/email` | HTTPS, 443 | Snapshot restore and replication; reset-code e-mail, via the token | Assumed to work from a container. **Not yet checked on Cloudflare.** See the deployment guide, section 10 |
| `api.frankfurter.dev` | HTTPS, 443 | Daily reference rates (live data, optional) | Runtime data source, pending your decision |
| Yahoo Finance, through `yfinance` | HTTPS, 443 | Hourly bars (live data, optional) | Runtime data source, pending your decision. Set `KFX_LIVE_DATA=0` to turn it off |

There is no SMTP server, no database server, no Tunnel connector, and no other outbound host on this path.

## 5. Application software

- Built into the image: Python dependencies from `requirements.txt`, and `requirements-live.txt` (`yfinance`) unless the image is built with `INSTALL_LIVE=0`.
- Worker: Node.js 20 or later for building and testing `edge/`. The Worker depends on `@cloudflare/containers`, `wrangler`, and TypeScript, all pinned in `edge/package.json`.
- Local checks: `pytest` (Python suite), `npm test` in `edge/`, and `wrangler deploy --dry-run --containers-rollout=none`.

## 6. Data stores

| Store | Where | Notes |
|---|---|---|
| SQLite `klevs.db` | Container disk, `/app/data/klevs.db` | Restored from R2 on start, replicated after each write, owner-only permissions |
| R2 bucket `klevs-backups` | Cloudflare R2, key `klevs/klevs.db` | Created with `wrangler r2 bucket create`. Bound to the Worker as `BACKUPS` |
| Models | Container disk, `/app/models` | Regenerable. Rebuilt on start if missing |

Known limit: a write that succeeds while R2 is unreachable is not protected until the background retry succeeds. See the deployment guide, section 8.

## 7. Configuration and secrets

| Name | Where it is set | Required | Purpose |
|---|---|---|---|
| `INTERNAL_TOKEN` | `wrangler secret put` | yes | Shared secret for the internal routes |
| `KFX_ADMIN_USERNAME`, `KFX_ADMIN_PASSWORD` | `wrangler secret put` | yes, for an operator | Operator account, applied on every start |
| `KFX_ADMIN_EMAIL`, `KFX_ADMIN_PHONE` | `wrangler secret put` | optional | Operator contact details |
| `KLEVS_PUBLIC_ORIGIN`, `KLEVS_MAIL_FROM` | `vars` in `edge/wrangler.jsonc` | yes | Public origin and sender address. Not secret |
| `KFX_LIVE_DATA` | Worker default `1` | no | Set `0` for the synthetic market only |
| `KLEVS_DEV_RESET_CODES` | nothing | must not be set | Development only |

No secret values are stored in the repository. `.dev.vars` is git-ignored and is for local development only.

## 8. Decisions and inputs needed from you

- [ ] Hostname to serve the app from (replace `klevs.example.com` in `edge/wrangler.jsonc`).
- [ ] Sender domain and address for e-mail, onboarded in Email Service.
- [ ] Whether to keep live market data (Yahoo Finance, Frankfurter) or run on the synthetic market only.
- [ ] Whether to put Cloudflare Access in front of the operator console.
- [ ] Acceptable loss window if R2 is unreachable during a write (section 6).
- [ ] Whether the D1/KV Worker route in `worker/` stays as an alternative. Its signal routes need an external engine, which is a non-Cloudflare dependency.

## 9. Go-live gate

- [ ] `npm test` passes in `edge/`, and the Python suite passes.
- [ ] `wrangler deploy --dry-run` succeeds with Docker available, so the image builds.
- [ ] A staging deploy starts the container, and `/api/health` reports `replica: ok` after a write.
- [ ] The container reaches its public origin for `/__internal/snapshot` and `/__internal/email`.
- [ ] A reset-code e-mail arrives in a real inbox.
- [ ] Data survives a container restart (the restore test in the deployment guide).
- [ ] The operator password has been rotated since it was last shown anywhere.
- [ ] The login rate-limit rule is in place on `/api/auth/*`.
- [ ] `/api/health` exposure (risk configuration) has been reviewed.
