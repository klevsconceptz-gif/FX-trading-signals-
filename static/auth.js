/* klevs FX trading App — shared auth component (register / login / forgot password).
   Injects one modal into the page; used by the landing page and the app paywall. */
"use strict";

(function () {
  const API = {
    async post(url, payload) {
      const r = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : "request failed");
      return body;
    },
  };

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
    el.className = "auth-modal";
    el.id = "auth-modal";
    el.hidden = true;
    el.innerHTML = `
      <div class="auth-card">
        <button class="auth-close" id="auth-close">✕</button>
        <div class="auth-tabs">
          <button class="auth-tab" data-view="register">Create account</button>
          <button class="auth-tab" data-view="login">Log in</button>
          <button class="auth-tab" data-view="forgot">Forgot password</button>
        </div>

        <!-- REGISTER -->
        <form class="auth-view" id="view-register">
          <h3>Create your account</h3>
          <p class="auth-sub">Username, email, phone number and password — then choose your monthly plan.</p>
          <label class="auth-field"><span class="auth-label">Username</span>
            <input type="text" id="reg-username" required minlength="3" maxlength="32"
                   pattern="[A-Za-z0-9_.\\-]{3,32}" placeholder="fxtrader24"
                   title="3-32 characters: letters, numbers, _ . - only" autocomplete="username" />
          </label>
          <label class="auth-field"><span class="auth-label">Email</span>
            <input type="email" id="reg-email" required maxlength="200"
                   placeholder="you@example.com" autocomplete="email" />
          </label>
          <label class="auth-field"><span class="auth-label">Phone number</span>
            <input type="tel" id="reg-phone" required minlength="7" maxlength="20"
                   placeholder="+1 555 123 4567" autocomplete="tel"
                   pattern="\\+?[0-9][0-9\\s\\-().]{6,19}" />
          </label>
          <div id="reg-password-slot"></div>
          <label class="auth-field"><span class="auth-label">Monthly plan (billed after checkout)</span>
            <select id="reg-plan">
              <option value="starter">Starter — $29/month</option>
              <option value="pro" selected>Pro — $79/month</option>
              <option value="elite">Elite — $199/month</option>
            </select>
          </label>
          <div class="auth-note">Demo checkout — no card is charged. In production this
            connects to secure Stripe Checkout; your account works exactly the same.</div>
          <button class="auth-submit" type="submit" id="reg-submit">Create account &amp; subscribe →</button>
        </form>

        <!-- LOGIN -->
        <form class="auth-view" id="view-login" hidden>
          <h3>Welcome back</h3>
          <p class="auth-sub">Log in with your username, email or phone number.</p>
          <label class="auth-field"><span class="auth-label">Username, email or phone</span>
            <input type="text" id="log-ident" required maxlength="200"
                   placeholder="fxtrader24 / you@example.com / +1 555 123 4567"
                   autocomplete="username" />
          </label>
          <div id="log-password-slot"></div>
          <button class="auth-submit" type="submit" id="log-submit">Log in →</button>
          <button class="auth-link" type="button" data-view="forgot">Forgot your password?</button>
        </form>

        <!-- FORGOT -->
        <form class="auth-view" id="view-forgot" hidden>
          <h3>Reset your password</h3>
          <p class="auth-sub">Enter your account email — we'll send a 7-digit reset code (email auth).
            The code is stored hashed and expires in 15 minutes.</p>
          <label class="auth-field"><span class="auth-label">Account email</span>
            <input type="email" id="fg-email" required maxlength="200" placeholder="you@example.com" />
          </label>
          <button class="auth-submit" type="submit" id="fg-submit">Send reset code →</button>
        </form>

        <!-- RESET (code entry) -->
        <form class="auth-view" id="view-reset" hidden>
          <h3>Enter your reset code</h3>
          <p class="auth-sub">We sent a code to <b id="reset-email-label"></b>.</p>
          <div class="auth-note" id="reset-dev-note" hidden></div>
          <label class="auth-field"><span class="auth-label">7-digit code</span>
            <input type="text" id="rst-code" required minlength="6" maxlength="10"
                   inputmode="numeric" placeholder="0000000" />
          </label>
          <div id="rst-password-slot"></div>
          <button class="auth-submit" type="submit" id="rst-submit">Set new password →</button>
        </form>

        <div class="auth-success" id="auth-success" hidden>
          <div class="auth-check">✓</div>
          <h3 id="auth-success-title">You're in!</h3>
          <p id="auth-success-msg"></p>
          <button class="auth-submit" id="auth-open-app">Open klevs FX trading App →</button>
        </div>
      </div>
    `;
    document.body.appendChild(el);

    // mount password fields
    el.querySelector("#reg-password-slot").replaceWith(
      passwordField("reg-password", "Password")
    );
    el.querySelector("#log-password-slot").replaceWith(
      passwordField("log-password", "Password", { checklist: false })
    );
    el.querySelector("#rst-password-slot").replaceWith(
      passwordField("rst-password", "New password")
    );
    return el;
  }

  const state = { onAuth: null, plan: "pro" };

  function showView(view) {
    const el = document.getElementById("auth-modal");
    ["register", "login", "forgot", "reset", "success"].forEach((v) => {
      const node = el.querySelector(`#view-${v}`) || (v === "success" ? el.querySelector("#auth-success") : null);
      if (node) node.hidden = v !== view;
    });
    el.querySelectorAll(".auth-tab").forEach((t) =>
      t.classList.toggle("active", t.dataset.view === view ||
        (view === "reset" && t.dataset.view === "forgot"))
    );
  }

  function open(view = "register", opts = {}) {
    const el = document.getElementById("auth-modal") || buildModal();
    bindOnce(el);
    el.hidden = false;
    document.body.style.overflow = "hidden";
    if (opts.plan) {
      state.plan = opts.plan;
      const sel = el.querySelector("#reg-plan");
      if (sel) sel.value = opts.plan;
    }
    showView(view);
  }

  function close() {
    const el = document.getElementById("auth-modal");
    if (el) el.hidden = true;
    document.body.style.overflow = "";
  }

  function succeed(data, title) {
    const session = {
      token: data.token,
      name: data.subscription.name || data.subscription.username || "",
      username: data.subscription.username || "",
      email: data.subscription.email,
      phone: data.subscription.phone || "",
      plan: data.subscription.plan,
      plan_name: data.subscription.plan_name,
      price_usd: data.subscription.price_usd,
    };
    localStorage.setItem("kfx_sub", JSON.stringify(session));
    const el = document.getElementById("auth-modal");
    el.querySelector("#auth-success-title").textContent = title || "You're in!";
    el.querySelector("#auth-success-msg").textContent =
      `${session.plan_name} plan · ${session.price_usd === 0 ? "free demo" : "$" + session.price_usd + "/month"} · signed in as ${session.username || session.email}`;
    showView("success");
    if (state.onAuth) state.onAuth(session);
  }

  /* ---------- wiring ---------- */
  let bound = false;
  function bindOnce(el) {
    if (bound) return;
    bound = true;

    el.addEventListener("click", (e) => {
      if (e.target === el) close();
      const tab = e.target.closest("[data-view]");
      if (tab) {
        const v = tab.dataset.view;
        if (["register", "login", "forgot"].includes(v)) showView(v);
      }
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !document.getElementById("auth-modal")?.hidden) close();
    });
    el.querySelector("#auth-close").addEventListener("click", close);
    el.querySelector("#auth-open-app").addEventListener("click", () => {
      window.location.href = "/app";
    });

    /* register */
    el.querySelector("#view-register").addEventListener("submit", async (e) => {
      e.preventDefault();
      const pw = el.querySelector("#reg-password").value;
      if (!policyValid(pw)) {
        alert("Password does not meet the policy: minimum 10 characters with uppercase, lowercase, number and symbol.");
        return;
      }
      const btn = el.querySelector("#reg-submit");
      btn.disabled = true; btn.textContent = "Creating account…";
      try {
        const data = await API.post("/api/register", {
          username: el.querySelector("#reg-username").value.trim(),
          email: el.querySelector("#reg-email").value.trim(),
          phone: el.querySelector("#reg-phone").value.trim(),
          password: pw,
          plan: el.querySelector("#reg-plan").value,
        });
        succeed(data, "Account created — subscription active");
      } catch (err) {
        alert(err.message);
      } finally {
        btn.disabled = false; btn.textContent = "Create account & subscribe →";
      }
    });

    /* login */
    el.querySelector("#view-login").addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = el.querySelector("#log-submit");
      btn.disabled = true; btn.textContent = "Logging in…";
      try {
        const data = await API.post("/api/auth/login", {
          identifier: el.querySelector("#log-ident").value.trim(),
          password: el.querySelector("#log-password").value,
        });
        succeed(data, "Welcome back");
      } catch (err) {
        alert(err.message);
      } finally {
        btn.disabled = false; btn.textContent = "Log in →";
      }
    });

    /* forgot -> request code */
    el.querySelector("#view-forgot").addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = el.querySelector("#fg-submit");
      const email = el.querySelector("#fg-email").value.trim();
      btn.disabled = true; btn.textContent = "Sending…";
      try {
        const data = await API.post("/api/auth/forgot", { email });
        el.querySelector("#reset-email-label").textContent = email;
        const note = el.querySelector("#reset-dev-note");
        if (data.dev_code) {
          note.hidden = false;
          note.innerHTML = `<b>Demo build</b> — in production this code is emailed to you only.
            Dev code: <b style="letter-spacing:2px">${data.dev_code}</b>`;
        }
        showView("reset");
      } catch (err) {
        alert(err.message);
      } finally {
        btn.disabled = false; btn.textContent = "Send reset code →";
      }
    });

    /* reset -> new password */
    el.querySelector("#view-reset").addEventListener("submit", async (e) => {
      e.preventDefault();
      const pw = el.querySelector("#rst-password").value;
      if (!policyValid(pw)) {
        alert("New password does not meet the policy: minimum 10 characters with uppercase, lowercase, number and symbol.");
        return;
      }
      const btn = el.querySelector("#rst-submit");
      btn.disabled = true; btn.textContent = "Updating…";
      try {
        const data = await API.post("/api/auth/reset", {
          email: el.querySelector("#fg-email").value.trim(),
          code: el.querySelector("#rst-code").value.trim(),
          new_password: pw,
        });
        succeed(data, "Password updated");
      } catch (err) {
        alert(err.message);
      } finally {
        btn.disabled = false; btn.textContent = "Set new password →";
      }
    });
  }

  window.Auth = {
    open,
    close,
    onAuth(cb) { state.onAuth = cb; },
    policy: POLICY,
  };
})();
