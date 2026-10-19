const DEFAULT_KLEVS_API_ORIGIN = "https://klv-fx-trading-signals.klevco.workers.dev";

const jsonHeaders = {
  "Cache-Control": "no-store",
  "Content-Type": "application/json; charset=utf-8",
};

function errorResponse(status, detail) {
  return new Response(JSON.stringify({ detail }), { status, headers: jsonHeaders });
}

export async function onRequest({ request, env }) {
  const configuredOrigin =
    typeof env.KLEVS_API_ORIGIN === "string" && env.KLEVS_API_ORIGIN.trim() !== ""
      ? env.KLEVS_API_ORIGIN.trim()
      : DEFAULT_KLEVS_API_ORIGIN;

  let apiOrigin;
  try {
    apiOrigin = new URL(configuredOrigin.trim());
  } catch {
    return errorResponse(500, "KLEVS_API_ORIGIN must be an HTTPS origin URL.");
  }

  if (
    apiOrigin.protocol !== "https:" ||
    apiOrigin.username ||
    apiOrigin.password ||
    apiOrigin.pathname !== "/" ||
    apiOrigin.search ||
    apiOrigin.hash
  ) {
    return errorResponse(500, "KLEVS_API_ORIGIN must be an HTTPS origin URL, such as https://api.example.com.");
  }

  const incomingUrl = new URL(request.url);
  const upstreamUrl = new URL(`${incomingUrl.pathname}${incomingUrl.search}`, apiOrigin.origin);

  try {
    // Keep the browser request same-origin; only the fixed, configured API origin is proxied.
    return await fetch(new Request(upstreamUrl, request));
  } catch {
    return errorResponse(502, "The KLEVS API origin is unavailable.");
  }
}
