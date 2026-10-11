/* klevs FX trading App — operator console.
   Only accounts with role=admin can use it; every write is also enforced by the server. */
"use strict";

const SESSION_KEY = "kfx_sub";
const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[c]);
const fmtDate = (ts) => (ts ? new Date(ts * 1000).toLocaleString() : "—");

function session() {
  try { return JSON.parse(localStorage.getItem(SESSION_KEY) || "null"); } catch (_) { return null; }
}

async function api(method, url, payload) {
  const s = session();
  const headers = {};
  if (s && s.token) headers.Authorization = `Bearer ${s.token}`;
  if (payload !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(url, { method, headers, body: payload === undefined ? undefined : JSON.stringify(payload) });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    const err = new Error(typeof body.detail === "string" ? body.detail : `request failed (${r.status})`);
    err.status = r.status;
    throw err;
  }
  return body;
}

function showGate(message) {
  $("#console").classList.add("hidden");
  $("#login").classList.remove("hidden");
  $("#btn-logout").classList.toggle("hidden", !session());
  $("#who").textContent = "";
  $("#lg-err").textContent = message || "";
}

function showConsole(who) {
  $("#login").classList.add("hidden");
  $("#console").classList.remove("hidden");
  $("#btn-logout").classList.remove("hidden");
  $("#who").textContent = `Signed in as ${who}`;
}

/* ---------- sign in ---------- */
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("#lg-submit");
  btn.disabled = true;
  try {
    const data = await api("POST", "/api/auth/login", {
      identifier: $("#lg-ident").value.trim(),
      password: $("#lg-pass").value,
    });
    if (data.user.role !== "admin") {
      localStorage.removeItem(SESSION_KEY);
      throw new Error("This account is not an operator account.");
    }
    localStorage.setItem(SESSION_KEY, JSON.stringify({ token: data.token, username: data.user.username, role: "admin" }));
    $("#lg-pass").value = "";
    showConsole(data.user.username);
    loadPayments();
  } catch (err) {
    $("#lg-err").textContent = err.message;
  } finally {
    btn.disabled = false;
  }
});

$("#btn-logout").addEventListener("click", async () => {
  try { await api("POST", "/api/auth/logout", {}); } catch (_) { /* already gone */ }
  localStorage.removeItem(SESSION_KEY);
  showGate("Signed out.");
});

/* every admin call that returns 401/403 goes back to the gate */
function guard(err) {
  if (err.status === 401 || err.status === 403) {
    localStorage.removeItem(SESSION_KEY);
    showGate(err.status === 401 ? "Session ended. Sign in again." : "Operator account required.");
    return true;
  }
  return false;
}

function setErr(msg) { $("#g-err").textContent = msg || ""; }

/* ---------- tabs ---------- */
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === t));
  ["payments", "users", "settings"].forEach((name) =>
    $(`#tab-${name}`).classList.toggle("hidden", name !== t.dataset.tab));
  if (t.dataset.tab === "payments") loadPayments();
  if (t.dataset.tab === "users") loadUsers();
  if (t.dataset.tab === "settings") loadSettings();
}));

/* ---------- payments ---------- */
async function loadPayments() {
  setErr("");
  const status = $("#pay-filter").value;
  try {
    const data = await api("GET", `/api/admin/payments${status ? `?status=${encodeURIComponent(status)}` : ""}`);
    renderPayments(data.payments);
  } catch (err) {
    if (!guard(err)) setErr(err.message);
  }
}

