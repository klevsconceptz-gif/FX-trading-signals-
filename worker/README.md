# Cloudflare Worker API

This Worker is the same-origin API target for the Pages proxy. It stores accounts, sessions, payments, access grants, payment settings, and admin audit events in D1, and uses KV for short-lived session/market-cache data.

## Signals (scheduled snapshot)

Signal generation runs in Python, but a Worker cannot run the engine within its CPU limit. The
scheduled workflow `.github/workflows/signals.yml` runs `scripts/publish_signals.py` every hour. It
scans live market data and writes the result to the D1 table `signal_cache` (migration `0002`). The
Worker serves paid users from that table:

- `GET /api/scan`, `GET /api/instructions`, `GET /api/pair/{symbol}` return the stored snapshot.
- They return `503` before the first publish, when the snapshot is older than
  `KFX_SIGNAL_MAX_AGE_SECONDS` (default 7200), and when a row belongs to a run that is not yet
  committed.
- `POST /api/backtest` returns `503` on this deployment. Run `scripts/backtest.py` locally.
- The publisher refuses to publish anything that is not live data.

The Worker has no engine origin or engine token. It makes no outbound calls for signals.

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
KFX_ADMIN_PASSWORD=<choose a local-only password>
```

Do not reuse a local password in production or commit `.dev.vars`.

Run the Worker unit tests with:

```sh
npm run worker:test
```

## Admin console and API

The operator console (`/admin` on the Pages site, `static/admin.html`) signs in through the normal sign-in flow, so the admin logs in with the same form as everyone else. Only `role = admin` can open it. Every route below requires an admin session; a non-admin gets 403.

| Area | Routes |
| --- | --- |
| Accounts | `GET /api/admin/users`, `POST /api/admin/users`, `GET /api/admin/users/{id}`, `PATCH /api/admin/users/{id}` (username, email, phone, `disabled`; `role` may only be `user`), `DELETE /api/admin/users/{id}` |
| Passwords and access | `POST /api/admin/users/{id}/reset-password`, `POST /api/admin/users/{id}/grant`, `POST /api/admin/users/{id}/revoke` (all grants of the account) |
| Grants | `GET /api/admin/grants[?user_id=&status=]`, `PATCH /api/admin/grants/{id}` (`expires_in_days`), `POST /api/admin/grants/{id}/revoke` |
| Payments | `GET /api/admin/payments[?status=]`, `POST /api/admin/payments` (create an invoice), `GET /api/admin/payments/{id}`, `POST /api/admin/payments/{id}/confirm\|reject\|cancel` |
| Settings and log | `GET\|PUT /api/admin/payment-settings`, `GET /api/admin/audit-log?limit=` |

Rules the Worker enforces, whatever the console shows:

- Every write needs a `note` of 3 to 300 characters. It is stored in the audit trail with the operator and the time. The change and its audit row are written in one D1 batch, so a change never happens without its record.
- A refused request writes nothing. Repeating a decision, such as cancelling a cancelled invoice or revoking a revoked grant, returns 409.
- The bootstrap admin (the account named by `KFX_ADMIN_USERNAME`) cannot be edited, disabled, demoted or deleted in the console. Its username, email, phone, role and status come from the Worker secrets, and its password is set by `KFX_ADMIN_PASSWORD`. Changing the secret and redeploying changes the password on the next sign-in.
- An operator cannot disable, demote or delete their own account.
- **Sole admin.** The bootstrap account named by `KFX_ADMIN_USERNAME` is the only admin. The API refuses `role: "admin"` on create and on PATCH (422), so no console or API path can add one. At sign-in the Worker demotes any other stored admin row to `user` and writes a `role_changed` audit row in the same batch. Public sign-up and the console refuse the bootstrap username and email, because an ordinary account holding them would block the admin's sign-in.
- **Native apps.** The Capacitor apps are allowed by CORS from `capacitor://localhost` and `https://localhost` only. They authenticate with the bearer token from `/api/auth/login`, not cookies.
- An account can be deleted only when it has no payment and no access-grant records. Otherwise disable it. Disabling ends its sessions and blocks sign-in, and keeps all records.
- Payment records and audit events are never deleted from the console.

Tests: `npm run worker:test` covers the guard rules offline. `npm run e2e:admin` runs about 84 checks against a running Worker (`BASE_URL`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`), including sign-in, lockout, reset, the role matrix, CRUD and the audit trail. It creates test accounts, invoices and grants, so run it against the local Worker or a staging Worker, never production.

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

3. Publish signals: after the deploy, run the **Publish signals** workflow (or, from a shell with
   `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` set, `python -m scripts.publish_signals`). Until
   the first publish, the signal routes return `503`.
4. If password reset by email is required, configure `KLEVS_RESET_EMAIL_URL` and `KLEVS_RESET_EMAIL_TOKEN` as a Worker variable and secret. The endpoint must accept a POST with `{to, subject, text}` and bearer-token authentication. If it is not configured, the generic reset response is still returned, but no email is delivered.
5. Deploy the Worker:

   ```sh
   npm run worker:deploy
   ```

6. The Pages Function defaults to the configured Worker origin (`https://klv-fx-trading-signals.klevco.workers.dev`) for Production and Preview builds. If you need a different origin, set `KLEVS_API_ORIGIN` in the Pages project for both environments, then deploy Pages. Do not put admin credentials in Pages settings: Pages variables are not forwarded to the API Worker.

The Worker name in this config targets `klv-fx-trading-signals`. Review the Cloudflare account/Worker target before running a remote migration or deployment. Remote migrations and deployment have not been run from this repository workspace.
