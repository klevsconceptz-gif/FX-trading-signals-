import test from "node:test";
import assert from "node:assert/strict";
import worker from "../src/index.js";
import { adminCreatePayment, adminRevokeGrant, adminUpdateGrant } from "../src/admin.js";

// Fake D1 that records every single statement and every batch, so a test can see whether an
// audit row was written by its own statement or inside the change's batch.
function recordingDb(answer = () => null) {
  const runs = [];
  const batches = [];
  return {
    runs,
    batches,
    prepare(sql) {
      return {
        bind(...args) {
          return {
            sql,
            args,
            first: async () => answer(sql, args),
            all: async () => ({ results: [] }),
            run: async () => { runs.push({ sql, args }); return { meta: { changes: 1, last_row_id: 1 } }; },
          };
        },
      };
    },
    batch: async (statements) => {
      batches.push(statements.map((s) => ({ sql: s.sql, args: s.args })));
      return statements.map(() => ({ meta: { changes: 1 } }));
    },
  };
}

const OPERATOR = { id: 1, username: "ops_two" };
const BOOTSTRAP = "opsadmin";
const env = { KFX_ADMIN_USERNAME: BOOTSTRAP };

test("the cron sweeps expired sessions and keeps live ones", async () => {
  const db = recordingDb();
  const pending = [];
  const ctx = { waitUntil: (promise) => pending.push(promise) };
  const before = Math.floor(Date.now() / 1000);
  await worker.scheduled({}, { DB: db }, ctx);
  await Promise.all(pending);
  const sweep = db.runs.find((r) => /DELETE FROM user_sessions WHERE expires_at <= \?/.test(r.sql));
  assert.ok(sweep, "a DELETE of expired sessions runs on the cron");
  const cutoff = sweep.args[0];
  assert.equal(typeof cutoff, "number");
  assert.ok(cutoff >= before && cutoff <= Math.floor(Date.now() / 1000) + 1, "the cutoff is the current time in seconds");
});

test("an operator-created invoice and its audit row are written in one batch", async () => {
  const settings = JSON.stringify({ crypto: [], wire: { account_number: "12345678" } });
  const invoiceRow = {
    id: 9, invoice: "KFX-TEST", plan: "starter", amount_usd: 99, method: "wire", network: null,
    pay_to: "{}", status: "awaiting_payment", created_at: 1, user_id: 2,
  };
  const db = recordingDb((sql) => {
    if (sql.includes("FROM settings")) return { value: settings };
    if (sql.includes("SELECT * FROM users WHERE id")) return { id: 2, username: "buyer_two", role: "user", disabled_at: 0 };
    if (sql.includes("SELECT * FROM payments WHERE invoice")) return invoiceRow;
    return null;
  });
  await adminCreatePayment({ ...env, DB: db }, OPERATOR, { user_id: 2, plan: "starter", method: "wire", note: "wire follow-up" });

  assert.equal(db.batches.length, 1, "the invoice and the audit row go in a single batch");
  const batch = db.batches[0];
  assert.ok(batch.some((s) => /INSERT INTO payments/.test(s.sql)), "the invoice insert is in the batch");
  const auditWrite = batch.find((s) => /INSERT INTO admin_audit_events/.test(s.sql));
  assert.ok(auditWrite, "the audit insert is in the same batch");
  assert.match(auditWrite.sql, /'payment_created'/);
  assert.ok(!db.runs.some((r) => /admin_audit_events/.test(r.sql)), "no audit row is written on its own");
});

test("a grant revoke writes its audit row only inside the revoke batch", async () => {
  const grant = { id: 5, user_id: 2, username: "buyer_two", plan: "starter", status: "active", expires_at: 9e9 };
  const db = recordingDb((sql) => {
    if (sql.includes("FROM access_grants")) return grant;
    return null;
  });
  await adminRevokeGrant({ ...env, DB: db }, 5, OPERATOR, { note: "chargeback" });

  assert.equal(db.batches.length, 1);
  const [update, audit] = db.batches[0];
  assert.match(update.sql, /UPDATE access_grants SET status = 'revoked'[\s\S]*WHERE id = \? AND status = 'active'/);
  // The audit row can only match the grant this request revoked, so a refused revoke leaves none.
  assert.match(audit.sql, /WHERE g\.id = \? AND g\.status = 'revoked' AND g\.revoked_at = \? AND g\.revoked_by = \?/);
  assert.ok(!db.runs.some((r) => /admin_audit_events/.test(r.sql)));
});

test("an expiry change writes its audit row only inside the update batch, matched to the new expiry", async () => {
  const grant = { id: 6, user_id: 2, username: "buyer_two", plan: "pro", status: "active", expires_at: 9e9 };
  const db = recordingDb((sql) => {
    if (sql.includes("FROM access_grants")) return grant;
    return null;
  });
  await adminUpdateGrant({ ...env, DB: db }, 6, OPERATOR, { note: "extended by request", expires_in_days: 30 });

  assert.equal(db.batches.length, 1);
  const [update, audit] = db.batches[0];
  assert.match(update.sql, /UPDATE access_grants SET expires_at = \? WHERE id = \? AND status = 'active'/);
  assert.match(audit.sql, /WHERE g\.id = \? AND g\.status = 'active' AND g\.expires_at = \?/);
  assert.ok(!db.runs.some((r) => /admin_audit_events/.test(r.sql)));
});
