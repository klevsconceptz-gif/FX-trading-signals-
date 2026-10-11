/* klevs FX trading App — landing page client (register / subscribe → access flow). */
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
};

/* ---------- smooth scroll ---------- */
$$("[data-scroll]").forEach((el) =>
  el.addEventListener("click", () => {
    const target = $(el.dataset.scroll);
    if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
  })
);

/* ---------- auth (register / login / forgot password) ---------- */
$$(".plan-btn").forEach((btn) =>
  btn.addEventListener("click", () =>
    window.Auth.open("register", { plan: btn.dataset.plan })
  )
);
$("#btn-login").addEventListener("click", () => window.Auth.open("login"));

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
    window.location.href = "/app";
  } catch (err) {
    alert(err.message);
    btn.disabled = false;
    btn.textContent = "Explore the live demo →";
  }
});

/* already logged in? show a shortcut */
(function () {
  const session = localStorage.getItem("kfx_sub");
  if (session) {
    const btn = $("#btn-login");
    btn.textContent = "Open the bot →";
    btn.classList.remove("ghost");
    btn.classList.add("primary");
    btn.onclick = () => {
      window.location.href = "/app";
    };
  }
})();
