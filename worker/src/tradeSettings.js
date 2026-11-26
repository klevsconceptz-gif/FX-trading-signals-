import { ApiError, requireDatabase } from "./http.js";

// Lot sizes a user may choose. Brokers fill whole 0.01-lot steps; 50 lots is a sanity ceiling.
export const MIN_LOT_SIZE = 0.01;
export const MAX_LOT_SIZE = 50;

/** Returns the lot size as a number on the 0.01 grid, or null to clear the choice. Throws 422. */
export function validateLotSize(value) {
  if (value === null) return null;
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new ApiError(422, "lot size must be a number, or null to use the signal size");
  }
  if (value < MIN_LOT_SIZE || value > MAX_LOT_SIZE) {
    throw new ApiError(422, `lot size must be between ${MIN_LOT_SIZE} and ${MAX_LOT_SIZE} lots`);
  }
  const hundredths = Math.round(value * 100);
  if (Math.abs(hundredths / 100 - value) > 1e-9) {
    throw new ApiError(422, "lot size must be a multiple of 0.01 lots");
  }
  return hundredths / 100;
}

export async function getTradeSettings(env, user) {
  const row = await requireDatabase(env)
    .prepare("SELECT lot_size FROM user_trade_settings WHERE user_id = ?")
    .bind(user.id)
    .first();
  return { lot_size: row ? Number(row.lot_size) : null, min_lot: MIN_LOT_SIZE, max_lot: MAX_LOT_SIZE };
}

export async function updateTradeSettings(env, user, body, now = Date.now() / 1000) {
  if (!body || typeof body !== "object" || !Object.hasOwn(body, "lot_size")) {
    throw new ApiError(422, "send lot_size: a number of lots, or null to use the signal size");
  }
  const lot = validateLotSize(body.lot_size);
  const db = requireDatabase(env);
  if (lot === null) {
    await db.prepare("DELETE FROM user_trade_settings WHERE user_id = ?").bind(user.id).run();
  } else {
    await db
      .prepare(`INSERT INTO user_trade_settings (user_id, lot_size, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET lot_size = excluded.lot_size, updated_at = excluded.updated_at`)
      .bind(user.id, lot, now)
      .run();
  }
  return getTradeSettings(env, user);
}
