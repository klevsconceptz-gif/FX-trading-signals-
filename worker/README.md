# Cloudflare Worker API

This Worker is the same-origin API target for the Pages proxy. It stores accounts, sessions, payments, access grants, payment settings, and admin audit events in D1, and uses KV for short-lived session/market-cache data.

## Important signal-engine adapter

The Python signal engine in `fxbot/` uses Python libraries that cannot run unchanged inside a Cloudflare Worker. This Worker therefore proxies the protected market endpoints to a separate HTTPS signal-engine origin configured as `KFX_SIGNAL_ENGINE_ORIGIN`:

- `GET /api/scan`
- `GET /api/instructions`
- `GET /api/pair/{symbol}`
- `POST /api/backtest`

The origin must be a separate HTTPS FastAPI service, not this Worker, and it must implement those paths. The Worker first checks the caller's account and paid access, then forwards only the market request with `X-KLEVS-ENGINE-TOKEN`. Set the same high-entropy token as `KFX_SIGNAL_ENGINE_TOKEN` on both the Worker (as a secret) and the FastAPI host (as an environment secret). The FastAPI backend compares it in constant time and accepts it only on the signal routes (`/api/scan`, `/api/instructions`, `/api/pair/{symbol}`, and `/api/backtest`); it does not authorize the universe, account, billing, or admin routes. Normal bearer-session access on the FastAPI app remains supported. The token is never exposed to the browser. The origin and token are intentionally not guessed or hard-coded. Until both are configured, proxied market endpoints return `503`; account, payment, admin, health, and Worker-owned universe routes still run in the Worker.

## Bindings

`wrangler.jsonc` attaches the existing Cloudflare resources:

- D1 binding `klev`, database `klev`.
- KV binding `voom`.

The D1 schema is in `migrations/0001_initial.sql`. Sessions are authoritative in D1 (hashed bearer-token keys); KV caches session metadata briefly and caches scans for up to 60 seconds by default.

## Local development

From the repository root:

```sh
npm ci
npm run worker:migrate:local
npm run worker:dev
```

Create `worker/.dev.vars` locally (it is git-ignored) with test-only values before using the admin login locally:

```dotenv
KFX_ADMIN_USERNAME=operator@example.test
KFX_ADMIN_EMAIL=operator@example.test
KFX_ADMIN_PASSWORD=Local-Only-Strong#2026
# Configure only when you have a separate market engine API:
# KFX_SIGNAL_ENGINE_ORIGIN=https://signals.example.com
# KFX_SIGNAL_ENGINE_TOKEN=use-a-random-local-only-secret
```

Do not use these example credentials in production or commit `.dev.vars`.

Run the Worker unit tests with:

```sh
npm run worker:test
```

## Production setup and deployment

Run these commands from the repository root in an authenticated Cloudflare/Wrangler environment:

1. Apply the migration to the configured remote D1 database:

   ```sh
   npm run worker:migrate:remote
   ```

2. Add admin bootstrap values as Worker secrets, not committed `vars`:

   ```sh
   npx wrangler secret put KFX_ADMIN_USERNAME --config worker/wrangler.jsonc
   npx wrangler secret put KFX_ADMIN_PASSWORD --config worker/wrangler.jsonc
   # Optional if the username is not an email address:
   npx wrangler secret put KFX_ADMIN_EMAIL --config worker/wrangler.jsonc
   ```

   The Worker creates or updates the admin record in D1 when `/api/auth/login` is called. The password is PBKDF2-hashed before it is stored. A changed configured password rotates the hash and invalidates existing sessions.

3. Set `KFX_SIGNAL_ENGINE_ORIGIN` as a Worker variable to the separate signal service's HTTPS origin. Do not point it at this Worker or the Pages site.
4. Set `KFX_SIGNAL_ENGINE_TOKEN` as a Worker secret, and set the exact same high-entropy value in the FastAPI host's environment. For example:

   ```sh
   npx wrangler secret put KFX_SIGNAL_ENGINE_TOKEN --config worker/wrangler.jsonc
   ```

   Restart/redeploy the FastAPI service after setting its environment secret. Never put this token in Pages settings, frontend code, source control, or a regular Worker `vars` block.
5. If password reset by email is required, configure `KLEVS_RESET_EMAIL_URL` and `KLEVS_RESET_EMAIL_TOKEN` as a Worker variable and secret. The endpoint must accept a POST with `{to, subject, text}` and bearer-token authentication. If it is not configured, the generic reset response is still returned, but no email is delivered.
6. Deploy the Worker:

   ```sh
   npm run worker:deploy
   ```

7. In the Cloudflare Pages project, set `KLEVS_API_ORIGIN` to the Worker HTTPS origin for both Production and Preview, then deploy Pages. Do not put admin credentials in Pages settings: Pages variables are not forwarded to the API Worker.

The Worker name in this config targets `klv-fx-trading-signals`. Review the Cloudflare account/Worker target before running a remote migration or deployment. Remote migrations and deployment have not been run from this repository workspace.
