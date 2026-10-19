# Admin login and access guide

## Sign in

Admin users use the same username-and-password sign-in flow as regular users:

1. Open the site and choose **Log in**, or go directly to `/admin`.
2. Enter the admin username (or email) and password.
3. When the API confirms the account has `role: "admin"`, the normal sign-in flow opens the Admin Console at `/admin`.

A regular user account cannot access the console. The API enforces the role on admin endpoints; browser visibility is not the security boundary.

Routine sign-in does not use an email code. Email is only used for an explicitly requested password reset. If Cloudflare Access is enabled, its access prompt is separate from the app's username-and-password login.

## Provisioning for the Worker API

The repository now includes the Worker API at `worker/src/index.js`, with D1 binding `klev` and KV binding `voom` in `worker/wrangler.jsonc`. On `/api/auth/login`, the Worker reads its admin bootstrap settings and creates or updates the admin account in D1. It hashes the password with PBKDF2 before storage; a changed configured password invalidates existing sessions.

In the API Worker's **Settings → Variables and Secrets**, configure:

- `KFX_ADMIN_USERNAME` — admin login name. Store as a secret if you want the name hidden too.
- `KFX_ADMIN_PASSWORD` — admin password; **must be a secret**.
- `KFX_ADMIN_EMAIL` — optional if the username itself is not a valid email address.
- `KFX_ADMIN_PHONE` — optional.

The password must be at least 10 characters and include uppercase, lowercase, a number, and a symbol. Set these on the **API Worker**, not the Pages project. Never put credential values in `wrangler.jsonc`, source control, or chat.

The Pages project should contain only `KLEVS_API_ORIGIN`, set to the API Worker's HTTPS origin for both Production and Preview. Pages variables are not forwarded to the external Worker.

For full local and production commands, including D1 migrations and Worker deployment, see [`worker/README.md`](../worker/README.md). Live Cloudflare secrets, remote migrations, and deployment have not been applied from this workspace.

## Important signal-engine requirement

Account, payment, admin, health, and universe APIs run in the Worker. The computational signal engine still uses the Python modules under `fxbot/`, which cannot run unchanged in a Worker. Configure `KFX_SIGNAL_ENGINE_ORIGIN` on the Worker to a **separate HTTPS signal-engine API origin** for scans, instructions, pair detail, and backtests. Without it those routes return `503`; this is documented in the Worker README.

## If using the FastAPI backend instead

The existing FastAPI service also supports admin bootstrap from its own runtime environment. Set `KFX_ADMIN_USERNAME` and `KFX_ADMIN_PASSWORD` on the **FastAPI service**, not on Pages. `KFX_ADMIN_EMAIL` and `KFX_ADMIN_PHONE` are optional. On each startup, the configured password is authoritative and rotates the admin password.

## Troubleshooting

- **Incorrect credentials / 401:** check the Worker secrets and that the admin bootstrap succeeds in D1.
- **“This account is not an operator account” / 403:** the account authenticated but does not have the `admin` role. Do not change the role in browser storage.
- **Pages reports that `KLEVS_API_ORIGIN` is not configured:** set it on the Pages project for the relevant environment, then deploy Pages.
- **Admin page loads but API calls fail:** verify the Worker deployment, D1/KV bindings, remote D1 migration, and Pages API origin.
- **Scan, instructions, pair or backtest returns 503:** configure a separate `KFX_SIGNAL_ENGINE_ORIGIN`; do not point it back to the Worker itself.
- **Password reset message arrives but no email:** configure the Worker reset-mail HTTP endpoint and token as described in `worker/README.md`.

If a password is exposed, rotate it in the API runtime and redeploy. Do not use test credentials from the repository.