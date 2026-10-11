/* klevs FX trading App — landing page client (subscribe → access flow). */
"use strict";

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const API = {
  async subscribe(payload) {
    const r = await fetch("/api/subscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.detail || "subscription failed");
    return body;
  },
  async login(email) {
    const r = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.detail || "login failed");
    return body;
  },
};

/* ---------- smooth scroll ---------- */
$$("[data-scroll]").forEach((el) =>
  el.addEventListener("click", () => {
    const target = $(el.dataset.scroll);
    if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
  })
);

/* ---------- modal ---------- */
const modal = $("#modal");
const subForm = $("#sub-form");
const loginView = $("#login-view");
const successView = $("#success-view");

function openModal(view = "subscribe", plan = null) {
  modal.hidden = false;
  document.body.style.overflow = "hidden";
  subForm.hidden = view !== "subscribe";
  loginView.hidden = view !== "login";
  successView.hidden = view !== "success";
  if (plan) $("#f-plan").value = plan;
  $("#modal-title").textContent =
    view === "login" ? "Welcome back" : "Start your monthly subscription";
  $("#modal-sub").textContent =
    view === "login"
      ? "Open the bot with your subscribed email"
      : "Instant access · cancel anytime · billed monthly";
}

function closeModal() {
  modal.hidden = true;
  document.body.style.overflow = "";
}

$("#modal-close").addEventListener("click", closeModal);
modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !modal.hidden) closeModal(); });

$$(".plan-btn").forEach((btn) =>
  btn.addEventListener("click", () => openModal("subscribe", btn.dataset.plan))
);
$("#btn-login").addEventListener("click", () => openModal("login"));

/* ---------- subscribe ---------- */
function saveSession(data) {
  localStorage.setItem(
    "kfx_sub",
    JSON.stringify({
      token: data.token,
      email: data.subscription.email,
      name: data.subscription.name,
      plan: data.subscription.plan,
      plan_name: data.subscription.plan_name,
      price_usd: data.subscription.price_usd,
    })
  );
}

subForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("#sub-submit");
  btn.disabled = true;
  btn.textContent = "Processing…";
  try {
    const data = await API.subscribe({
      name: $("#f-name").value.trim(),
      email: $("#f-email").value.trim(),
      plan: $("#f-plan").value,
    });
    saveSession(data);
    subForm.hidden = true;
    loginView.hidden = true;
    successView.hidden = false;
    $("#modal-title").textContent = "Subscription active";
    $("#modal-sub").textContent = "";
    $("#success-msg").textContent =
      `${data.subscription.plan_name} plan · ${data.subscription.price_usd === 0 ? "free demo" : "$" + data.subscription.price_usd + "/month"} · billed monthly, cancel anytime.`;
  } catch (err) {
    alert(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Subscribe & open the bot →";
  }
});

/* ---------- login ---------- */
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const data = await API.login($("#l-email").value.trim());
    saveSession(data);
    window.location.href = data.access_url || "/app";
  } catch (err) {
    alert(err.message + "\n\nUse the subscribe form to create your account first.");
  }
});

/* ---------- demo / open app ---------- */
$("#btn-demo").addEventListener("click", async () => {
  const btn = $("#btn-demo");
  btn.disabled = true;
  btn.textContent = "Preparing demo…";
  try {
    const data = await API.subscribe({
      name: "Demo Trader",
      email: `demo-${Math.random().toString(36).slice(2, 8)}@klevsfx.demo`,
      plan: "demo",
    });
    saveSession(data);
    window.location.href = "/app";
  } catch (err) {
    alert(err.message);
    btn.disabled = false;
    btn.textContent = "Explore the live demo →";
  }
});

$("#btn-open-app").addEventListener("click", () => {
  window.location.href = "/app";
});

/* already subscribed? show a shortcut */
(function () {
  const session = localStorage.getItem("kfx_sub");
  if (session) {
    const btn = $("#btn-login");
    btn.textContent = "Open the bot →";
    btn.classList.remove("ghost");
    btn.classList.add("primary");
    btn.onclick = () => { window.location.href = "/app"; };
  }
})();
