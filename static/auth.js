/* klevs FX trading App — shared auth + payment component.
   Injects one modal into the page; used by the landing page, the app paywall and
   the operator console.  Flow: create account -> choose plan -> pay by crypto or wire
   -> submit the transaction hash / wire reference -> operator verifies -> access.
   No card is taken; nothing is charged by this page. */
"use strict";

(function () {
  // Page routes: /app, /admin and / on the web, bundled files inside the Android and iOS shells.
  function appRoute(name) {
    if (window.KLEVS) return window.KLEVS.route(name);
    return { home: "/", app: "/app", admin: "/admin" }[name] || "/";
  }
  const KEY = "kfx_sub";

  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);

  function authHeaders() {
    const s = readSession();
    return s && s.token ? { Authorization: `Bearer ${s.token}` } : {};
  }

  const API = {
    async send(method, url, payload) {
      const opts = { method, headers: { ...authHeaders() } };
      if (payload !== undefined) {
        opts.headers["Content-Type"] = "application/json";
        opts.body = JSON.stringify(payload);
      }
      const r = await fetch(url, opts);
      const body = await r.json().catch(() => ({}));
      if (!r.ok) {
        const err = new Error(typeof body.detail === "string" ? body.detail : "request failed");
        err.status = r.status;
        throw err;
      }
      return body;
    },
    post(url, payload) { return this.send("POST", url, payload || {}); },
    put(url, payload) { return this.send("PUT", url, payload); },
    get(url) { return this.send("GET", url); },
  };

  function readSession() {
    try { return JSON.parse(localStorage.getItem(KEY) || "null"); } catch (_) { return null; }
  }

  function writeSession(s) {
    if (s) localStorage.setItem(KEY, JSON.stringify(s));
    else localStorage.removeItem(KEY);
  }

  /* stores the account + access state returned by /api/me or a login/register response */
  function saveFromPayload(data) {
    const user = data.user || {};
    const access = data.access || {};
    const prev = readSession() || {};
    const s = {
      token: data.token || prev.token,
      username: user.username || prev.username || "",
      email: user.email || prev.email || "",
      phone: user.phone || prev.phone || "",
      role: user.role || prev.role || "user",
      access_active: !!access.active,
      plan: access.plan || null,
      plan_name: access.plan_name || null,
      expires_at: access.expires_at || null,
      access: access.access || null,
    };
    writeSession(s);
    return s;
  }

  const POLICY = [
    { key: "length_10", label: "At least 10 characters", test: (p) => p.length >= 10 },
    { key: "uppercase", label: "Uppercase letter (A-Z)", test: (p) => /[A-Z]/.test(p) },
    { key: "lowercase", label: "Lowercase letter (a-z)", test: (p) => /[a-z]/.test(p) },
    { key: "number", label: "Number (0-9)", test: (p) => /[0-9]/.test(p) },
    { key: "symbol", label: "Symbol (!@#$%…)", test: (p) => /[^A-Za-z0-9]/.test(p) },
  ];

  function strengthOf(pw) {
    const passed = POLICY.filter((r) => r.test(pw)).length;
    if (!pw) return { label: "—", cls: "", passed };
    if (passed <= 2) return { label: "Weak", cls: "st-weak", passed };
    if (passed === 3) return { label: "Fair", cls: "st-fair", passed };
    if (passed === 4) return { label: "Strong", cls: "st-strong", passed };
    return { label: "Very strong", cls: "st-vstrong", passed };
  }

  /* ---------- password field factory ---------- */
  function passwordField(id, label, { checklist = true } = {}) {
    const wrap = document.createElement("label");
    wrap.className = "auth-field";
    wrap.innerHTML = `
      <span class="auth-label">${label}</span>
      <div class="pw-row">
        <input type="password" id="${id}" autocomplete="new-password" maxlength="128" required />
        <button type="button" class="pw-toggle" data-for="${id}" tabindex="-1" title="Show password">👁</button>
      </div>
      ${checklist ? `
      <div class="pw-meter"><i id="${id}-bar"></i></div>
      <div class="pw-strength" id="${id}-strength">Strength: —</div>
      <ul class="pw-checklist" id="${id}-checks">
        ${POLICY.map((r) => `<li data-k="${r.key}"><span class="tick">○</span> ${r.label}</li>`).join("")}
      </ul>` : ""}
    `;
    // live meter + checklist
    const input = wrap.querySelector(`#${CSS.escape(id)}`);
    const refresh = () => {
      const v = input.value;
      const s = strengthOf(v);
      if (checklist) {
        const bar = wrap.querySelector(`#${CSS.escape(id)}-bar`);
        const txt = wrap.querySelector(`#${CSS.escape(id)}-strength`);
        bar.style.width = `${(s.passed / POLICY.length) * 100}%`;
        bar.className = s.cls;
        txt.textContent = `Strength: ${s.label}`;
        txt.className = `pw-strength ${s.cls}`;
        wrap.querySelectorAll(".pw-checklist li").forEach((li) => {
          const rule = POLICY.find((r) => r.key === li.dataset.k);
          const ok = rule.test(v);
          li.classList.toggle("ok", ok);
          li.querySelector(".tick").textContent = ok ? "✓" : "○";
        });
      }
    };
    input.addEventListener("input", refresh);
    return wrap;
  }

  /* show/hide toggles are delegated globally */
  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".pw-toggle");
    if (!btn) return;
    const input = document.getElementById(btn.dataset.for);
    const showing = input.type === "text";
    input.type = showing ? "password" : "text";
    btn.textContent = showing ? "👁" : "🙈";
    btn.title = showing ? "Show password" : "Hide password";
    input.focus();
  });

  function policyValid(pw) {
    return POLICY.every((r) => r.test(pw));
  }

  /* ---------- modal shell ---------- */
  function buildModal() {
    const el = document.createElement("div");
    el.id = "auth-modal";
    el.className = "auth-modal";
    el.hidden = true;
    el.innerHTML = `
      <div class="auth-card" role="dialog" aria-modal="true" aria-labelledby="auth-title">
        <button class="auth-close" id="auth-close" aria-label="Close">×</button>
        <div class="auth-tabs" id="auth-tabs">
          <button class="auth-tab" data-view="register">Create account</button>
          <button class="auth-tab" data-view="login">Log in</button>
          <button class="auth-tab" data-view="forgot">Forgot password</button>
        </div>

        <form class="auth-view" id="view-register" hidden>
          <h3 id="auth-title">Create your account</h3>
          <p class="auth-sub">Username, email, phone number and password. Access to the bot is unlocked after you pay and the operator verifies the payment.</p>
          <label class="auth-field"><span class="auth-label">Username</span>
            <input type="text" id="reg-username" required minlength="3" maxlength="32"
                   pattern="[A-Za-z0-9_.\\-]{3,32}" placeholder="fxtrader24" autocomplete="username" />
          </label>
          <label class="auth-field"><span class="auth-label">Email</span>
            <input type="email" id="reg-email" required maxlength="200" placeholder="you@example.com" autocomplete="email" />
          </label>
          <label class="auth-field"><span class="auth-label">Phone number</span>
            <input type="tel" id="reg-phone" required maxlength="20" placeholder="+1 555 123 4567" autocomplete="tel" />
          </label>
          <div id="reg-password-slot"></div>
          <button class="auth-submit" type="submit" id="reg-submit">Create account →</button>
        </form>

        <form class="auth-view" id="view-login" hidden>
          <h3>Welcome back</h3>
          <p class="auth-sub">Log in with your username, email or phone number. Operator accounts can sign in here too and continue to the admin console.</p>
          <label class="auth-field"><span class="auth-label">Username, email or phone</span>
            <input type="text" id="log-ident" required maxlength="200"
                   placeholder="fxtrader24 / you@example.com / +1 555 123 4567" autocomplete="username" />
          </label>
          <div id="log-password-slot"></div>
          <button class="auth-submit" type="submit" id="log-submit">Log in →</button>
          <button class="auth-link" type="button" data-view="forgot">Forgot your password?</button>
        </form>

        <form class="auth-view" id="view-forgot" hidden>
          <h3>Reset your password</h3>
          <p class="auth-sub">We e-mail a 7-digit code to the address on your account. It expires in 15 minutes.</p>
          <label class="auth-field"><span class="auth-label">Email</span>
            <input type="email" id="fg-email" required maxlength="200" placeholder="you@example.com" />
          </label>
          <button class="auth-submit" type="submit" id="fg-submit">Send reset code →</button>
        </form>

        <form class="auth-view" id="view-reset" hidden>
          <h3>Enter your reset code</h3>
          <p class="auth-sub">Code sent to <b id="reset-email-label"></b>.</p>
          <label class="auth-field"><span class="auth-label">7-digit code</span>
            <input type="text" id="rst-code" required inputmode="numeric" maxlength="10" autocomplete="one-time-code" />
          </label>
          <div id="rst-password-slot"></div>
          <button class="auth-submit" type="submit" id="rst-submit">Set new password →</button>
        </form>

        <section class="auth-view" id="view-plan" hidden>
          <h3>Choose your plan</h3>
          <p class="auth-sub">Pay with crypto or a direct bank transfer. Nothing is charged by this site: you pay the operator's wallet or account directly, then the operator unlocks your access.</p>
          <div class="pay-plans" id="pay-plans"></div>
          <div class="pay-methods" id="pay-methods"></div>
          <div class="auth-note" id="pay-unavailable" hidden></div>
          <button class="auth-submit" type="button" id="pay-create">Create invoice →</button>
        </section>

        <section class="auth-view" id="view-invoice" hidden>
          <h3>Pay invoice <span id="inv-code" class="inv-code"></span></h3>
          <p class="auth-sub" id="inv-summary"></p>
          <div class="pay-box" id="inv-payto"></div>
          <p class="auth-note" id="inv-instruction"></p>
          <form id="inv-submit-form">
            <label class="auth-field"><span class="auth-label" id="inv-ref-label">Transaction hash</span>
              <input type="text" id="inv-ref" required minlength="4" maxlength="200" autocomplete="off" />
            </label>
            <label class="auth-field"><span class="auth-label">Note for the operator (optional)</span>
              <input type="text" id="inv-note" maxlength="300" />
            </label>
            <button class="auth-submit" type="submit" id="inv-submit">I have paid — submit for verification →</button>
          </form>
          <button class="auth-link" type="button" id="inv-back">Choose another method</button>
        </section>

        <section class="auth-view" id="view-status" hidden>
          <h3 id="status-title">Waiting for verification</h3>
          <p class="auth-sub" id="status-msg"></p>
          <button class="auth-submit" type="button" id="status-refresh">Check status</button>
          <button class="auth-link" type="button" id="status-new">Start a new payment</button>
        </section>

        <section class="auth-view" id="view-success" hidden>
          <h3 id="auth-success-title">You're in!</h3>
          <p class="auth-sub" id="auth-success-msg"></p>
          <button class="auth-submit" type="button" id="auth-open-app">Open the bot →</button>
        </section>
      </div>`;
    document.body.appendChild(el);

    el.querySelector("#reg-password-slot").appendChild(passwordField("reg-password", "Password"));
    el.querySelector("#log-password-slot").appendChild(passwordField("log-password", "Password", { checklist: false }));
    el.querySelector("#rst-password-slot").appendChild(passwordField("rst-password", "New password"));
    return el;
  }

  const state = { onAuth: null, plan: "pro", methods: null, plans: null, invoice: null, poll: null };

  function $(el, sel) { return el.querySelector(sel); }

  function modal() {
    let el = document.getElementById("auth-modal");
    if (!el) el = buildModal();
    return el;
  }

  const VIEWS = ["register", "login", "forgot", "reset", "plan", "invoice", "status", "success"];

  function showView(view) {
    const el = modal();
    VIEWS.forEach((v) => {
      const node = el.querySelector(`#view-${v}`);
      if (node) node.hidden = v !== view;
    });
    $(el, "#auth-tabs").hidden = !["register", "login", "forgot"].includes(view);
    el.querySelectorAll(".auth-tab").forEach((t) => t.classList.toggle("active", t.dataset.view === view));
    stopPolling();
  }

  function open(view = "register", opts = {}) {
    const el = modal();
    if (opts.plan) state.plan = opts.plan;
    state.operatorOnly = !!opts.operator;   // set by the operator console: only admin accounts may continue
    el.hidden = false;
    document.body.style.overflow = "hidden";
    showView(view);
    if (view === "plan") loadPlanView();
  }

  function close() {
    const el = document.getElementById("auth-modal");
    if (el) el.hidden = true;
    document.body.style.overflow = "";
    stopPolling();
  }

  function stopPolling() {
    if (state.poll) { clearInterval(state.poll); state.poll = null; }
  }

  function alertBox(el, view, msg) {
    // inline error line inside the active view (no browser alert())
    let box = el.querySelector(`#view-${view} .auth-error`);
    if (!box) {
      box = document.createElement("div");
      box.className = "auth-error";
      el.querySelector(`#view-${view}`).prepend(box);
    }
    box.textContent = msg || "";
    box.hidden = !msg;
  }

  /* After auth, admins go straight to the operator console; subscribers use the payment/access flow. */
  async function routeAfterAuth(payload, title) {
    const s = saveFromPayload(payload);
    if (state.operatorOnly && s.role !== "admin") {
      writeSession(null);   // a customer account must not stay signed in to the operator console
      showView("login");
      alertBox(modal(), "login", "This account is not an operator account.");
      return;
    }
    if (s.role === "admin") {
      window.location.href = appRoute("admin");
      return;
    }
    if (s.access_active) return finishSuccess(s, title || "You're in!");
    const me = await API.get("/api/me");
    saveFromPayload(me);
    const latest = (me.payments || [])[0];
    if (latest && latest.status === "awaiting_payment") return showInvoice(latest);
    if (latest && latest.status === "submitted") return showStatus(latest);
    return open("plan");
  }

  function finishSuccess(s, title) {
    const el = modal();
    el.querySelector("#auth-success-title").textContent = title;
    const until = s.expires_at ? ` · access until ${new Date(s.expires_at * 1000).toLocaleDateString()}` : "";
    const isAdmin = s.role === "admin";
    el.querySelector("#auth-success-msg").textContent = isAdmin
      ? `Operator account · unrestricted access · signed in as ${s.username || s.email}`
      : `${s.plan_name || "Paid"} plan${until} · signed in as ${s.username || s.email}`;
    $(el, "#auth-open-app").textContent = isAdmin ? "Open operator console →" : "Open the bot →";
    showView("success");
    if (state.onAuth) state.onAuth(s);
  }

  /* ---------- payment: plan + method ---------- */
  async function loadPlanView() {
    const el = modal();
    const box = $(el, "#pay-plans");
    box.innerHTML = "<p class=\"auth-sub\">Loading plans…</p>";
    $(el, "#pay-methods").innerHTML = "<p class=\"auth-sub\">Loading payment methods…</p>";
    $(el, "#pay-create").disabled = true;      // no invoice until the methods are known
    try {
      const opts = await API.get("/api/payment-options");
      state.plans = opts.plans;
      state.methods = opts.methods;
    } catch (err) {
      box.textContent = err.message;
      return;
    }
    const plans = state.plans;
    if (!plans[state.plan]) state.plan = "pro";
    box.innerHTML = Object.entries(plans).map(([key, p]) => `
      <label class="pay-opt ${state.plan === key ? "selected" : ""}">
        <input type="radio" name="pay-plan" value="${esc(key)}" ${state.plan === key ? "checked" : ""} />
        <b>${esc(p.name)}</b> <span>$${esc(p.price_usd)} / ${esc(p.days)} days</span>
      </label>`).join("");

    const m = state.methods;
    const cryptoOk = m.crypto.available, wireOk = m.wire.available;
    const netOptions = (m.crypto.networks || []).map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join("");
    $(el, "#pay-methods").innerHTML = `
      <label class="pay-opt ${cryptoOk ? "" : "disabled"}">
        <input type="radio" name="pay-method" value="crypto" ${cryptoOk ? "checked" : "disabled"} />
        <b>Crypto</b> <span>${cryptoOk ? "" : "not set up yet"}</span>
      </label>
      <label class="auth-field ${cryptoOk ? "" : "hidden"}" id="pay-network-wrap">
        <span class="auth-label">Network</span>
        <select id="pay-network">${netOptions}</select>
      </label>
      <label class="pay-opt ${wireOk ? "" : "disabled"}">
        <input type="radio" name="pay-method" value="wire" ${!cryptoOk && wireOk ? "checked" : ""} ${wireOk ? "" : "disabled"} />
        <b>Bank wire</b> <span>${wireOk ? "" : "not set up yet"}</span>
      </label>`;
    const none = !cryptoOk && !wireOk;
    const note = $(el, "#pay-unavailable");
    note.hidden = !none;
    note.textContent = "Payments are not set up yet — the operator has not added a wallet or bank details. Check back shortly.";
    $(el, "#pay-create").disabled = none;
    syncNetworkVisibility();
  }

  function syncNetworkVisibility() {
    const el = modal();
    const method = el.querySelector("input[name=pay-method]:checked");
    const wrap = $(el, "#pay-network-wrap");
    if (wrap) wrap.hidden = !method || method.value !== "crypto";
  }

  async function createInvoice() {
    const el = modal();
    const plan = el.querySelector("input[name=pay-plan]:checked")?.value || state.plan;
    const method = el.querySelector("input[name=pay-method]:checked")?.value;
    const network = $(el, "#pay-network")?.value;
    if (!method) return alertBox(el, "plan", "Choose a payment method.");
    const btn = $(el, "#pay-create");
    btn.disabled = true; btn.textContent = "Creating invoice…";
    try {
      const data = await API.post("/api/payments", { plan, method, network: method === "crypto" ? network : undefined });
      showInvoice(data.payment);
    } catch (err) {
      alertBox(el, "plan", err.message);
    } finally {
      btn.disabled = false; btn.textContent = "Create invoice →";
    }
  }

  /* ---------- payment: invoice + reference ---------- */
  function payToHtml(p) {
    const pt = p.pay_to || {};
    if (p.method === "crypto") {
      return `<div class="pay-line"><span>Network</span><b>${esc(pt.network)}</b></div>
              <div class="pay-line"><span>Send to</span><code class="pay-addr">${esc(pt.address)}</code></div>`;
    }
    const labels = { bank_name: "Bank", account_name: "Account name", account_number: "Account number",
                     routing_or_swift: "Routing / SWIFT", iban: "IBAN" };
    return Object.entries(labels).filter(([k]) => pt[k])
      .map(([k, label]) => `<div class="pay-line"><span>${label}</span><code>${esc(pt[k])}</code></div>`).join("")
      + `<div class="pay-line"><span>Payment reference</span><code class="inv-code">${esc(p.invoice)}</code></div>`;
  }

  function showInvoice(p) {
    const el = modal();
    state.invoice = p;
    el.querySelector("#inv-code").textContent = p.invoice;
    $(el, "#inv-summary").textContent =
      `${p.plan_name} plan · $${p.amount_usd} for 30 days · ${p.method === "crypto" ? `crypto (${p.network})` : "bank wire"}`;
    $(el, "#inv-payto").innerHTML = payToHtml(p);
    $(el, "#inv-instruction").textContent = p.payment_instruction;
    $(el, "#inv-ref-label").textContent = p.method === "crypto" ? "Transaction hash" : "Wire reference / transfer ID";
    $(el, "#inv-ref").value = "";
    showView("invoice");
    $(el, "#inv-ref").focus();
  }

  async function submitReference(e) {
    e.preventDefault();
    const el = modal();
    const p = state.invoice;
    const btn = $(el, "#inv-submit");
    btn.disabled = true; btn.textContent = "Submitting…";
    try {
      const data = await API.post(`/api/payments/${p.id}/reference`, {
        reference: $(el, "#inv-ref").value.trim(),
        sender_note: $(el, "#inv-note").value.trim(),
      });
      showStatus(data.payment);
    } catch (err) {
      alertBox(el, "invoice", err.message);
    } finally {
      btn.disabled = false; btn.textContent = "I have paid — submit for verification →";
    }
  }

  function showStatus(p) {
    const el = modal();
    state.invoice = p;
    $(el, "#status-title").textContent = "Waiting for verification";
    $(el, "#status-msg").textContent =
      `Invoice ${p.invoice} (${p.plan_name}, $${p.amount_usd}) is with the operator. ` +
      "Your access unlocks as soon as the payment is confirmed. This page checks automatically.";
    showView("status");
    stopPolling();
    state.poll = setInterval(checkStatus, 20000);
  }

  async function checkStatus() {
    const el = modal();
    try {
      const me = await API.get("/api/me");
      const s = saveFromPayload(me);
      if (s.access_active) {
        stopPolling();
        return finishSuccess(s, "Payment verified — access unlocked");
      }
      const latest = (me.payments || [])[0];
      if (latest && latest.status === "rejected") {
        stopPolling();
        $(el, "#status-title").textContent = "Payment not verified";
        $(el, "#status-msg").textContent =
          `The operator could not verify invoice ${latest.invoice}: ${latest.decision_note || "no reason given"}. ` +
          "You can start a new payment.";
      } else if (latest && latest.status === "confirmed") {
        // confirmed but access has not synced yet — refresh again on the next tick
        $(el, "#status-msg").textContent = "Payment confirmed. Unlocking your access…";
      }
    } catch (err) {
      alertBox(el, "status", err.message);
    }
  }

  /* ---------- wiring ---------- */
  let bound = false;
  function bindOnce(el) {
    if (bound) return;
    bound = true;

    el.addEventListener("click", (e) => {
      if (e.target === el) close();
      const tab = e.target.closest("[data-view]");
      if (tab && !tab.disabled) {
        const v = tab.dataset.view;
        if (["register", "login", "forgot"].includes(v)) showView(v);
      }
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !document.getElementById("auth-modal")?.hidden) close();
    });
    $(el, "#auth-close").addEventListener("click", close);
    $(el, "#auth-open-app").addEventListener("click", () => {
      window.location.href = readSession()?.role === "admin" ? appRoute("admin") : appRoute("app");
    });

    /* plan + method controls */
    el.addEventListener("change", (e) => {
      if (e.target.name === "pay-method") syncNetworkVisibility();
      if (e.target.name === "pay-plan") state.plan = e.target.value;
    });
    $(el, "#pay-create").addEventListener("click", createInvoice);
    $(el, "#inv-submit-form").addEventListener("submit", submitReference);
    $(el, "#inv-back").addEventListener("click", () => open("plan"));
    $(el, "#status-new").addEventListener("click", () => open("plan"));
    $(el, "#status-refresh").addEventListener("click", checkStatus);

    /* register */
    $(el, "#view-register").addEventListener("submit", async (e) => {
      e.preventDefault();
      const pw = $(el, "#reg-password").value;
      if (!policyValid(pw)) return alertBox(el, "register", "Password does not meet the policy: at least 10 characters with uppercase, lowercase, a number and a symbol.");
      const btn = $(el, "#reg-submit");
      btn.disabled = true; btn.textContent = "Creating account…";
      try {
        const data = await API.post("/api/register", {
          username: $(el, "#reg-username").value.trim(),
          email: $(el, "#reg-email").value.trim(),
          phone: $(el, "#reg-phone").value.trim(),
          password: pw,
        });
        await routeAfterAuth(data, "Account created");
      } catch (err) {
        alertBox(el, "register", err.message);
      } finally {
        btn.disabled = false; btn.textContent = "Create account →";
      }
    });

    /* login */
    $(el, "#view-login").addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = $(el, "#log-submit");
      btn.disabled = true; btn.textContent = "Logging in…";
      try {
        const data = await API.post("/api/auth/login", {
          identifier: $(el, "#log-ident").value.trim(),
          password: $(el, "#log-password").value,
        });
        await routeAfterAuth(data, "Welcome back");
      } catch (err) {
        alertBox(el, "login", err.message);
      } finally {
        btn.disabled = false; btn.textContent = "Log in →";
      }
    });

    /* forgot -> e-mail a code */
    $(el, "#view-forgot").addEventListener("submit", async (e) => {
      e.preventDefault();
      const email = $(el, "#fg-email").value.trim();
      const btn = $(el, "#fg-submit");
      btn.disabled = true; btn.textContent = "Sending…";
      try {
        await API.post("/api/auth/forgot", { email });
        $(el, "#reset-email-label").textContent = email;
        showView("reset");
      } catch (err) {
        alertBox(el, "forgot", err.message);
      } finally {
        btn.disabled = false; btn.textContent = "Send reset code →";
      }
    });

    /* reset -> new password -> logged in */
    $(el, "#view-reset").addEventListener("submit", async (e) => {
      e.preventDefault();
      const pw = $(el, "#rst-password").value;
      if (!policyValid(pw)) return alertBox(el, "reset", "New password does not meet the policy.");
      const btn = $(el, "#rst-submit");
      btn.disabled = true; btn.textContent = "Updating…";
      try {
        const data = await API.post("/api/auth/reset", {
          email: $(el, "#reset-email-label").textContent,
          code: $(el, "#rst-code").value.trim(),
          new_password: pw,
        });
        await routeAfterAuth(data, "Password updated");
      } catch (err) {
        alertBox(el, "reset", err.message);
      } finally {
        btn.disabled = false; btn.textContent = "Set new password →";
      }
    });
  }

  /* ---------- public API ---------- */
  async function refresh() {
    const s = readSession();
    if (!s || !s.token) return null;
    try {
      const me = await API.get("/api/me");
      return saveFromPayload(me);
    } catch (err) {
      if (err.status === 401) writeSession(null);
      return null;
    }
  }

  async function logout() {
    try { await API.post("/api/auth/logout", {}); } catch (_) { /* already gone */ }
    writeSession(null);
    window.location.href = appRoute("home");
  }

  window.Auth = {
    open,
    close,
    logout,
    refresh,
    session: readSession,
    headers: authHeaders,
    api: API,
    onAuth(cb) { state.onAuth = cb; },
    policy: POLICY,
    ensure() { bindOnce(modal()); },
  };

  /* bind as soon as the DOM exists so buttons work immediately */
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => bindOnce(modal()));
  } else {
    bindOnce(modal());
  }

  /* policy check used by the forms */
  function policyValid(pw) {
    return POLICY.every((r) => r.test(pw));
  }
})();
