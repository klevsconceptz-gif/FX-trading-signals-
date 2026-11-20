// Deletes the Telegram webhook that the Worker used for private-channel join events. Site-based channel
// access has been removed, so nothing listens for those events any more. Telegram keeps a set webhook until
// it is deleted, and its updates would keep failing against a removed route. Signal posts do not use a
// webhook and are unaffected. Prints only Telegram's description, never the token.
//
//   TELEGRAM_BOT_TOKEN=... node scripts/remove_telegram_webhook.mjs

const token = (process.env.TELEGRAM_BOT_TOKEN || "").trim();

if (!token) {
  console.log("telegram webhook: skipped (TELEGRAM_BOT_TOKEN not set)");
  process.exit(0);
}

const response = await fetch(`https://api.telegram.org/bot${token}/deleteWebhook`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ drop_pending_updates: true }),
});
const body = await response.json().catch(() => ({}));
if (!response.ok || body.ok !== true) {
  const reason = typeof body.description === "string" ? body.description : `HTTP ${response.status}`;
  console.error(`::error title=Telegram webhook removal failed::deleteWebhook: ${reason}`);
  process.exit(1);
}
console.log("telegram webhook: removed");