function renderPayments(rows) {
  const tbody = $("#pay-rows");
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="note">No payments in this view.</td></tr>`;
    return;
  }
  tbody.innerHTML = rows.map((p) => {
    const actionable = p.status === "submitted";
    const ref = p.reference ? `<code>${esc(p.reference)}</code>` : `<span class="note">not submitted yet</span>`;
    const sender = p.sender_note ? `<div class="note">${esc(p.sender_note)}</div>` : "";
    const decided = p.decision_note ? `<div class="note">Note: ${esc(p.decision_note)}</div>` : "";
    const decision = actionable ? `
      <div class="row">
        <input data-note-for="${p.id}" placeholder="note (required to reject)" maxlength="300" style="min-width:180px" />
        <button class="ok" data-act="confirm" data-id="${p.id}">Confirm &amp; grant</button>
        <button class="bad" data-act="reject" data-id="${p.id}">Reject</button>
      </div>` : `<span class="note">${esc(p.decided_by ? `by ${p.decided_by} · ${fmtDate(p.decided_at)}` : "—")}</span>${decided}`;
    return `<tr>
      <td><code>${esc(p.invoice)}</code><div class="note">${fmtDate(p.created_at)}</div></td>
      <td>${esc(p.user.username)}<div class="note">${esc(p.user.email)}<br>${esc(p.user.phone || "")}</div></td>
      <td>${esc(p.plan_name)}</td>
      <td>$${esc(Number(p.amount_usd).toFixed(2))}</td>
      <td>${esc(p.method === "crypto" ? `crypto · ${p.network}` : "bank wire")}</td>
      <td>${ref}${sender}</td>
      <td><span class="pill s-${esc(p.status)}">${esc(p.status.replace("_", " "))}</span></td>
      <td>${decision}</td>
    </tr>`;
  }).join("");
}

$("#tab-payments").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const id = btn.dataset.id;
  const note = document.querySelector(`[data-note-for="${id}"]`).value.trim();
  if (btn.dataset.act === "reject" && note.length < 3) {
    setErr("A rejection needs a reason of at least 3 characters — the buyer sees it.");
    return;
  }
  const confirmText = btn.dataset.act === "confirm"
    ? "Confirm this payment and grant 30 days of access? Only do this after checking the wallet or bank statement."
    : "Reject this payment? The buyer will see the reason and can try again.";
  if (!window.confirm(confirmText)) return;
  btn.disabled = true;
  try {
    await api("POST", `/api/admin/payments/${id}/${btn.dataset.act}`, { note });
    setErr("");
    loadPayments();
  } catch (err) {
    if (!guard(err)) setErr(err.message);
    btn.disabled = false;
  }
});
$("#pay-filter").addEventListener("change", loadPayments);
$("#pay-refresh").addEventListener("click", loadPayments);

/* ---------- users & access ---------- */
async function loadUsers() {
  setErr("");
  try {
    const data = await api("GET", "/api/admin/users");
    const list = data.users;
    $("#user-list").innerHTML = list.map((u) => `<option value="${esc(u.username)}">`).join("");
    $("#user-rows").innerHTML = list.map((u) => {
      const a = u.access;
      const access = a.active
        ? `<span class="pill s-confirmed">${esc(a.plan === "admin" ? "operator" : a.plan)}</span>
           ${a.expires_at ? `<div class="note">until ${fmtDate(a.expires_at)}</div>` : ""}`
        : `<span class="pill s-awaiting_payment">no access</span>`;
      const revoke = a.active && a.plan !== "admin"
        ? `<button class="bad" data-revoke="${u.id}">Revoke</button>` : "";
      return `<tr>
        <td>${esc(u.username)}</td>
        <td>${esc(u.email)}</td>
        <td>${esc(u.phone || "")}</td>
        <td>${esc(u.role)}</td>
        <td>${access}</td>
        <td>${revoke}</td>
      </tr>`;
    }).join("") || `<tr><td colspan="6" class="note">No accounts yet.</td></tr>`;
  } catch (err) {
    if (!guard(err)) setErr(err.message);
  }
}

$("#us-refresh").addEventListener("click", loadUsers);

async function resolveUserId(ident) {
  const data = await api("GET", "/api/admin/users");
  const hit = data.users.find((u) => u.username === ident || u.email === ident);
  if (!hit) throw new Error(`No account matches "${ident}".`);
  return hit.id;
}

$("#gr-submit").addEventListener("click", async () => {
  const msg = $("#gr-msg");
  msg.textContent = "";
  const ident = $("#gr-user").value.trim();
  const note = $("#gr-note").value.trim();
  if (!ident) { msg.textContent = "Enter the username or email."; return; }
  if (note.length < 3) { msg.textContent = "A note is required for a manual grant."; return; }
  if (!window.confirm(`Grant ${$("#gr-plan").value} access to ${ident} for ${$("#gr-days").value} days?`)) return;
  try {
    const id = await resolveUserId(ident);
    await api("POST", `/api/admin/users/${id}/grant`, {
      plan: $("#gr-plan").value,
      days: Number($("#gr-days").value),
      note,
    });
    msg.textContent = `Access granted to ${ident}.`;
    $("#gr-note").value = "";
    loadUsers();
  } catch (err) {
    if (!guard(err)) msg.textContent = err.message;
  }
});

$("#user-rows").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-revoke]");
  if (!btn) return;
  const note = window.prompt("Reason for revoking access (required, shown in the audit trail):", "");
  if (note === null) return;
  if (note.trim().length < 3) { setErr("A revoke needs a reason of at least 3 characters."); return; }
  try {
    await api("POST", `/api/admin/users/${btn.dataset.revoke}/revoke`, { note: note.trim() });
    setErr("");
    loadUsers();
  } catch (err) {
    if (!guard(err)) setErr(err.message);
  }
});

/* ---------- payment details ---------- */
const WIRE_FIELDS = [
  ["bank_name", "Bank name"], ["account_name", "Account name"], ["account_number", "Account number"],
  ["routing_or_swift", "Routing / SWIFT"], ["iban", "IBAN"], ["instructions", "Instructions for the buyer"],
];

function cryptoRow(network = "", address = "") {
  const row = document.createElement("div");
  row.className = "row crypto-row";
  row.style.marginBottom = "8px";
  row.innerHTML = `
    <input class="c-net" placeholder="Network, e.g. BTC, ETH, USDT-TRC20" maxlength="20" value="${esc(network)}" style="width:220px" />
    <input class="c-addr" placeholder="Wallet address" maxlength="200" value="${esc(address)}" style="flex:1;min-width:260px" />
    <button type="button" class="bad c-del">Remove</button>`;
  return row;
}

function renderSettings(s) {
  const wrap = $("#crypto-rows");
  wrap.innerHTML = "";
  (s.crypto.length ? s.crypto : [{ network: "", address: "" }]).forEach((c) => wrap.appendChild(cryptoRow(c.network, c.address)));
  $("#wire-fields").innerHTML = WIRE_FIELDS.map(([k, label]) => `
    <label class="f">${esc(label)}
      ${k === "instructions"
        ? `<textarea data-wire="${k}" rows="2" maxlength="500">${esc(s.wire[k])}</textarea>`
        : `<input data-wire="${k}" maxlength="120" value="${esc(s.wire[k])}" />`}
    </label>`).join("");
}

async function loadSettings() {
  setErr("");
  try {
    renderSettings(await api("GET", "/api/admin/payment-settings"));
  } catch (err) {
    if (!guard(err)) setErr(err.message);
  }
}

$("#add-crypto").addEventListener("click", () => $("#crypto-rows").appendChild(cryptoRow()));
$("#crypto-rows").addEventListener("click", (e) => {
  if (e.target.closest(".c-del")) e.target.closest(".crypto-row").remove();
});

$("#set-save").addEventListener("click", async () => {
  const msg = $("#set-msg");
  msg.textContent = "";
  const crypto = Array.from(document.querySelectorAll(".crypto-row"))
    .map((r) => ({ network: r.querySelector(".c-net").value.trim(), address: r.querySelector(".c-addr").value.trim() }))
    .filter((c) => c.network || c.address);
  const wire = {};
  document.querySelectorAll("[data-wire]").forEach((el) => { wire[el.dataset.wire] = el.value.trim(); });
  if (!window.confirm("Save these payment details? Buyers see them on their next invoice.")) return;
  try {
    await api("PUT", "/api/admin/payment-settings", { crypto, wire });
    msg.textContent = "Saved.";
    loadSettings();
  } catch (err) {
    if (!guard(err)) msg.textContent = err.message;
  }
});

/* ---------- boot ---------- */
(async function boot() {
  const s = session();
  if (!s || !s.token) return showGate();
  try {
    const me = await api("GET", "/api/me");
    if (me.user.role !== "admin") return showGate("This account is not an operator account.");
    showConsole(me.user.username);
    loadPayments();
  } catch (err) {
    if (!guard(err)) showGate(err.message);
  }
})();
