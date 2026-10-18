# Requirements: deploying KLEVS behind Cloudflare

This is the checklist to complete before following
[`CLOUDFLARE_DEPLOYMENT.md`](CLOUDFLARE_DEPLOYMENT.md). Items marked **measure** are not
yet known for this app and must be checked on the real host. Items marked **not tested**
were not run in the build sandbox.

## 1. Cloudflare account and plans

| Requirement | Needed for | Plan and cost |
|---|---|---|
| Cloudflare account, with the domain added as a zone and its nameservers moved to Cloudflare | DNS, TLS, WAF, rate limits | Free to create |
| **Pro plan** on the zone (recommended) | Login rate limit with 2 rules and a 60 s counting window | $20/mo billed annually, $25/mo billed monthly. The Free plan allows 1 rule with a 10 s window only. |
| Zero Trust organisation (create a team name) | Tunnel and Access | Free for up to 50 users |
| Access identity provider: one-time PIN by email, or an SSO provider | Gate `/admin` and `/api/admin/*` | Included with Zero Trust |
| Cloudflare Tunnel with a remotely managed tunnel | Route the hostname to the origin | Included with Zero Trust |

Not required: Workers, Containers, R2, or any Cloudflare-hosted database.

## 2. Domain and DNS

- A domain you control, with its nameservers set to the two Cloudflare nameservers shown
  when you add the zone.
- One hostname for the app, for example `klevs.example.com`. The Tunnel creates a proxied
  DNS record for it. Do not create an A record that points to the origin.
- Plan for `http://` to redirect to `https://` (Always Use HTTPS, set in the guide).

## 3. Origin host

| Item | Requirement | Status |
|---|---|---|
| Operating system | Linux, x86_64. Ubuntu 22.04 or 24.04 LTS is the intended target. | Not tested |
| CPU and memory | 2 vCPU and 4 GB RAM recommended. The market scan and the per-pair model training are CPU-bound. | **Measure** the peak memory during a cold scan with `docker stats` or `systemd-cgtop`. |
| Disk | At least 20 GB. The image is large (pandas, scikit-learn). | Image size **not measured**. |
| Container runtime | Docker Engine, current release, **or** Python 3.11 for the systemd path | Docker not tested in the sandbox |
| Clock | Time synchronised with NTP. Login lockout and session expiry depend on time. | Standard |
| Inbound network | **None** from the internet. SSH only from listed admin addresses. | Required |
| Listener | The app binds to `127.0.0.1:8000` (systemd) or is published on `127.0.0.1:8000` (Docker). | Verify with `ss -ltnp` |

## 4. Outbound network access (egress allow-list)

The origin host must reach these destinations. If the host sits behind an egress filter,
allow them before you start.

| Destination | Port and protocol | Used by |
|---|---|---|
| `region1.v2.argotunnel.com`, `region2.v2.argotunnel.com` | **7844 TCP and UDP** | `cloudflared` tunnel connection (QUIC on UDP, HTTP/2 on TCP fallback) |
| `api.cloudflare.com` | 443 TCP | `cloudflared` connectivity checks (version 2026.5.2 and later) |
| `_v2-origintunneld._tcp.argotunnel.com`, `cftunnel.com`, `h2.cftunnel.com`, `quic.cftunnel.com` | 7844 | Only needed when an SNI-enforcing firewall sits in the path |
| `api.frankfurter.dev` | 443 HTTPS | Daily ECB reference rates. The app uses these when yfinance is unavailable. |
| Yahoo Finance hosts used by the `yfinance` library, for example `query1.finance.yahoo.com` and `query2.finance.yahoo.com` | 443 HTTPS | Hourly FX and metal bars when `KFX_LIVE_DATA=1`. The exact host list depends on the library version. Test after install. |
| Your SMTP provider, for example `smtp.example.com` | 587 TCP with STARTTLS | Verification and reset codes |
| PostgreSQL host | 5432 TCP (or your port) | Operator database, if it is on another host |

Build-time only (not needed on the running host): `pypi.org`, `files.pythonhosted.org`,
and the container registry used for the base image.

Check the tunnel path before you install the connector:

```bash
dig +short region1.v2.argotunnel.com
nc -vz -w 5 <ONE_OF_THE_IPS_FROM_DIG> 7844   # TCP
# Or run the built-in check after install (cloudflared 2026.5.2 or later):
cloudflared tunnel diag
```

