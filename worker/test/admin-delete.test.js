import test from "node:test";
import assert from "node:assert/strict";
import { adminDeleteGrant, adminDeletePayment } from "../src/admin.js";

// Fake D1 that records every single statement and every batch.
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
            run: async () => { runs.push({ sql, args }); return { meta: { changes: 1 } }; },
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

const auditWritten = (db) => [...db.runs, ...db.batches.flat()].some((s) => /admin_audit_events/.test(s.sql));

test("deleting a payment removes its grant first, in the same transaction, and writes no audit row", async () => {
  const db = recordingDb((sql) => (sql.startsWith("SELECT id FROM payments") ? { id: 7 } : null));
  const result = await adminDeletePayment({ DB: db }, 7);

  assert.equal(db.batches.length, 1, "grant and payment are deleted in one batch");
  const [grantDelete, paymentDelete] = db.batches[0];
  assert.match(grantDelete.sql, /DELETE FROM access_grants WHERE payment_id = \?/, "the grant goes first");
  assert.deepEqual(grantDelete.args, [7]);
  assert.match(paymentDelete.sql, /DELETE FROM payments WHERE id = \?/);
  assert.deepEqual(paymentDelete.args, [7]);
  assert.equal(auditWritten(db), false, "no audit entry is written for a delete");
  assert.deepEqual(result, { ok: true, deleted: { payment_id: 7, grants_removed: 1 } });
});

test("deleting a missing payment is a 404 and changes nothing", async () => {
  const db = recordingDb();
  await assert.rejects(adminDeletePayment({ DB: db }, 99), (err) => err.status === 404);
  assert.equal(db.batches.length, 0);
  assert.equal(db.runs.length, 0);
});

test("deleting a grant removes that row only, with no audit entry", async () => {
  const db = recordingDb((sql) => (sql.startsWith("SELECT id FROM access_grants") ? { id: 3 } : null));
  const result = await adminDeleteGrant({ DB: db }, 3);

  assert.equal(db.runs.length, 1);
  assert.match(db.runs[0].sql, /DELETE FROM access_grants WHERE id = \?/);
  assert.deepEqual(db.runs[0].args, [3]);
  assert.equal(auditWritten(db), false);
  assert.deepEqual(result, { ok: true, deleted: { grant_id: 3 } });
});

test("deleting a missing grant is a 404 and changes nothing", async () => {
  const db = recordingDb();
  await assert.rejects(adminDeleteGrant({ DB: db }, 42), (err) => err.status === 404);
  assert.equal(db.runs.length, 0);
});
