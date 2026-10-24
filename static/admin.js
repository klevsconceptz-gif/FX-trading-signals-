/* klevs FX trading App — operator console.
   Only accounts with role=admin can use it; every write is also enforced by the server. */
"use strict";

const SESSION_KEY = "kfx_sub";
const PLANS = ["starter", "pro", "elite"];
const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[c]);
const fmtDate = (ts) => (ts ? new Date(ts * 1000).toLocaleString() : "—");

let operator = null;        // the signed-in admin: { id, username }
let accounts = [];          // last loaded account list
let openAccountId = null;   // account shown in the detail card

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

/* ---------- sign in: the shared sign-in modal (auth.js), operator mode ---------- */
$("#lg-open").addEventListener("click", () => window.Auth.open("login", { operator: true }));

$("#btn-logout").addEventListener("click", async () => {
  try { await api("POST", "/api/auth/logout", {}); } catch (_) { /* already gone */ }
  localStorage.removeItem(SESSION_KEY);
  operator = null;
  showGate("Signed out.");
});

/* every admin call that returns 401/403 goes back to the gate */
function guard(err) {
  if (err.status === 401 || err.status === 403) {
    localStorage.removeItem(SESSION_KEY);
    operator = null;
    showGate(err.status === 401 ? "Session ended. Sign in again." : "Operator account required.");
    return true;
  }
  return false;
}

function setErr(msg) { $("#g-err").textContent = msg || ""; }

/* Runs a write, shows a single error line on failure, then refreshes what is on screen. */
async function write(fn, onError) {
  setErr("");
  try {
    const result = await fn();
    reloadAfterChange();
    return result;
  } catch (err) {
    if (!guard(err)) {
      if (onError) onError(err.message);
      else setErr(err.message);
    }
    return null;
  }
}

/* ---------- tabs ---------- */
const TAB_NAMES = ["payments", "users", "grants", "settings", "activity"];

function showTab(name) {
  document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x.dataset.tab === name));
  TAB_NAMES.forEach((n) => $(`#tab-${n}`).classList.toggle("hidden", n !== name));
  if (name === "payments") loadPayments();
  if (name === "users") loadUsers();
  if (name === "grants") loadGrants();
  if (name === "settings") loadSettings();
  if (name === "activity") loadAuditLog();
}

document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));

function activeTab() {
  return document.querySelector(".tab.active")?.dataset.tab;
}