## 5. Application software

| Component | Version | Source |
|---|---|---|
| Python | 3.11 (the image uses `python:3.11-slim`) | `Dockerfile` |
| Core packages | numpy ≥ 1.26, pandas ≥ 2.1, scikit-learn ≥ 1.4, fastapi ≥ 0.110, uvicorn[standard] ≥ 0.29, httpx ≥ 0.27, SQLAlchemy 2.x | `requirements.txt` |
| Live market data | yfinance ≥ 1.7, < 2 | `requirements-live.txt` (installed by default in the image) |
| PostgreSQL driver | psycopg[binary] ≥ 3.1 | Installed in the image and in the systemd setup |
| Connector | cloudflared 2026.5.2 or later recommended | Cloudflare package or `cloudflare/cloudflared` image |

The driver resolution for `postgresql+psycopg://` was checked in the sandbox. A live
PostgreSQL connection was **not tested**.

## 6. Data stores

| Store | Requirement | Notes |
|---|---|---|
| Operator database | PostgreSQL, reachable from the origin, with a dedicated role and database | Strongly recommended. Holds accounts, password hashes, sessions, and payment records. SQLite is the fallback and needs a persistent volume. |
| Backups | Off-host storage for database dumps | Required before launch. See the Backups section of the guide. |
| Model cache | `models/` directory, writable | Regenerable. It does not need a backup. |

The app creates its tables at startup and does not run migrations. Take a backup before
each upgrade.

## 7. Configuration and secrets

Every variable is documented in [`deploy/klevs.env.example`](../deploy/klevs.env.example).

| Variable | Required? | Value |
|---|---|---|
| `KLEVS_DATABASE_URL` | Yes for production | `postgresql+psycopg://…?sslmode=require` |
| `KFX_ADMIN_USERNAME`, `KFX_ADMIN_PASSWORD` | Yes, both | Admin login. The password is set again on every start. |
| `KFX_ADMIN_EMAIL`, `KFX_ADMIN_PHONE` | No | Email defaults to the username when it is an address. |
| `KFX_LIVE_DATA` | Yes (set `1` for production, or `0` for synthetic only) | The app defaults to live when unset. |
| `KLEVS_SMTP_HOST`, `KLEVS_SMTP_PORT`, `KLEVS_SMTP_USER`, `KLEVS_SMTP_PASSWORD`, `KLEVS_SMTP_FROM` | Yes, for email codes | Without SMTP the reset and verification codes are not delivered. |
| `KLEVS_DEV_RESET_CODES` | **Must be unset** | Returns codes in API responses. Development only. |
| `TUNNEL_TOKEN` (Docker connector) or the token in the `cloudflared service install` command | Yes | Treat as a secret. It runs the tunnel. |

Secrets to rotate before launch: the admin password that was committed in an earlier
commit. Store secrets in `/etc/klevs/` with owner `root` and group `klevs`, mode `0640`.
Never commit them.

## 8. Decisions and inputs needed from you

- [ ] Production hostname (for example `klevs.example.com`)
- [ ] Operator email addresses for the Access policy
- [ ] Access identity method (one-time PIN or SSO provider)
- [ ] SMTP provider and sender address
- [ ] PostgreSQL location (managed or self-hosted) and backup location
- [ ] Whether to keep `/api/health` public (it returns the risk and signal configuration)
- [ ] Response to the account-existence leak on the 429 lockout (open)
- [ ] Wallet and bank details shown on the payment page (open)

## 9. Go-live gate

All of these must be true before real users sign up:

- [ ] Tunnel shows **Healthy** and the hostname returns the app over HTTPS.
- [ ] `https://<host>/api/health` returns 200 and `cf-cache-status` is not `HIT`.
- [ ] Direct access to the origin's public IP on ports 80, 443 and 8000 fails.
- [ ] `/api/admin/users` returns 401 or 403 without a login.
- [ ] The Access login page appears before `/admin`.
- [ ] A burst of login attempts returns Cloudflare's 429 page.
- [ ] `KLEVS_DEV_RESET_CODES` is unset in the running environment.
- [ ] The admin password has been rotated since the earlier commit.
- [ ] A database backup has been taken and restored once into a scratch database.
- [ ] Memory use during a cold scan is measured and fits the chosen instance.
- [ ] The Docker image has been built and started on a host, which is not yet tested.
