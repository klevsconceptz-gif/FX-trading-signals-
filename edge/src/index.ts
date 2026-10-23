import { Container, getContainer } from "@cloudflare/containers";
import { containerEnv } from "./container-env.js";
import { handleInternal, type InternalEnv } from "./internal.js";

/**
 * Environment for the Worker. Plain values come from `vars` in wrangler.jsonc;
 * secrets come from `wrangler secret put` and never appear in the repository.
 */
export interface Env extends InternalEnv {
  KLEVS_CONTAINER: DurableObjectNamespace<KlevsContainer>;
  /** Public origin of the Worker, e.g. https://klevs.example.com (no trailing slash). */
  KLEVS_PUBLIC_ORIGIN: string;
  /** Sender address for reset codes. Must be on a domain onboarded to Email Service. */
  KLEVS_MAIL_FROM: string;
  KFX_ADMIN_USERNAME?: string;
  KFX_ADMIN_PASSWORD?: string;
  KFX_ADMIN_EMAIL?: string;
  KFX_ADMIN_PHONE?: string;
  KFX_LIVE_DATA?: string;
}

/** Name of the one container instance. Together with max_instances = 1 this keeps a single writer for the SQLite file. */
export const SINGLETON = "klevs";

/** The Durable Object state type the Container base class expects. */
type ContainerContext = ConstructorParameters<typeof Container>[0];

export class KlevsContainer extends Container<Env> {
  /** The app listens on 8000 inside the image (see Dockerfile). */
  defaultPort = 8000;
  /** Stop the container after 15 minutes without requests. The next request restarts it and restores the database. */
  sleepAfter = "15m";

  constructor(ctx: ContainerContext, env: Env) {
    super(ctx, env);
    this.envVars = containerEnv(env);
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const { pathname } = new URL(request.url);
    if (pathname.startsWith("/__internal/")) {
      return handleInternal(request, env);
    }
    return getContainer(env.KLEVS_CONTAINER, SINGLETON).fetch(request);
  },
} satisfies ExportedHandler<Env>;
