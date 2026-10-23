/**
 * The environment the KLEVS container starts with. Kept free of Cloudflare runtime
 * imports so it can be unit-tested under plain Node.
 */

export interface ContainerSettings {
  /** Shared secret for /__internal/*. Set with `wrangler secret put INTERNAL_TOKEN`. */
  INTERNAL_TOKEN: string;
  /** Public origin of the Worker, e.g. https://klevs.example.com (no trailing slash needed). */
  KLEVS_PUBLIC_ORIGIN: string;
  KFX_ADMIN_USERNAME?: string;
  KFX_ADMIN_PASSWORD?: string;
  KFX_ADMIN_EMAIL?: string;
  KFX_ADMIN_PHONE?: string;
  KFX_LIVE_DATA?: string;
}

/**
 * Build the container environment. Empty values are dropped so the app sees "unset"
 * rather than "set to nothing". KLEVS_DEV_RESET_CODES is never set here: reset codes
 * must not be shown to users in production.
 */
export function containerEnv(settings: ContainerSettings): Record<string, string> {
  const origin = (settings.KLEVS_PUBLIC_ORIGIN ?? "").replace(/\/+$/, "");
  const candidates: Record<string, string | undefined> = {
    KLEVS_REPLICA_URL: origin ? `${origin}/__internal/snapshot` : undefined,
    KLEVS_EMAIL_URL: origin ? `${origin}/__internal/email` : undefined,
    KLEVS_INTERNAL_TOKEN: settings.INTERNAL_TOKEN,
    KFX_ADMIN_USERNAME: settings.KFX_ADMIN_USERNAME,
    KFX_ADMIN_PASSWORD: settings.KFX_ADMIN_PASSWORD,
    KFX_ADMIN_EMAIL: settings.KFX_ADMIN_EMAIL,
    KFX_ADMIN_PHONE: settings.KFX_ADMIN_PHONE,
    KFX_LIVE_DATA: settings.KFX_LIVE_DATA ?? "1",
  };
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(candidates)) {
    if (value !== undefined && value !== "") out[key] = value;
  }
  return out;
}
