/* klevs FX trading App — landing page client.
   Plan buttons open the account form with the plan preselected; after registration
   the buyer picks a payment method, pays the operator directly and waits for verification. */
"use strict";

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

/* ---------- smooth scroll ---------- */
$$("[data-scroll]").forEach((el) =>
  el.addEventListener("click", () => {
    const target = $(el.dataset.scroll);
    if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
  })
);

/* ---------- account + payment (shared modal in auth.js) ---------- */
$$(".plan-btn").forEach((btn) =>
  btn.addEventListener("click", () => window.Auth.open("register", { plan: btn.dataset.plan }))
);
$("#btn-login").addEventListener("click", () => window.Auth.open("login"));

/* already signed in? the header button opens the app instead */
(async function () {
  const session = await window.Auth.refresh();
  if (!session) return;
  const btn = $("#btn-login");
  const isAdmin = session.role === "admin";
  btn.textContent = isAdmin ? "Open operator console →" : session.access_active ? "Open the bot →" : "Finish payment →";
  btn.classList.remove("ghost");
  btn.classList.add("primary");
  btn.onclick = () => {
    if (isAdmin) window.location.href = "/admin";
    else if (session.access_active) window.location.href = "/app";
    else window.Auth.open("plan");
  };
})();
