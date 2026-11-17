// Registers the Worker's Telegram webhook (chat_member updates for the private channel).
// Run by the deploy workflow after the health check. Without TELEGRAM_WEBHOOK_SECRET it does nothing,
// so the deploy still works before the secret is set. Prints only Telegram's description, never the token.
//
//   TELEGRAM_BOT_TOKEN=... TELEGRAM_WEBHOOK_SECRET=... API_ORIGIN=https://... node scripts/register_telegram_webhook.mjs

const token = (process.env.TELEGRAM_BOT_TOKEN || "").trim();
const secret = (process.env.TELEGRAM_WEBHOOK_SECRET || "").trim();
const origin = (process.env.API_ORIGIN || "").trim().replace(/\/+$/, "");

if (!token || !secret) {
  console.log("telegram webhook: skipped (TELEGRAM_BOT_TOKEN or TELEGRAM_WEBHOOK_SECRET not set)");
  process.exit(0);
}
if (!origin.startsWith("https://")) {
  console.error("::error title=Telegram webhook failed::API_ORIGIN must be an https URL");
  process.exit(1);
}

const response = await fetch(`https://api.telegram.org/bot${token}/setWebhook`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    url: `${origin}/api/internal/telegram/webhook`,
    secret_token: secret,
    allowed_updates: ["chat_member"],
  }),
});
const body = await response.json().catch(() => ({}));
if (!response.ok || body.ok !== true) {
  const reason = typeof body.description === "string" ? body.description : `HTTP ${response.status}`;
  console.error(`::error title=Telegram webhook failed::setWebhook: ${reason}`);
  process.exit(1);
}
console.log("telegram webhook: set (chat_member updates)");
