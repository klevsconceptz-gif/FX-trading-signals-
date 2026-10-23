# klevs-edge: Cloudflare Worker for the KLEVS container

This Worker is the public entry point of the Cloudflare-only deployment. It owns the
`KlevsContainer` (the FastAPI app), the token-checked `/__internal/snapshot` route (R2 database
snapshot), and the `/__internal/email` route (Cloudflare Email Service).

The full runbook is [`docs/CLOUDFLARE_DEPLOYMENT.md`](../docs/CLOUDFLARE_DEPLOYMENT.md). The
checklist is [`docs/CLOUDFLARE_REQUIREMENTS.md`](../docs/CLOUDFLARE_REQUIREMENTS.md).

## Commands

```sh
npm ci                     # install (Node 20+)
npm test                   # type-check and run the unit tests
npm run deploy:dry-run     # validate bindings (add --containers-rollout=none if Docker is absent)
npm run deploy             # build the container image and deploy
npm run dev                # local development with wrangler dev (needs Docker for the container)
```

## Files

- `wrangler.jsonc`: bindings (R2 `BACKUPS`, Email `EMAIL`, Durable Object `KLEVS_CONTAINER`),
  the container, and the public vars. Replace the placeholder hostname before deploying.
- `src/index.ts`: the Worker entry point and the `KlevsContainer` class.
- `src/container-env.ts`: the environment passed to the container. Empty values are dropped.
- `src/internal.ts`: the token-checked internal routes.
- `test/`: unit tests for the internal routes and the container environment.
- `.dev.vars.example`: placeholder values for local development. Copy to `.dev.vars` and never commit that file.

Secrets are set with `wrangler secret put`: `INTERNAL_TOKEN`, `KFX_ADMIN_USERNAME`,
`KFX_ADMIN_PASSWORD`, and optionally `KFX_ADMIN_EMAIL`.
