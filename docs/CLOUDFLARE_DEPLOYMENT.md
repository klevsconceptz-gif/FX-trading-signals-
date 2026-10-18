# Deploying KLEVS behind Cloudflare

This guide puts the KLEVS FX signals app on the internet with Cloudflare in front of it.
Cloudflare handles DNS, TLS, the WAF, and rate limiting. A **Cloudflare Tunnel** carries
traffic to the app, so the origin server accepts **no inbound connections**.

> **Verification status.** The steps below were checked against this repository as follows:
>
> - Checked locally: the app starts with the production command, `/api/health` responds,
>   admin bootstrap works, and a restart with a new `KFX_ADMIN_PASSWORD` rotates the password.
>   The deploy-file checks in `tests/test_deploy_files.py` pass.
> - **Not checked here:** the Docker image build (Docker is not installed in the build
>   sandbox), the `cloudflared` connector, a live PostgreSQL server, and the Cloudflare dashboard
>   screens. Cloudflare's UI labels change, so follow the names you see and treat the labels in this
>   guide as approximate. Run the checks in [section 11](#11-verify-the-deployment) before you
>   rely on the setup.

---

## 1. Architecture and why

```
 Browser ──HTTPS──▶ Cloudflare edge (DNS, TLS, WAF, rate limits, Access)
                          │  outbound-only tunnel, opened by cloudflared
                          ▼
              origin host (VM or PaaS box)
                cloudflared ──http://localhost:8000──▶ klevs (uvicorn, 1 worker)
                                                          │
                                                          ▼
                                       PostgreSQL (KLEVS_DATABASE_URL)
```

Options that were considered:

| Option | Verdict | Reason |
|---|---|---|
| **Tunnel to a container or VM (this guide)** | Recommended | Runs the full Python stack unchanged. No inbound ports. Works on any Linux host. |
| Native Python Workers | Not used | Workers Free allows 10 ms of CPU per request, and Paid allows 30 s by default (up to 5 min). Memory is capped at 128 MB per isolate. In our sandbox a cold market scan took about 44 s, and the app loads pandas, NumPy, and scikit-learn. Support for these libraries in Workers is not confirmed for this app. There is also no persistent SQLite on Workers. |
| Cloudflare Containers | Not used yet | Needs the Workers Paid plan, plus a Worker and Durable Object wrapper that this repo does not have. Instances start on demand. Disk persistence is not documented, so the database must still live outside the container. |

Cloudflare provides the edge only. The application runs on a host you control. Sections 3 and 4
cover that host.

### Cloudflare Pages build logs

This repository is a Python/FastAPI application, not a Node.js or static-only project. It has no
`package.json`, so a Pages build configured with `npm run build` will fail with `ENOENT` exactly
as expected. Removing that command is not a complete deployment: Pages can serve static files,
but it does not run this ASGI API, so dashboard requests such as `/api/scan` and `/api/pair/...`
would fail. Use the origin-host + Cloudflare Tunnel architecture in this guide for the complete
app. Pages is only suitable if the frontend is split out and configured to reach a separately
hosted API.

---

## 2. Prerequisites

The full checklist, with costs, egress allow-list, and go-live gate, is in
[`CLOUDFLARE_REQUIREMENTS.md`](CLOUDFLARE_REQUIREMENTS.md). Complete it first.

- A domain whose DNS is managed by Cloudflare (for example `klevs.example.com`).
- A Cloudflare account. Tunnels, the WAF rate-limit rule, and Cloudflare Access are available on
  the free plan, with these limits:
  - **Rate limiting:** Free allows **1 rule** that matches on the request **path** only, counts per
    IP, and uses a **10-second** period. Pro allows 2 rules and a period up to 60 seconds.
    Business allows 5 rules and a period up to 10 minutes.
  - **Access:** the Zero Trust free plan covers up to **50 users**.
- A Linux host for the app: 2 vCPU and 2 GB RAM is a reasonable start. The market scan and the
  per-pair model training are CPU-bound. The image is large because of pandas and
  scikit-learn (expect roughly 1 GB; not measured here).
- PostgreSQL 14 or newer, either managed or on another host.
- Outbound access from the host: **TCP and UDP port 7844** to `region1.v2.argotunnel.com` and
  `region2.v2.argotunnel.com` (required for the tunnel), plus HTTPS to `api.frankfurter.dev`, Yahoo
  Finance when live data is on, and your SMTP server on port 587. No inbound ports are needed.

---

## 3. Prepare the origin host

Choose **A (Docker)** or **B (systemd)**. Both bind the app to loopback only.

### A. Docker

Build and start the image from the repository root. The Dockerfile is `Dockerfile`, and the
build context excludes secrets and local data (see `.dockerignore`).

```bash
docker build -t klevs-fx:$(git rev-parse --short HEAD) -t klevs-fx:latest .

docker volume create klevs-data
docker volume create klevs-models

sudo install -d -m 0750 /etc/klevs
sudo install -m 0640 deploy/klevs.env.example /etc/klevs/klevs.env   # then edit it

docker run -d --name klevs --restart unless-stopped \
  --env-file /etc/klevs/klevs.env \
  -p 127.0.0.1:8000:8000 \
  -v klevs-data:/app/data -v klevs-models:/app/models \
  klevs-fx:latest
```

Notes:

- `-p 127.0.0.1:8000:8000` publishes the port **only on loopback**. Do not write `-p 8000:8000`.
  Docker writes its own firewall rules and can expose a published port even when UFW says it is
  closed.
- `data/` holds the SQLite file if `KLEVS_DATABASE_URL` is unset. Keep the volume even when you
  use PostgreSQL, because it keeps the app's working files.
- Check that the container is healthy with `docker ps` (the image has a health check on
  `/api/health`).

### B. systemd on a VM (no Docker)

```bash
sudo useradd --system --home /opt/klevs --shell /usr/sbin/nologin klevs
sudo install -d -o klevs -g klevs /opt/klevs /opt/klevs/data /opt/klevs/models
sudo rsync -a --exclude .git --exclude .venv ./ /opt/klevs/    # or git clone into /opt/klevs
cd /opt/klevs
sudo -u klevs python3.11 -m venv .venv
sudo -u klevs .venv/bin/pip install -r requirements.txt -r requirements-live.txt "psycopg[binary]>=3.1"

sudo install -d -m 0750 -o root -g klevs /etc/klevs
sudo install -m 0640 -o root -g klevs deploy/klevs.env.example /etc/klevs/klevs.env   # then edit it

sudo cp deploy/klevs.service /etc/systemd/system/klevs.service
sudo systemctl daemon-reload
sudo systemctl enable --now klevs
systemctl status klevs --no-pager
```

The unit runs uvicorn on `127.0.0.1:8000` with one worker and systemd hardening. It can write
only to `/opt/klevs/data` and `/opt/klevs/models`.

---

## 4. Configure the application

All settings come from environment variables. The full list, with comments, is in
[`deploy/klevs.env.example`](../deploy/klevs.env.example). Copy it to `/etc/klevs/klevs.env` and
fill in the values below.

| Variable | Production value | Notes |
|---|---|---|
| `KLEVS_DATABASE_URL` | `postgresql+psycopg://USER:PASS@HOST:5432/klevs?sslmode=require` | Strongly recommended. Unset means SQLite at `data/klevs.db`. |
| `KFX_ADMIN_USERNAME` / `KFX_ADMIN_PASSWORD` | Set both | Provisioned on **every** start. See below. |
| `KFX_ADMIN_EMAIL`, `KFX_ADMIN_PHONE` | Optional | Email defaults to the username if it is an address. |
| `KFX_LIVE_DATA` | `1` | `1` uses Yahoo Finance hourly bars. `0` forces the synthetic market. |
| `KLEVS_SMTP_HOST`, `_PORT`, `_USER`, `_PASSWORD`, `_FROM` | Your mail provider | Verification and reset codes are emailed over STARTTLS. Without SMTP, codes are not delivered. |
| `KLEVS_DEV_RESET_CODES` | **Leave unset** | Returns reset codes in API responses. Development only. |

**The admin password is authoritative.** On every start the app sets the admin's password to
`KFX_ADMIN_PASSWORD` and clears any login lockout. This was tested: restarting with a new value
rejected the old password and accepted the new one. Consequences:

- To rotate the admin password, change `KFX_ADMIN_PASSWORD` in the env file, then restart the
  container or service.
- Never leave an old or previously committed password in the env file.
- The password that appeared in an earlier commit of this repository must be treated as
  compromised. Rotate it before going live. Scrubbing Git history is optional and does not replace
  rotation.
- The password must meet the policy: at least 10 characters with upper case, lower case, a number,
  and a symbol.

Keep `/etc/klevs/klevs.env` readable only by root and the app user. Do not commit it.

---

## 5. Database

**Use PostgreSQL in production.** The app stores accounts, password hashes, login state, sessions,
and payment records. These records must not depend on a container's disk.

- Create a dedicated role and database. Give the role access to that database only.
- Use `sslmode=require` when the database is on another host.
- The app creates its tables at startup (`create_all`). It does not run migrations. It adds
  missing tables but does not alter existing ones. **Back up before every upgrade** (section 12).
- Use one app replica. The market engine keeps its caches in process memory, so two replicas would
  each hold separate state.

Check the driver resolves before you start the app. The image includes it, and this was confirmed
in the sandbox:

```bash
python -c "from sqlalchemy import create_engine; print(create_engine('postgresql+psycopg://u:p@h/db').dialect.driver)"
# psycopg
```

---

## 6. Create the Tunnel

1. In the Cloudflare dashboard, open **Networking → Tunnels → Create a tunnel**. Choose
   **Cloudflared** as the connector type. Name it `klevs-prod`.
2. The dashboard shows an install command with a token. **Treat the token as a secret.** It grants
   the ability to run this tunnel.
3. On the origin host, install the connector as a service (VM):

   ```bash
   # Install cloudflared using the package for your distribution (see Cloudflare's install page).
   # Use version 2026.5.2 or later. Its startup checks test DNS, port 7844, and api.cloudflare.com.
   sudo cloudflared service install <TOKEN_FROM_DASHBOARD>
   systemctl status cloudflared --no-pager
   sudo cloudflared tunnel diag      # optional: re-run the connectivity checks
   ```

   The Docker alternative keeps the token in an env file, not on the command line:

   ```bash
   printf 'TUNNEL_TOKEN=%s\n' '<TOKEN_FROM_DASHBOARD>' | sudo tee /etc/klevs/cloudflared.env >/dev/null
   sudo chmod 0600 /etc/klevs/cloudflared.env
   docker run -d --name cloudflared --restart unless-stopped --network host \
     --env-file /etc/klevs/cloudflared.env \
     cloudflare/cloudflared:latest tunnel --no-autoupdate run
   ```

   `--network host` lets the connector reach `localhost:8000`, which Docker published on loopback.
4. Back in the dashboard, the tunnel should show **Healthy** after a few seconds. If it does not, run
   `cloudflared tunnel diag` on the host. The most common cause is blocked egress on port 7844.
5. Open the tunnel's **Published application routes** and add a route:
   - **Hostname:** `klevs.example.com`
   - **Service:** type `HTTP`, URL `localhost:8000` (the Service URL is `http://localhost:8000`).
     The connector reaches it because it runs on the same host.
   - Save. The dashboard creates a proxied DNS record for the hostname.
6. Under **DNS → Records**, confirm the hostname is a proxied record (orange cloud). It should
   point at the tunnel, not at a server IP.

The Tunnel is outbound only. The connector opens connections to Cloudflare. No inbound firewall
rule is needed for it.

---

## 7. Cloudflare settings for the zone

Open the zone (`example.com`) and set the following. Labels may vary slightly.

**SSL/TLS**

- **Overview → Encryption mode:** `Full (strict)`. Tunnel traffic is encrypted between Cloudflare
  and `cloudflared` regardless of this setting, and the connector does not need an origin
  certificate. Full (strict) is still the safe default for the zone.
- **Edge Certificates → Always Use HTTPS:** on.
- **Edge Certificates → Minimum TLS Version:** 1.2.
- **Edge Certificates → HSTS:** enable only after the site works over HTTPS. Start with a short
  max-age, such as 3 months, and raise it later. Do not enable "include subdomains" until you have
  checked every subdomain.

**Caching**

- Cloudflare does not cache HTML or JSON by default. It caches static file types such as `.js` and
  `.css`. Leave the defaults in place.
- Do not add a cache rule that caches `/api/*`. The live signals must not be served from the edge.
- After a deploy, if browsers keep an old `app.js` or `admin.js`, purge the cache for those files
  under **Caching → Configuration → Custom purge**.

**Security**

- **Security → Bots:** enable Bot Fight Mode or the free equivalent. It is a reasonable default for
  a login-bearing site.
- Leave the WAF managed rules on at their default settings.

---

## 8. Rate-limit the login endpoints

The app has no per-IP limit. Its lockout is per account. An attacker can therefore try many
accounts from one address. Add an edge rule for the authentication path. The rule covers
`/api/auth/*`, which includes login, logout, forgot, reset, and the password-policy endpoint.

In **Security → WAF → Rate limiting rules** (or the newer "Security rules" page, if that is where
your account shows it), create a rule:

- **Name:** `klevs-auth-rate`
- **Expression (path):** `starts_with(http.request.uri.path, "/api/auth/")`
- **Counting characteristics:** IP.

Settings by plan:

| Plan | Requests | Period | Mitigation timeout | Notes |
|---|---|---|---|---|
| **Free** | 10 | 10 s | 10 s | The only rule available. It is weak against slow guessing. |
| **Pro** (recommended) | 20 | 60 s | 600 s | Lets you add a second rule, such as one for `/api/admin/*`. |

The rule returns a Cloudflare 429 page. The app's own 429 is a different response, sent when an
account is locked. Section 13 explains the account-existence leak that this rule does not fix.

---

## 9. Protect the operator console with Cloudflare Access

`/admin` and the `/api/admin/*` endpoints are for the operator. Put them behind Access so that
only listed people reach the login page at all.

1. **Zero Trust → Access → Applications → Add an application → Self-hosted.**
2. **Application domain:** `klevs.example.com`. Add these paths, or one application per path if
   your dashboard allows only one:
   - `/admin`
   - `/api/admin/*`
3. **Identity provider:** one-time PIN (email), or your SSO provider.
4. **Policy:** Action `Allow`. Include only the operator email addresses.
5. Save, then open `https://klevs.example.com/admin` in a private window. You should see the
   Access login first.

Access is a second layer. The app's own admin login still applies behind it.

---

## 10. Lock down the origin

The goal is that the app is reachable only through the Tunnel.

- **Firewall:** allow inbound SSH from your admin addresses only. Do not open ports 80, 443, or
  8000 to the internet. The tunnel does not need them.
- **Port binding:** the systemd unit binds to `127.0.0.1`, and the Docker command publishes to
  `127.0.0.1` only. Confirm this after each change:

  ```bash
  sudo ss -ltnp | grep -E ':8000\b'   # expect 127.0.0.1:8000 only
  ```

- **Direct access test:** from a machine outside the host, the public IP must not answer on 8000
  or 80. The request should time out or be refused.
- **Trusted proxy headers:** the app does not read the client IP today, so the forwarded-header
  setting does not change its behaviour. Logs will show `127.0.0.1` as the client, because every
  request arrives through the connector. The real client address is in the `CF-Connecting-IP`
  header. If you later add IP-based logic, read `CF-Connecting-IP` **only** from the connector,
  and never trust `X-Forwarded-For` from the internet.

---

## 11. Verify the deployment

Replace `klevs.example.com` with your hostname. Run these from a machine outside the host.

```bash
H=https://klevs.example.com

# 1. The app answers through Cloudflare.
curl -sI $H/ | head -n 1                              # HTTP/2 200
curl -s $H/api/health; echo                            # {"status":"ok",...}

# 2. HTTP redirects to HTTPS.
curl -sI http://klevs.example.com/ | grep -i '^location'   # https://...

# 3. Live data is on, if you expect it.
curl -s $H/api/health | grep -o '"data_source":"[^"]*"'

# 4. The API is not cached at the edge.
curl -sI $H/api/health | grep -i cf-cache-status       # DYNAMIC (or BYPASS), not HIT

# 5. Admin API refuses anonymous callers.
curl -s -o /dev/null -w "%{http_code}\n" $H/api/admin/users   # 401 or 403

# 6. Rate limit fires. Expect 429 with a Cloudflare page (server: cloudflare).
for i in $(seq 1 15); do
  curl -s -o /dev/null -w "%{http_code} " -H 'content-type: application/json' \
    -d '{"identifier":"nobody@example.com","password":"wrong-password"}' $H/api/auth/login
done; echo

# 7. Origin is not reachable directly. Run from an outside machine against the server's public IP.
curl -m 5 http://<PUBLIC_IP>:8000/api/health || echo "good: not reachable"
```

Also check in a browser: login works, the dashboard loads, the admin console asks for Access
first, and logout ends the session.

Known exposure to review: `/api/health` is public and returns the risk and signal configuration
(equity, risk per trade, and so on). Decide whether that is acceptable before launch. Restricting
it means a code change, which is not included in this guide.

---

## 12. Operations

**Logs**

- Docker: `docker logs -f klevs` and `docker logs -f cloudflared`.
- systemd: `journalctl -u klevs -f` and `journalctl -u cloudflared -f`.

**Upgrade**

```bash
cd /path/to/repo && git pull
# Back up first (see Backups below), then:
docker build -t klevs-fx:$(git rev-parse --short HEAD) -t klevs-fx:latest .
docker stop klevs && docker rm klevs
docker run -d --name klevs --restart unless-stopped --env-file /etc/klevs/klevs.env \
  -p 127.0.0.1:8000:8000 -v klevs-data:/app/data -v klevs-models:/app/models klevs-fx:latest
```

For systemd, run `git pull`, reinstall requirements if they changed, then `sudo systemctl restart klevs`.

Keep the previous image tag (the git short SHA) so you can roll back by starting that tag.

**Backups**

- PostgreSQL: `pg_dump --format=custom --file klevs-$(date +%F).dump "$KLEVS_DATABASE_URL"`. Store
  the dump off the host, encrypted.
- SQLite (only if you chose it): `sqlite3 data/klevs.db ".backup 'klevs-backup.db'"`, run while the
  app is stopped or with the file copied in full.
- `models/` is regenerated from the data. It does not need a backup.

**Restore drill:** restore a backup into a scratch database and start the app against it once
before you rely on the backup.

---

## 13. Launch checklist and open items

These are not solved by this guide. Resolve them before taking real payments or real users.

- [ ] **Rotate the admin password** that appeared in an earlier commit (section 4).
- [ ] **Account-existence leak on lockout.** When an account is locked, the login endpoint returns
  429 with a lockout message. That response can reveal whether an account exists. The edge rule
  (section 8) limits per-IP guessing, but it does not remove this leak. The mitigation is an open
  decision.
- [ ] **Payment details.** The wallet and bank details shown to customers still need confirming.
  Review them before launch.
- [ ] **Pricing wording** on the public pages needs final approval.
- [ ] **Unbuilt Pro and Elite features.** Do not advertise features the app does not yet deliver.
- [ ] **Public `/api/health` detail** (section 11).
- [ ] **Tested build.** Build the image and run the verification steps on a real host with Docker and
  `cloudflared` installed. This guide was not tested end to end in the sandbox.

---

## 14. Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Tunnel shows **Down** or **Inactive** | The connector is not running or the token is wrong. Check `systemctl status cloudflared` or `docker logs cloudflared`. |
| Cloudflare **502** | The route points at the wrong URL, or the app is down. Check that the Service is `http://localhost:8000` and that `curl http://127.0.0.1:8000/api/health` works on the host. |
| Cloudflare **1033** or **530** | The tunnel is not connected. Check the connector logs. |
| App container exits on start | A missing admin variable or a weak admin password. The log shows the reason. Both `KFX_ADMIN_USERNAME` and `KFX_ADMIN_PASSWORD` must be set, and the password must meet the policy. |
| Admin login fails after a restart | `KFX_ADMIN_PASSWORD` in the env file is the active password. Use that value. |
| `data_source` shows synthetic | `KFX_LIVE_DATA` is `0`, or Yahoo Finance is unreachable. The app falls back to synthetic data and shows the reason in the dashboard. |
| Permission denied on `/app/data` or `/app/models` | The volume is owned by the wrong user. The image uses UID 10001. For a bind mount, run `sudo chown -R 10001:10001` on the host directory. |
| Browser shows an old UI after a deploy | Purge the edge cache for `app.js`, `admin.js`, and the style files (section 7). |
| Reset codes never arrive | SMTP is not configured. Set the `KLEVS_SMTP_*` variables. Do not use `KLEVS_DEV_RESET_CODES` in production. |

---

## Appendix: files in this repository

| File | Purpose |
|---|---|
| `Dockerfile` | Production image: Python 3.11 slim, non-root user, health check, one uvicorn worker on port 8000. |
| `.dockerignore` | Keeps `.env` files, databases, models, Git data, and tests out of the image. |
| `deploy/klevs.env.example` | Every environment variable, with production guidance. Contains no real secrets. |
| `deploy/klevs.service` | systemd unit for running the app on a VM without Docker. |
| `tests/test_deploy_files.py` | Checks that the template documents every variable the code reads, that COPY sources exist, that the build context excludes secrets, and that the unit binds to loopback. |