/* After any write: refresh only what the operator is looking at. */
function reloadAfterChange() {
  const tab = activeTab();
  if (tab === "payments") loadPayments();
  if (tab === "users") {
    loadUsers();
    if (openAccountId) openAccount(openAccountId);
  }
  if (tab === "grants") loadGrants();
}

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
    const open = p.status === "submitted" || p.status === "awaiting_payment";
    const ref = p.reference ? `<code>${esc(p.reference)}</code>` : `<span class="note">not submitted yet</span>`;
    const sender = p.sender_note ? `<div class="note">${esc(p.sender_note)}</div>` : "";
    const decided = p.decision_note ? `<div class="note">Note: ${esc(p.decision_note)}</div>` : "";
    const decision = open ? `
      <div class="row">
        <input data-note-for="${p.id}" placeholder="reason (required to reject or cancel)" maxlength="300" style="min-width:180px" />
        ${p.status === "submitted" ? `<button class="ok" data-act="confirm" data-id="${p.id}">Confirm &amp; grant</button>
        <button class="bad" data-act="reject" data-id="${p.id}">Reject</button>` : ""}
        <button class="bad" data-act="cancel" data-id="${p.id}">Cancel invoice</button>
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

const PAYMENT_PROMPTS = {
  confirm: "Confirm this payment and grant 30 days of access? Only do this after checking the wallet or bank statement.",
  reject: "Reject this payment? The buyer will see the reason and can try again.",
  cancel: "Cancel this open invoice? Nothing is granted and the invoice cannot be paid after this.",
};

$("#tab-payments").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const id = btn.dataset.id;
  const act = btn.dataset.act;
  const note = document.querySelector(`[data-note-for="${id}"]`).value.trim();
  if ((act === "reject" || act === "cancel") && note.length < 3) {
    setErr("A reason of at least 3 characters is required. It is kept in the audit trail.");
    return;
  }
  if (!window.confirm(PAYMENT_PROMPTS[act])) return;
  btn.disabled = true;
  const result = await write(() => api("POST", `/api/admin/payments/${id}/${act}`, { note }));
  if (result === null) btn.disabled = false;
});
$("#pay-filter").addEventListener("change", loadPayments);
$("#pay-refresh").addEventListener("click", loadPayments);

/* ---------- accounts ---------- */
async function loadUsers() {
  setErr("");
  try {
    const data = await api("GET", "/api/admin/users");
    accounts = data.users;
    $("#user-list").innerHTML = accounts.map((u) => `<option value="${esc(u.username)}">`).join("");
    renderAccounts();
  } catch (err) {
    if (!guard(err)) setErr(err.message);
  }
}

function renderAccounts() {
  const q = $("#us-search").value.trim().toLowerCase();
  const list = accounts.filter((u) => !q
    || u.username.toLowerCase().includes(q)
    || (u.email || "").toLowerCase().includes(q)
    || (u.phone || "").toLowerCase().includes(q));
  $("#user-rows").innerHTML = list.map((u) => {
    const a = u.access;
    const access = a.active
      ? `<span class="pill s-confirmed">${esc(a.plan === "admin" ? "operator" : a.plan)}</span>
         ${a.expires_at ? `<div class="note">until ${fmtDate(a.expires_at)}</div>` : ""}`
      : `<span class="pill s-awaiting_payment">no access</span>`;
    const status = u.disabled
      ? `<span class="pill s-rejected">disabled</span>`
      : `<span class="pill s-confirmed">active</span>`;
    const you = operator && u.id === operator.id ? ` <span class="note">(you)</span>` : "";
    return `<tr>
      <td>${esc(u.username)}${you}</td>
      <td>${esc(u.email)}</td>
      <td>${esc(u.phone || "")}</td>
      <td>${esc(u.role)}</td>
      <td>${status}</td>
      <td>${access}</td>
      <td><button data-view="${Number(u.id)}">Open</button></td>
    </tr>`;
  }).join("") || `<tr><td colspan="7" class="note">No accounts match.</td></tr>`;
}

$("#us-search").addEventListener("input", renderAccounts);
$("#us-refresh").addEventListener("click", loadUsers);

async function openAccount(id) {
  setErr("");
  try {
    const data = await api("GET", `/api/admin/users/${Number(id)}`);
    openAccountId = Number(id);
    renderAccountDetail(data);
    $("#acct-detail").classList.remove("hidden");
  } catch (err) {
    if (guard(err)) return;
    if (err.status === 404) {
      // the account was deleted or never existed: close the panel without an error
      openAccountId = null;
      $("#acct-detail").classList.add("hidden");
      return;
    }
    setErr(err.message);
  }
}

function renderAccountDetail({ user, grants, payments }) {
  const self = operator && user.id === operator.id;
  const bootstrap = user.managed_by_env;
  const hasRecords = grants.length > 0 || payments.length > 0;

  $("#acct-title").textContent = `Account · ${user.username} · ${user.role === "admin" ? "operator (admin)" : "user"}`;
  $("#acct-notice").textContent = bootstrap
    ? "This is the bootstrap admin. Its username, email, phone, role and status come from Worker secrets and cannot be changed here. Its password is set by KFX_ADMIN_PASSWORD."
    : self
      ? "This is your own account. You can change your details, but you cannot disable or delete yourself."
      : "";
  $("#ad-username").value = user.username;
  $("#ad-email").value = user.email || "";
  $("#ad-phone").value = user.phone || "";
  $("#ad-note").value = "";

  const locked = bootstrap;
  ["#ad-username", "#ad-email", "#ad-phone"].forEach((sel) => { $(sel).disabled = locked; });
  $("#acct-save").disabled = locked;
  $("#acct-toggle").textContent = user.disabled ? "Enable account" : "Disable account";
  $("#acct-toggle").disabled = locked || self;
  $("#acct-delete").disabled = locked || self || hasRecords;
  $("#acct-delete").title = hasRecords
    ? "This account has payment or access records, which are kept for audit. Disable it instead."
    : "";
  $("#acct-toggle").title = self ? "You cannot disable your own account." : "";

  $("#ad-grants").innerHTML = grants.map((g) => {
    const pill = g.status === "revoked"
      ? `<span class="pill s-rejected">revoked</span>`
      : g.active
        ? `<span class="pill s-confirmed">active</span>`
        : `<span class="pill s-awaiting_payment">expired</span>`;
    const actions = g.status === "active"
      ? `<button data-grant-extend="${g.id}">Set length</button>
         <button class="bad" data-grant-revoke="${g.id}">Revoke</button>` : `<span class="note">—</span>`;
    return `<tr>
      <td>${esc(g.plan)}</td>
      <td>${pill}</td>
      <td>${fmtDate(g.expires_at)}</td>
      <td>${esc(g.granted_by)}</td>
      <td>${esc(g.note || "—")}${g.revoke_note ? `<div class="note">Revoked: ${esc(g.revoke_note)}</div>` : ""}</td>
      <td class="row">${actions}</td>
    </tr>`;
  }).join("") || `<tr><td colspan="6" class="note">No access grants yet.</td></tr>`;

  $("#ad-payments").innerHTML = payments.map((p) => {
    const open = p.status === "submitted" || p.status === "awaiting_payment";
    return `<tr>
      <td><code>${esc(p.invoice)}</code><div class="note">${fmtDate(p.created_at)}</div></td>
      <td>${esc(p.plan_name)}</td>
      <td>$${esc(Number(p.amount_usd).toFixed(2))}</td>
      <td>${esc(p.method === "crypto" ? `crypto · ${p.network}` : "bank wire")}</td>
      <td><span class="pill s-${esc(p.status)}">${esc(p.status.replace("_", " "))}</span></td>
      <td>${open ? `<button class="bad" data-pay-cancel="${p.id}">Cancel invoice</button>` : `<span class="note">—</span>`}</td>
    </tr>`;
  }).join("") || `<tr><td colspan="6" class="note">No invoices yet.</td></tr>`;
}

async function saveAccount() {
  $("#acct-msg").textContent = "";
  const note = $("#ad-note").value.trim();
  if (note.length < 3) { $("#acct-msg").textContent = "A reason of at least 3 characters is required."; return; }
  const body = {
    username: $("#ad-username").value.trim(),
    email: $("#ad-email").value.trim(),
    phone: $("#ad-phone").value.trim(),
    note,
  };
  const ok = await write(() => api("PATCH", `/api/admin/users/${openAccountId}`, body), (m) => { $("#acct-msg").textContent = m; });
  if (ok) $("#acct-msg").textContent = "Saved.";
}

async function toggleAccount() {
  $("#acct-msg").textContent = "";
  const note = $("#ad-note").value.trim();
  if (note.length < 3) { $("#acct-msg").textContent = "A reason of at least 3 characters is required."; return; }
  const disabling = $("#acct-toggle").textContent.startsWith("Disable");
  const prompt = disabling
    ? "Disable this account? It cannot sign in and its current sessions end. Records are kept."
    : "Enable this account so it can sign in again?";
  if (!window.confirm(prompt)) return;
  await write(() => api("PATCH", `/api/admin/users/${openAccountId}`, { disabled: disabling, note }),
    (m) => { $("#acct-msg").textContent = m; });
}

async function deleteAccount() {
  $("#acct-msg").textContent = "";
  const note = $("#ad-note").value.trim();
  if (note.length < 3) { $("#acct-msg").textContent = "A reason of at least 3 characters is required."; return; }
  if (!window.confirm("Permanently delete this account? This cannot be undone. Only accounts with no payment or access records can be deleted.")) return;
  const result = await write(() => api("DELETE", `/api/admin/users/${openAccountId}`, { note }),
    (m) => { $("#acct-msg").textContent = m; });
  if (result) {
    openAccountId = null;
    $("#acct-detail").classList.add("hidden");
  }
}

function promptNote(question) {
  const note = window.prompt(question, "");
  if (note === null) return null;
  if (note.trim().length < 3) { setErr("A reason of at least 3 characters is required. It is kept in the audit trail."); return null; }
  return note.trim();
}

async function extendGrant(id) {
  const days = window.prompt("New length of this grant, in days counted from today (1–366):", "30");
  if (days === null) return;
  const n = Number(days.trim());
  if (!Number.isInteger(n) || n < 1 || n > 366) { setErr("Enter a whole number of days from 1 to 366."); return; }
  const note = promptNote("Reason for changing this grant (required, in the audit trail):");
  if (note === null) return;
  await write(() => api("PATCH", `/api/admin/grants/${id}`, { expires_in_days: n, note }));
}

async function revokeGrant(id) {
  const note = promptNote("Reason for revoking this grant (required, in the audit trail):");
  if (note === null) return;
  if (!window.confirm("Revoke this access grant now?")) return;
  await write(() => api("POST", `/api/admin/grants/${id}/revoke`, { note }));
}

async function cancelInvoice(id) {
  const note = promptNote("Reason for cancelling this invoice (required, in the audit trail):");
  if (note === null) return;
  if (!window.confirm("Cancel this open invoice? Nothing is granted.")) return;
  await write(() => api("POST", `/api/admin/payments/${id}/cancel`, { note }));
}

$("#tab-users").addEventListener("click", async (e) => {
  const view = e.target.closest("button[data-view]");
  if (view) { openAccount(view.dataset.view); return; }
  const grantExtend = e.target.closest("button[data-grant-extend]");
  if (grantExtend) { extendGrant(grantExtend.dataset.grantExtend); return; }
  const grantRevoke = e.target.closest("button[data-grant-revoke]");
  if (grantRevoke) { revokeGrant(grantRevoke.dataset.grantRevoke); return; }
  const payCancel = e.target.closest("button[data-pay-cancel]");
  if (payCancel) { cancelInvoice(payCancel.dataset.payCancel); return; }
});

$("#acct-close").addEventListener("click", () => {
  openAccountId = null;
  $("#acct-detail").classList.add("hidden");
});
$("#acct-save").addEventListener("click", saveAccount);
$("#acct-toggle").addEventListener("click", toggleAccount);
$("#acct-delete").addEventListener("click", deleteAccount);

$("#ad-invoice").addEventListener("click", async () => {
  const msg = $("#ad-invoice-msg");
  msg.textContent = "";
  const note = $("#ad-inv-note").value.trim();
  const method = $("#ad-inv-method").value;
  const network = $("#ad-inv-network").value.trim();
  if (note.length < 3) { msg.textContent = "A reason of at least 3 characters is required."; return; }
  if (method === "crypto" && !network) { msg.textContent = "Enter the network for a crypto invoice."; return; }
  if (!window.confirm("Create this invoice for the account? The buyer sees it the next time they sign in.")) return;
  const result = await write(() => api("POST", "/api/admin/payments", {
    user_id: openAccountId,
    plan: $("#ad-inv-plan").value,
    method,
    network: method === "crypto" ? network : "",
    note,
  }), (m) => { msg.textContent = m; });
  if (result) {
    msg.textContent = `Invoice ${result.payment?.invoice || ""} created.`;
    $("#ad-inv-note").value = "";
  }
});

$("#nu-submit").addEventListener("click", async () => {
  const msg = $("#nu-msg");
  msg.textContent = "";
  const body = {
    username: $("#nu-username").value.trim(),
    email: $("#nu-email").value.trim(),
    phone: $("#nu-phone").value.trim(),
    password: $("#nu-password").value,
    role: "user",
    note: $("#nu-note").value.trim(),
  };
  if (!body.username || !body.email) { msg.textContent = "Enter a username and email."; return; }
  if (!body.password) { msg.textContent = "Enter an initial password."; return; }
  if (body.note.length < 3) { msg.textContent = "A reason of at least 3 characters is required."; return; }
  if (!window.confirm(`Create the user account ${body.username}?`)) return;
  const result = await write(() => api("POST", "/api/admin/users", body), (m) => { msg.textContent = m; });
  if (result) {
    msg.textContent = `Account ${body.username} created.`;
    ["#nu-username", "#nu-email", "#nu-phone", "#nu-password", "#nu-note"].forEach((sel) => { $(sel).value = ""; });
  }
});

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
    reloadAfterChange();
  } catch (err) {
    if (!guard(err)) msg.textContent = err.message;
  }
});

$("#rp-submit").addEventListener("click", async () => {
  const msg = $("#rp-msg");
  msg.textContent = "";
  const ident = $("#rp-user").value.trim();
  const password = $("#rp-password").value;
  const note = $("#rp-note").value.trim();
  if (!ident) { msg.textContent = "Enter the username or email."; return; }
  if (!password) { msg.textContent = "Enter the new password."; return; }
  if (note.length < 3) { msg.textContent = "A reason of at least 3 characters is required."; return; }
  if (!window.confirm(`Reset the password for ${ident}? All of their sessions will end.`)) return;
  try {
    const id = await resolveUserId(ident);
    await api("POST", `/api/admin/users/${id}/reset-password`, { new_password: password, note });
    msg.textContent = `Password reset for ${ident}. They must sign in again.`;
    $("#rp-password").value = "";
    $("#rp-note").value = "";
    reloadAfterChange();
  } catch (err) {
    if (!guard(err)) msg.textContent = err.message;
  }
});

async function resolveUserId(ident) {
  const data = await api("GET", "/api/admin/users");
  const hit = data.users.find((u) => u.username === ident || u.email === ident);
  if (!hit) throw new Error(`No account matches "${ident}".`);
  return hit.id;
}

/* ---------- access grants ---------- */
async function loadGrants() {
  setErr("");
  const status = $("#gr-filter").value;
  try {
    const data = await api("GET", `/api/admin/grants${status ? `?status=${status}` : ""}`);
    renderGrants(data.grants);
  } catch (err) {
    if (!guard(err)) setErr(err.message);
  }
}

function renderGrants(rows) {
  const tbody = $("#grant-rows");
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="7" class="note">No grants in this view.</td></tr>`;
    return;
  }
  tbody.innerHTML = rows.map((g) => {
    const pill = g.status === "revoked"
      ? `<span class="pill s-rejected">revoked</span>`
      : g.active
        ? `<span class="pill s-confirmed">active</span>`
        : `<span class="pill s-awaiting_payment">expired</span>`;
    const actions = g.status === "active"
      ? `<button data-grant-extend="${g.id}">Set length</button>
         <button class="bad" data-grant-revoke="${g.id}">Revoke</button>` : `<span class="note">—</span>`;
    return `<tr>
      <td>${esc(g.username || `user #${g.user_id}`)}</td>
      <td>${esc(g.plan)}</td>
      <td>${pill}</td>
      <td>${fmtDate(g.expires_at)}</td>
      <td>${esc(g.granted_by)}</td>
      <td>${esc(g.note || "—")}${g.revoke_note ? `<div class="note">Revoked: ${esc(g.revoke_note)}</div>` : ""}</td>
      <td class="row">${actions}</td>
    </tr>`;
  }).join("");
}

$("#tab-grants").addEventListener("click", (e) => {
  const extend = e.target.closest("button[data-grant-extend]");
  if (extend) { extendGrant(extend.dataset.grantExtend); return; }
  const revoke = e.target.closest("button[data-grant-revoke]");
  if (revoke) { revokeGrant(revoke.dataset.grantRevoke); return; }
});
$("#gr-filter").addEventListener("change", loadGrants);
$("#grl-refresh").addEventListener("click", loadGrants);

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

const AUDIT_ACTIONS = {
  user_created: "Account created",
  role_changed: "Role changed",
  user_updated: "Account updated",
  user_deleted: "Account deleted",
  password_reset: "Password reset",
  access_granted: "Access granted",
  access_changed: "Grant changed",
  access_revoked: "Access revoked",
  payment_created: "Invoice created",
  payment_cancelled: "Invoice cancelled",
  payment_confirmed: "Payment confirmed",
  payment_rejected: "Payment rejected",
  payment_settings_updated: "Payment settings updated",
};

function renderAuditLog(events) {
  const tbody = $("#audit-rows");
  if (!events.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="note">No admin activity recorded yet.</td></tr>`;
    return;
  }
  tbody.innerHTML = events.map((event) => {
    const when = event.occurred_at == null ? "Time not recorded" : fmtDate(event.occurred_at);
    const legacy = event.legacy ? `<div class="note">Historical record</div>` : "";
    const action = AUDIT_ACTIONS[event.action] || event.action;
    return `<tr>
      <td>${esc(when)}${legacy}</td>
      <td>${esc(event.actor || "—")}</td>
      <td>${esc(action)}<div class="note">${esc(event.summary || "")}</div></td>
      <td>${esc(event.target_username || "—")}</td>
      <td>${esc(event.reference || "—")}</td>
      <td>${esc(event.note || "—")}</td>
    </tr>`;
  }).join("");
}

async function loadAuditLog() {
  setErr("");
  try {
    const data = await api("GET", "/api/admin/audit-log?limit=200");
    renderAuditLog(data.events || []);
  } catch (err) {
    if (!guard(err)) setErr(err.message);
  }
}

$("#audit-refresh").addEventListener("click", loadAuditLog);

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
    operator = { id: Number(me.user.id), username: me.user.username };
    showConsole(me.user.username);
    loadPayments();
  } catch (err) {
    if (!guard(err)) showGate(err.message);
  }
})();
