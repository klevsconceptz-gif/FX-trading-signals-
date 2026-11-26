import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.js";
import { validateLotSize, getTradeSettings, updateTradeSettings } from "../src/tradeSettings.js";

// Fake D1 for user_trade_settings: a Map keyed by user_id.
function fakeEnv() {
  const rows = new Map();
  return {
    rows,
    DB: {
      prepare(sql) {
        return {
          bind(...args) {
            return {
              first: async () => {
                if (/SELECT lot_size FROM user_trade_settings/.test(sql)) {
                  const row = rows.get(args[0]);
                  return row ? { lot_size: row.lot_size } : null;
                }
                throw new Error(`unexpected first(): ${sql}`);
              },
              run: async () => {
                if (/^DELETE FROM user_trade_settings/.test(sql)) {
                  rows.delete(args[0]);
                } else if (/^INSERT INTO user_trade_settings/.test(sql)) {
                  rows.set(args[0], { lot_size: args[1], updated_at: args[2] });
                } else {
                  throw new Error(`unexpected run(): ${sql}`);
                }
                return { success: true };
              },
            };
          },
        };
      },
    },
  };
}

const USER = { id: 7, username: "trader" };

test("lot sizes must be on the 0.01 grid within 0.01 to 50 lots", () => {
  assert.equal(validateLotSize(0.5), 0.5);
  assert.equal(validateLotSize(0.01), 0.01);
  assert.equal(validateLotSize(50), 50);
  assert.equal(validateLotSize(null), null);
  for (const bad of [0, 0.005, 0.499, 51, -1, NaN, Infinity, "0.5", undefined, true]) {
    assert.throws(() => validateLotSize(bad), (e) => e.status === 422, String(bad));
  }
});

test("a user's lot size is saved, read back, and replaced without duplicates", async () => {
  const env = fakeEnv();
  assert.deepEqual(await getTradeSettings(env, USER), { lot_size: null, min_lot: 0.01, max_lot: 50 });

  await updateTradeSettings(env, USER, { lot_size: 0.5 }, 100);
  assert.equal((await getTradeSettings(env, USER)).lot_size, 0.5);

  await updateTradeSettings(env, USER, { lot_size: 0.25 }, 200);
  assert.equal(env.rows.size, 1);
  assert.equal((await getTradeSettings(env, USER)).lot_size, 0.25);
});

test("another user's choice never changes yours", async () => {
  const env = fakeEnv();
  await updateTradeSettings(env, USER, { lot_size: 2 });
  await updateTradeSettings(env, { id: 8 }, { lot_size: 0.1 });
  assert.equal((await getTradeSettings(env, USER)).lot_size, 2);
  assert.equal((await getTradeSettings(env, { id: 8 })).lot_size, 0.1);
});

test("null clears the choice so the signal size applies again", async () => {
  const env = fakeEnv();
  await updateTradeSettings(env, USER, { lot_size: 1 });
  await updateTradeSettings(env, USER, { lot_size: null });
  assert.equal((await getTradeSettings(env, USER)).lot_size, null);
});

test("a body without lot_size is rejected", async () => {
  await assert.rejects(updateTradeSettings(fakeEnv(), USER, {}), (e) => e.status === 422);
  await assert.rejects(updateTradeSettings(fakeEnv(), USER, null), (e) => e.status === 422);
});

test("the endpoints need a logged-in user", async () => {
  const env = fakeEnv();
  for (const method of ["GET", "PUT"]) {
    const res = await worker.fetch(
      new Request("https://api.example/api/me/trade-settings", {
        method,
        headers: { "Content-Type": "application/json" },
        body: method === "PUT" ? JSON.stringify({ lot_size: 0.5 }) : undefined,
      }),
      env,
    );
    assert.equal(res.status, 401, method);
  }
  assert.equal(env.rows.size, 0);
});
