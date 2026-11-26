/* klevs FX trading App — dashboard client.
   Vanilla JS + canvas charts (zero external dependencies). */
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

let SCAN = null;          // last scan report
let PAIR = null;          // last pair detail
let scanInProgress = false; // avoid overlapping manual/automatic refresh requests
let scanFailed = false;     // the last scan request did not reach the server
let sortKey = "confidence";
let sortDir = -1;

/* ---------------- helpers ---------------- */

const fmt = (v, d = 5) =>
  v === null || v === undefined || Number.isNaN(v)
    ? "—"
    : Number(v).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: Math.min(d, 4) });

const pct = (v, d = 2) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(d)}%`);
// Confidence as a bar with its percentage. Values outside 0..1 are clamped so the bar stays in its track.
const confCell = (v) => {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  const w = Math.max(0, Math.min(1, v)) * 100;
  return `<div class="conf-cell"><div class="conf-bar" role="img" aria-label="Confidence ${pct(v, 0)}"><i style="width:${w.toFixed(0)}%"></i></div><span>${pct(v, 0)}</span></div>`;
};

function sideClass(side) {
  return side === "BUY" ? "buy" : side === "SELL" ? "sell" : "wait";
}

/* ---------------- tabs ---------------- */

$$(".tab").forEach((btn) =>
  btn.addEventListener("click", () => {
    $$(".tab").forEach((b) => b.classList.remove("active"));
    $$(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $(`#tab-${btn.dataset.tab}`).classList.add("active");
  })
);

/* ---------------- instruction feed (buy → predict → sell) ---------------- */

function renderInstructions(list) {
  const el = $("#instructions");
  $("#instruction-time").textContent = SCAN
    ? `· generated ${new Date(SCAN.generated_at * 1000).toLocaleTimeString()}`
    : "";
  if (!list.length) {
    el.innerHTML = `<div class="empty">No instructions yet — scan in progress.</div>`;
    return;
  }
  el.innerHTML = list
    .map((ins) => {
      const cls = ins.action === "buy" ? "ins-buy" : ins.action === "sell" ? "ins-sell" : "ins-wait";
      const exitLine = (ins.exit_orders || [])
        .map((o) => `<span class="exit-chip"><b>${o.label}</b>${o.price !== null && o.price !== undefined ? " " + fmt(o.price) : ""} — ${o.instruction}</span>`)
        .join("");
      return `<div class="ins ${cls}">
        <div class="ins-head">
          <span class="badge ${sideClass(ins.action === "buy" ? "BUY" : ins.action === "sell" ? "SELL" : "WAIT")}">${ins.verdict}</span>
          <strong>${ins.display}</strong>
          <span class="ins-conf">confidence ${pct(ins.confidence, 0)} · P(target before stop) ≈ ${pct(ins.p_target_first, 0)}</span>
          <button class="ins-link" data-symbol="${ins.symbol}">chart &amp; full plan →</button>
        </div>
        <div class="ins-line">${ins.one_line}</div>
        ${sizeLine(ins)}
        <div class="ins-pred">🔮 ${ins.prediction}</div>
        ${exitLine ? `<div class="ins-exits">${exitLine}</div>` : ""}
      </div>`;
    })
    .join("");
  el.querySelectorAll(".ins-link").forEach((btn) =>
    btn.addEventListener("click", () => {
      loadPair(btn.dataset.symbol);
      $("#pair-title").scrollIntoView({ behavior: "smooth", block: "center" });
    })
  );
}

/* ---------------- scan ---------------- */

/* ---------------- authenticated API access ---------------- */

// every market request carries the session token; 401/403 drop the user back to the paywall
async function apiFetch(url, opts = {}) {
  const headers = { ...(opts.headers || {}), ...(window.Auth ? window.Auth.headers() : {}) };
  // A full scan can take about a minute on first load, so it gets a longer time limit.
  const timeoutMs = url.startsWith("/api/scan") ? 90000 : 20000;
  const res = await window.KLEVS.request(url, { ...opts, headers }, { timeoutMs });
  if (res.status === 401) {
    lockPaywall("Your session has ended. Log in again to continue.", true);
  } else if (res.status === 403) {
    lockPaywall("Paid access is required. Choose a plan and pay; access unlocks once the operator verifies your payment.");
  }
  return res;
}

const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[c]);

async function loadScan(refresh = false) {
  if (scanInProgress) return;
  scanInProgress = true;
  const btn = $("#btn-refresh");
  btn.disabled = true;
  btn.textContent = "Scanning…";
  if (!SCAN) {
    // zero state: no scan yet, so say so instead of showing an empty feed
    $("#chip-source").textContent = "scanning…";
    $("#insights").innerHTML =
      `<li class="hint">Scanning all 32 instruments for the first time. This can take up to a minute; the feed fills in automatically.</li>`;
  }
  try {
    const res = await apiFetch(`/api/scan${refresh ? "?refresh=true" : ""}`);
    if (res.status === 401 || res.status === 403) return;   // paywall already shown
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `server returned ${res.status}`);
    }
    SCAN = await res.json();
    scanFailed = false;
    renderScan();
    if (!TRADE.loaded) loadTradeSettings();
  } catch (e) {
    if (e.network) {
      // Keep the last loaded signals on screen and say why they may be out of date.
      scanFailed = true;
      if (!SCAN) $("#chip-source").textContent = "offline";
      if (SCAN) renderScan();
      const note = SCAN
        ? "Connection problem: showing the last loaded signals. They refresh when you reconnect or press Rescan."
        : "Connection problem: the signals will load automatically when you reconnect.";
      if (SCAN) $("#insights").insertAdjacentHTML("afterbegin", `<li class="warn">${esc(note)}</li>`);
      else $("#insights").innerHTML = `<li class="warn">${esc(note)}</li>`;   // replaces the "scanning…" hint
    } else {
      if (!SCAN) $("#chip-source").textContent = "unavailable";
      $("#insights").innerHTML = `<li class="warn">Failed to load scan: ${esc(e.message || e)}</li>`;
    }
  } finally {
    scanInProgress = false;
    btn.disabled = false;
    btn.textContent = "↻ Rescan market";
  }
}

function renderScan() {
  if (!SCAN) return;
  $("#chip-source").textContent = SCAN.data_source.split(" ")[0];
  $("#chip-source").title = SCAN.data_source;
  $("#chip-dxy").textContent = fmt(SCAN.dollar_index, 1);
  $("#chip-breadth").textContent = pct(SCAN.breadth, 0);
  $("#chip-mood").textContent = SCAN.summary.market_mood || "—";
  $("#chip-signals").textContent =
    `${SCAN.summary.buy_signals == null ? 0 : SCAN.summary.buy_signals}▲ / ${SCAN.summary.sell_signals == null ? 0 : SCAN.summary.sell_signals}▼`;

  renderInstructions(SCAN.instructions || []);

  // strength bars
  const rows = SCAN.currency_strength || [];
  const maxAbs = Math.max(10, ...rows.map((r) => Math.abs(r.score)));
  $("#strength").innerHTML = rows
    .map((r) => {
      const w = (Math.abs(r.score) / maxAbs) * 50;
      const left = r.score >= 0 ? 50 : 50 - w;
      const color = r.score >= 0 ? "var(--green)" : "var(--red)";
      return `<div class="str-row">
        <div class="cur">${r.currency}</div>
        <div class="str-bar"><i style="left:${left}%;width:${w}%;background:${color}"></i></div>
        <div class="sc ${r.score >= 0 ? "num-pos" : "num-neg"}">${r.score > 0 ? "+" : ""}${r.score.toFixed(1)}</div>
      </div>`;
    })
    .join("");

  // insights
  $("#insights").innerHTML = (SCAN.insights || [])
    .map((t) => {
      let cls = "";
      if (t.startsWith("⛔") || t.startsWith("⚠") || t.startsWith("SIGNAL FLIPPED")) cls = "warn";
      else if (t.startsWith("WHEN TO") || t.startsWith("INSTRUCTION")) cls = "good";
      return `<li class="${cls}">${t}</li>`;
    })
    .join("");

  // signals table
  const all = [...(SCAN.signals || []), ...(SCAN.watchlist || [])];
  $("#signal-count").textContent = `${all.length} instruments · click a row for the full plan`;
  all.sort((a, b) => {
    const va = a[sortKey], vb = b[sortKey];
    if (typeof va === "number" && typeof vb === "number") return (va - vb) * sortDir;
    return String(va).localeCompare(String(vb)) * sortDir;
  });
  const tbody = $("#signals-table tbody");
  tbody.innerHTML = all
    .map(
      (p) => `<tr data-symbol="${p.symbol}">
      <td><strong>${p.display}</strong></td>
      <td><span class="badge ${sideClass(p.side)}">${p.side === "WAIT" ? "WAIT" : p.side + " NOW"}</span></td>
      <td>${confCell(p.confidence)}</td>
      <td>${String(p.regime).replace("_", " ")}</td>
      <td>${fmt(p.price)}</td>
      <td>${pct(p.p_up, 0)}</td>
      <td>${typeof p.p_target_first === "number" ? pct(p.p_target_first, 0) : "—"}</td>
      <td class="${p.expected_move_pct >= 0 ? "num-pos" : "num-neg"}">${p.expected_move_pct ? pct(p.expected_move_pct, 2) : "—"}</td>
      <td>${fmt(p.entry)}</td>
      <td>${fmt(p.target)}</td>
      <td>${fmt(p.stop)}</td>
      <td>${p.target2 ? fmt(p.target2) : "—"}</td>
      <td>${p.risk_reward ? p.risk_reward.toFixed(2) : "—"}</td>
      <td>${p.lots ? p.lots.toFixed(2) : "—"}</td>
      <td>${p.risk_amount ? "$" + fmt(p.risk_amount, 0) : "—"}</td>
      <td>${p.time_stop_bars ? p.time_stop_bars + " bars" : "—"}</td>
    </tr>`
    )
    .join("");

  tbody.querySelectorAll("tr").forEach((tr) =>
    tr.addEventListener("click", () => {
      tbody.querySelectorAll("tr").forEach((x) => x.classList.remove("selected"));
      tr.classList.add("selected");
      loadPair(tr.dataset.symbol);
    })
  );

  renderHeatmap(all);
  renderCorrelation(SCAN.correlation);
  populateBacktestSymbols(all);
}

// Forex heatmap: one tile per instrument. Colour is the signal side; brightness is the confidence.
function renderHeatmap(rows) {
  const el = $("#heatmap");
  if (!el) return;
  if (!rows || rows.length === 0) {
    el.innerHTML = `<div class="empty">No instruments yet</div>`;
    return;
  }
  el.innerHTML = rows
    .map((p) => {
      const conf = typeof p.confidence === "number" && Number.isFinite(p.confidence)
        ? Math.max(0, Math.min(1, p.confidence)) : 0;
      const rgb = p.side === "BUY" ? "35,193,123" : p.side === "SELL" ? "240,85,107" : "139,152,173";
      const alpha = p.side === "WAIT" ? 0.14 : 0.18 + conf * 0.62;
      const label = p.side === "WAIT" ? "WAIT" : `${p.side} NOW`;
      return `<button class="heat-tile" data-symbol="${esc(p.symbol)}" style="background:rgba(${rgb},${alpha.toFixed(2)})" title="${esc(p.display)} · ${label} · confidence ${pct(p.confidence, 0)}">
        <span class="heat-name">${esc(p.display)}</span>
        <span class="heat-side">${label}</span>
        <span class="heat-meta">${pct(p.confidence, 0)} · ${fmt(p.price)}</span>
      </button>`;
    })
    .join("");
  el.querySelectorAll(".heat-tile").forEach((btn) =>
    btn.addEventListener("click", () => {
      loadPair(btn.dataset.symbol);
      $("#pair-title").scrollIntoView({ behavior: "smooth", block: "center" });
    })
  );
}

// sortable headers
$$("#signals-table th").forEach((th) =>
  th.addEventListener("click", () => {
    const key = th.dataset.sort;
    if (sortKey === key) sortDir *= -1;
    else { sortKey = key; sortDir = -1; }
    renderScan();
  })
);

/* ---------------- pair detail ---------------- */

async function loadPair(symbol) {
  $("#pair-title").innerHTML = `${symbol} <span class="hint">loading…</span>`;
  try {
    const res = await apiFetch(`/api/pair/${symbol}`);
    if (!res.ok) { $("#pair-title").innerHTML = `${symbol} <span class="hint">locked — paid access required</span>`; return; }
    PAIR = await res.json();
    renderPair();
  } catch (e) {
    $("#pair-title").innerHTML = `${symbol} <span class="hint">failed to load</span>`;
  }
}

function renderPair() {
  if (!PAIR) return;
  if (PAIR.status === "unavailable") {
    // the publisher could not load live data for this market: say so, and clear the old chart
    $("#pair-title").innerHTML = `${esc(PAIR.display)} <span class="hint">live data unavailable</span>`;
    $("#pair-meta").innerHTML = "";
    for (const id of ["chart", "rsi-chart"]) {
      const c = $(`#${id}`);
      if (c && c.getContext) c.getContext("2d").clearRect(0, 0, c.width, c.height);
    }
    $("#plan-box").innerHTML = `<h3>Trade instructions — ${esc(PAIR.display)}</h3>
      <p class="hint">No signal. Live data could not be loaded for this market (${esc(PAIR.reason)}). It is retried on every scheduled publish.</p>`;
    return;
  }
  const p = PAIR.plan;
  $("#pair-title").innerHTML =
    `${PAIR.display} <span class="hint">${PAIR.timeframe} · regime ${PAIR.regime.replace("_", " ")} · ADX ${PAIR.adx}</span>`;

  $("#pair-meta").innerHTML = `
    <div class="chip"><span class="k">Signal</span><span class="v"><span class="badge ${sideClass(p.side)}">${p.side}</span></span></div>
    <div class="chip"><span class="k">Composite</span><span class="v">${p.score >= 0 ? "+" : ""}${p.score.toFixed(2)}</span></div>
    <div class="chip"><span class="k">Confidence</span><span class="v">${pct(p.confidence, 0)}</span></div>
    <div class="chip"><span class="k">P(up)</span><span class="v">${pct(p.p_up, 0)}</span></div>
    <div class="chip"><span class="k">ATR</span><span class="v">${fmt(p.atr)}</span></div>
    <div class="chip"><span class="k">Vol regime</span><span class="v">×${PAIR.vol_ratio}</span></div>
  `;

  drawCandles($("#chart"), PAIR.candles, PAIR.overlays, p);
  drawRsi($("#rsi-chart"), PAIR.rsi);

  const f = PAIR.forecast;
  const steps = (p.actions || [])
    .map((a) => `<li>${a}</li>`)
    .join("");
  $("#plan-box").innerHTML = `
    <h3>Trade instructions — ${PAIR.display}</h3>
    <div class="lifecycle">
      <div class="stage stage-in">
        <div class="stage-tag">① WHEN TO ${p.side === "BUY" ? "BUY" : p.side === "SELL" ? "SELL" : "ACT"}</div>
        <div class="stage-main">${
          p.side === "WAIT"
            ? `<span class="badge wait">DO NOT BUY YET</span> — no qualifying edge`
            : `<span class="badge ${sideClass(p.side)}">${p.side} NOW</span> at ≈ ${fmt(p.entry)}`
        }</div>
      </div>
      <div class="stage stage-pred">
        <div class="stage-tag">② PREDICTED OUTCOME</div>
        <div class="stage-main">${p.predicted_outcome || "—"}</div>
      </div>
      <div class="stage stage-out">
        <div class="stage-tag">③ WHEN TO SELL / EXIT</div>
        <div class="stage-main">
          take profit at <strong>${fmt(p.target)}</strong> · stop-loss at
          <strong>${fmt(p.stop)}</strong> · trail to <strong>${fmt(p.target2)}</strong>
          · time stop <strong>${p.time_stop_bars} bars</strong>
          · P(target before stop) ≈ <strong>${pct(p.p_target_first, 0)}</strong>
        </div>
      </div>
    </div>
    <div class="levels">
      <div class="level"><div class="lbl">Entry</div><div class="val">${fmt(p.entry)}</div></div>
      <div class="level"><div class="lbl">Stop loss (sell all)</div><div class="val" style="color:var(--red)">${fmt(p.stop)}</div></div>
      <div class="level"><div class="lbl">Take profit (sell)</div><div class="val" style="color:var(--green)">${fmt(p.target)}</div></div>
      <div class="level"><div class="lbl">Stretch target</div><div class="val" style="color:var(--green)">${fmt(p.target2)}</div></div>
      <div class="level"><div class="lbl">Risk : reward</div><div class="val">1 : ${p.risk_reward ? p.risk_reward.toFixed(2) : "—"}</div></div>
      <div class="level"><div class="lbl">Size</div><div class="val">${p.lots ? p.lots.toFixed(2) + " lots" : "—"}</div></div>
    </div>
    <ol class="actions">${steps}</ol>
    ${f ? `<div class="factors">
      <span class="fchip">ML accuracy ${f.accuracy ? pct(f.accuracy, 0) : "—"}</span>
      <span class="fchip">expected move ${pct(f.expected_move_pct, 2)}</span>
      <span class="fchip">P(up) ${pct(f.p_up, 0)}</span>
      ${Object.entries(f.feature_importance || {})
        .slice(0, 5)
        .map(([k, v]) => `<span class="fchip">${k} ${(v * 100).toFixed(0)}%</span>`)
        .join("")}
    </div>` : ""}
    <div class="factors">
      ${Object.entries(p.factors || {})
        .map(([k, v]) => `<span class="fchip">${k}: ${v >= 0 ? "+" : ""}${v.toFixed(2)}</span>`)
        .join("")}
    </div>
  `;
}

/* ---------------- canvas: candlestick chart ---------------- */

function drawCandles(canvas, candles, overlays, plan) {
  const ctx = canvas.getContext("2d");
  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);
  if (!candles || !candles.length) return;

  const padL = 8, padR = 64, padT = 10, padB = 22;
  const lows = candles.map((c) => c.l);
  const highs = candles.map((c) => c.h);
  const extraLines = [plan.stop, plan.target, plan.target2].filter((v) => v && Number.isFinite(v));
  let min = Math.min(...lows, ...extraLines);
  let max = Math.max(...highs, ...extraLines);
  const span = (max - min) || 1;
  min -= span * 0.06; max += span * 0.06;

  const x = (i) => padL + (i / candles.length) * (W - padL - padR);
  const y = (v) => padT + (1 - (v - min) / (max - min)) * (H - padT - padB);

  // grid + price axis
  ctx.font = "10px Consolas, monospace";
  for (let g = 0; g <= 5; g++) {
    const v = min + ((max - min) * g) / 5;
    const yy = y(v);
    ctx.strokeStyle = "#1b2333";
    ctx.beginPath(); ctx.moveTo(padL, yy); ctx.lineTo(W - padR, yy); ctx.stroke();
    ctx.fillStyle = "#8b98ad";
    ctx.fillText(v.toPrecision(6).slice(0, 9), W - padR + 5, yy + 3);
  }

  // candles
  const cw = Math.max(1.2, ((W - padL - padR) / candles.length) * 0.62);
  candles.forEach((c, i) => {
    const up = c.c >= c.o;
    ctx.strokeStyle = ctx.fillStyle = up ? "#23c17b" : "#f0556b";
    const xx = x(i);
    ctx.beginPath(); ctx.moveTo(xx, y(c.h)); ctx.lineTo(xx, y(c.l)); ctx.stroke();
    const top = y(Math.max(c.o, c.c));
    const bot = y(Math.min(c.o, c.c));
    ctx.fillRect(xx - cw / 2, top, cw, Math.max(1, bot - top));
  });

  // overlays
  const drawLine = (arr, color, dash = []) => {
    if (!arr) return;
    ctx.strokeStyle = color;
    ctx.setLineDash(dash);
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    let started = false;
    arr.forEach((v, i) => {
      if (v === null || v === undefined) return;
      const xx = x(i), yy = y(v);
      if (!started) { ctx.moveTo(xx, yy); started = true; }
      else ctx.lineTo(xx, yy);
    });
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.lineWidth = 1;
  };
  drawLine(overlays.ema20, "#4da3ff");
  drawLine(overlays.ema50, "#f2b63d");
  drawLine(overlays.ema200, "#9d7bff");
  drawLine(overlays.bb_upper, "#2c3a52", [4, 4]);
  drawLine(overlays.bb_lower, "#2c3a52", [4, 4]);

  // buy marker: an arrow under the latest candle while the pair's current signal is BUY NOW
  if (plan && plan.side === "BUY" && candles.length) {
    const last = candles[candles.length - 1];
    const xx = x(candles.length - 1);
    const yy = y(last.l) + 6;
    ctx.fillStyle = "#23c17b";
    ctx.beginPath();
    ctx.moveTo(xx, yy);
    ctx.lineTo(xx - 6, yy + 10);
    ctx.lineTo(xx + 6, yy + 10);
    ctx.closePath();
    ctx.fill();
    ctx.font = "bold 10px Consolas, monospace";
    ctx.fillText("BUY", xx - 12, yy + 22);
    ctx.font = "10px Consolas, monospace";
  }

  // plan levels
  const level = (v, color, label) => {
    if (!v || !Number.isFinite(v)) return;
    const yy = y(v);
    ctx.strokeStyle = color;
    ctx.setLineDash([6, 4]);
    ctx.beginPath(); ctx.moveTo(padL, yy); ctx.lineTo(W - padR, yy); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = color;
    ctx.fillText(label, W - padR + 5, yy + 3);
  };
  if (plan && plan.side !== "WAIT") {
    level(plan.entry, "#4da3ff", "entry");
    level(plan.stop, "#f0556b", "SL");
    level(plan.target, "#23c17b", "TP");
    level(plan.target2, "#23c17b", "TP2");
  }

  // legend
  const legend = [["EMA20", "#4da3ff"], ["EMA50", "#f2b63d"], ["EMA200", "#9d7bff"], ["BB", "#2c3a52"]];
  let lx = padL + 6;
  legend.forEach(([txt, color]) => {
    ctx.fillStyle = color;
    ctx.fillRect(lx, padT + 2, 9, 9);
    ctx.fillStyle = "#8b98ad";
    ctx.fillText(txt, lx + 13, padT + 10);
    lx += 20 + txt.length * 6.4;
  });
}

function drawRsi(canvas, rsi) {
  const ctx = canvas.getContext("2d");
  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);
  if (!rsi || !rsi.length) return;
  const padL = 8, padR = 64, padT = 8, padB = 8;
  const x = (i) => padL + (i / rsi.length) * (W - padL - padR);
  const y = (v) => padT + (1 - v / 100) * (H - padT - padB);

  // overbought / oversold bands
  ctx.fillStyle = "rgba(240,85,107,0.08)";
  ctx.fillRect(padL, y(100), W - padL - padR, y(70) - y(100));
  ctx.fillStyle = "rgba(35,193,123,0.08)";
  ctx.fillRect(padL, y(30), W - padL - padR, y(0) - y(30));

  ctx.strokeStyle = "#4da3ff";
  ctx.beginPath();
  let started = false;
  rsi.forEach((v, i) => {
    if (v === null || v === undefined) return;
    const xx = x(i), yy = y(v);
    if (!started) { ctx.moveTo(xx, yy); started = true; }
    else ctx.lineTo(xx, yy);
  });
  ctx.stroke();

  ctx.fillStyle = "#8b98ad";
  ctx.font = "10px Consolas, monospace";
  [30, 50, 70].forEach((v) => {
    ctx.strokeStyle = "#243049";
    ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(padL, y(v)); ctx.lineTo(W - padR, y(v)); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillText(`RSI ${v}`, W - padR + 5, y(v) + 3);
  });
}

/* ---------------- correlation heatmap ---------------- */

function renderCorrelation(corr) {
  const el = $("#corr");
  if (!corr || !corr.labels || corr.labels.length === 0) {
    el.innerHTML = `<div class="empty">No correlation data</div>`;
    return;
  }
  const labels = corr.labels.slice(0, 14);
  const n = labels.length;
  const color = (v) => {
    if (v >= 0) return `rgba(35,193,123,${0.12 + Math.abs(v) * 0.7})`;
    return `rgba(240,85,107,${0.12 + Math.abs(v) * 0.7})`;
  };
  let html = `<table><thead><tr><th></th>${labels.map((l) => `<th>${l.slice(0, 6)}</th>`).join("")}</tr></thead><tbody>`;
  for (let i = 0; i < n; i++) {
    html += `<tr><th>${labels[i].slice(0, 6)}</th>`;
    for (let j = 0; j < n; j++) {
      const v = corr.matrix[i][j];
      html += `<td style="background:${color(v)}" title="${labels[i]}/${labels[j]}: ${v.toFixed(2)}">${v.toFixed(2).slice(1)}</td>`;
    }
    html += `</tr>`;
  }
  html += `</tbody></table>`;
  el.innerHTML = html;
}

/* ---------------- backtest lab ---------------- */

let backtestLoaded = false;
let backtestSeq = 0;

function populateBacktestSymbols(rows) {
  const sel = $("#bt-symbol");
  if (sel.options.length) return;
  rows.forEach((r) => {
    const o = document.createElement("option");
    o.value = r.symbol;
    o.textContent = `${r.display} (${r.symbol})`;
    sel.appendChild(o);
  });
  sel.addEventListener("change", loadBacktest);
  if (!backtestLoaded && sel.options.length) {
    backtestLoaded = true;
    loadBacktest();
  }
}

$("#btn-backtest").addEventListener("click", loadBacktest);

// Shows the stored result for the selected pair. The backtest job (GitHub Actions) writes the results;
// this page only reads them. Paid members only, like every other market request.
async function loadBacktest() {
  const symbol = $("#bt-symbol").value;
  if (!symbol) return;
  const seq = ++backtestSeq;
  const btn = $("#btn-backtest");
  const status = $("#bt-status");
  btn.disabled = true;
  btn.textContent = "Loading…";
  try {
    const res = await apiFetch(`/api/backtest/${encodeURIComponent(symbol)}`);
    const out = await res.json().catch(() => ({}));
    if (seq !== backtestSeq) return;            // a newer pair was chosen while this loaded
    if (!res.ok) throw new Error(out.detail || "backtest unavailable");
    renderBacktest(out);
    const p = out.params || {};
    status.textContent = `Stored ${out.run_at} · ${out.data_source} · ${p.bars_used || p.bars} bars · ` +
      `equity $${fmt(p.equity, 0)} · risk ${p.risk_per_trade_pct}% per trade`;
  } catch (e) {
    if (seq !== backtestSeq) return;
    drawEquity($("#equity-chart"), null);
    $("#trades-table tbody").innerHTML = "";
    $("#bt-metrics").innerHTML = `<div class="metric"><div class="k">Backtest</div><div class="v">${esc(e.message || e)}</div></div>`;
    status.textContent = "No stored result to show for this pair yet.";
  } finally {
    if (seq === backtestSeq) {
      btn.disabled = false;
      btn.textContent = "↻ Reload result";
    }
  }
}

function renderBacktest(out) {
  const m = out.metrics || {};
  const cards = [
    ["Trades", m.trades == null ? 0 : m.trades],
    ["Win rate", m.win_rate ? pct(m.win_rate, 1) : "—"],
    ["Profit factor", m.profit_factor == null ? "—" : m.profit_factor],
    ["Total P&L", m.total_pnl ? "$" + fmt(m.total_pnl, 0) : "—"],
    ["Return", m.total_return_pct !== undefined ? m.total_return_pct + "%" : "—"],
    ["Expectancy", m.expectancy_r !== undefined ? m.expectancy_r + "R" : "—"],
    ["Sharpe", m.sharpe == null ? "—" : m.sharpe],
    ["Sortino", m.sortino == null ? "—" : m.sortino],
    ["Max DD", m.max_drawdown_pct !== undefined ? m.max_drawdown_pct + "%" : "—"],
    ["Avg win / loss", `${m.avg_win == null ? 0 : m.avg_win} / ${m.avg_loss == null ? 0 : m.avg_loss}`],
  ];
  $("#bt-metrics").innerHTML = cards
    .map(
      ([k, v]) => `<div class="metric"><div class="k">${k}</div><div class="v">${v}</div></div>`
    )
    .join("");

  drawEquity($("#equity-chart"), out.equity_curve);
  const tb = $("#trades-table tbody");
  tb.innerHTML = (out.trades || [])
    .slice()
    .reverse()
    .map(
      (t) => `<tr>
      <td><span class="badge ${sideClass(t.side)}">${t.side}</span></td>
      <td>${t.entry_time}</td><td>${fmt(t.entry)}</td>
      <td>${t.exit_time}</td><td>${fmt(t.exit)}</td>
      <td>${fmt(t.stop)}</td><td>${fmt(t.target)}</td>
      <td class="${t.r_multiple >= 0 ? "num-pos" : "num-neg"}">${t.r_multiple}R</td>
      <td class="${t.pnl >= 0 ? "num-pos" : "num-neg"}">$${fmt(t.pnl, 2)}</td>
      <td>${t.exit_reason}</td>
    </tr>`
    )
    .join("");
}

function drawEquity(canvas, curve) {
  const ctx = canvas.getContext("2d");
  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);
  if (!curve || !curve.v || !curve.v.length) return;
  const padL = 8, padR = 70, padT = 12, padB = 20;
  const vals = curve.v;
  let min = Math.min(...vals), max = Math.max(...vals);
  const span = (max - min) || 1;
  min -= span * 0.08; max += span * 0.08;
  const x = (i) => padL + (i / (vals.length - 1)) * (W - padL - padR);
  const y = (v) => padT + (1 - (v - min) / (max - min)) * (H - padT - padB);

  ctx.font = "10px Consolas, monospace";
  for (let g = 0; g <= 4; g++) {
    const v = min + ((max - min) * g) / 4;
    const yy = y(v);
    ctx.strokeStyle = "#1b2333";
    ctx.beginPath(); ctx.moveTo(padL, yy); ctx.lineTo(W - padR, yy); ctx.stroke();
    ctx.fillStyle = "#8b98ad";
    ctx.fillText("$" + v.toFixed(0), W - padR + 5, yy + 3);
  }

  // fill under curve
  const grad = ctx.createLinearGradient(0, padT, 0, H);
  grad.addColorStop(0, "rgba(77,163,255,0.28)");
  grad.addColorStop(1, "rgba(77,163,255,0.02)");
  ctx.fillStyle = grad;
  ctx.beginPath();
  ctx.moveTo(x(0), y(vals[0]));
  vals.forEach((v, i) => ctx.lineTo(x(i), y(v)));
  ctx.lineTo(x(vals.length - 1), H - padB);
  ctx.lineTo(x(0), H - padB);
  ctx.closePath();
  ctx.fill();

  ctx.strokeStyle = "#4da3ff";
  ctx.lineWidth = 1.8;
  ctx.beginPath();
  vals.forEach((v, i) => (i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v))));
  ctx.stroke();
  ctx.lineWidth = 1;

  ctx.fillStyle = "#8b98ad";
  ctx.fillText("Equity curve", padL + 6, padT + 10);
}

/* ---------------- paid-access gate ---------------- */

function showChip(s) {
  const chip = $("#chip-sub");
  chip.hidden = false;
  const label = s.role === "admin" ? "Admin" : (s.plan_name || "Paid");
  $("#chip-sub-name").textContent = `${label} · ${s.username || s.email}`;
  $("#btn-logout").hidden = false;
}

function unlockApp(s) {
  $("#paywall").hidden = true;
  document.body.style.overflow = "";
  showChip(s);
  loadScan();   // market data is only requested once access is verified
}

function lockPaywall(message, clearSession = false) {
  if (clearSession) localStorage.removeItem("kfx_sub");
  $("#paywall").hidden = false;
  $("#pw-msg").textContent = message || $("#pw-msg").dataset.default;
  document.body.style.overflow = "hidden";
  $("#chip-sub").hidden = true;
  $("#btn-logout").hidden = !localStorage.getItem("kfx_sub");
}

const OFFLINE_PAYWALL_MESSAGE =
  "We couldn't reach the server, so your access could not be checked. Check your connection, then press \"I have already paid\" to try again.";

async function initSubscriberGate() {
  const s = window.Auth ? await window.Auth.refresh() : null;
  if (s && s.access_active) return unlockApp(s);   // includes the last known access when offline
  if (s && s.offline) return lockPaywall(OFFLINE_PAYWALL_MESSAGE);
  if (s) return lockPaywall("Your account has no active access yet. Choose a plan and pay; access unlocks once the operator verifies your payment.");
  return lockPaywall();
}

/* a verified login/registration that carries access unlocks the app without a reload */
if (window.Auth) {
  window.Auth.onAuth((s) => { if (s && s.access_active) unlockApp(s); });
}

$("#pw-msg").dataset.default = $("#pw-msg").textContent;

$("#btn-logout").addEventListener("click", () => {
  TRADE = { lotSize: null, loaded: false };   // the next account must not inherit this lot size
  window.Auth.logout();
});
$("#pw-pay").addEventListener("click", () => window.Auth.open("plan"));
$("#pw-status").addEventListener("click", async () => {
  const s = await window.Auth.refresh();
  if (s && s.access_active) return unlockApp(s);
  if (s && s.offline) return lockPaywall(OFFLINE_PAYWALL_MESSAGE);
  window.Auth.open(s ? "status" : "login");
});
$("#pw-login").addEventListener("click", () => window.Auth.open("login"));
$("#pw-forgot").addEventListener("click", () => window.Auth.open("forgot"));

/* ---------------- lot size: each registered user's own choice ---------------- */

// The published signals are shared, so a user's lot size only changes what that user sees:
// risk and first target are the per-lot figures from the signal, multiplied by the user's lots.
let TRADE = { lotSize: null, loaded: false };
const money = (v) => `${Number(v).toFixed(2)} USD`;

function sizeLine(ins) {
  if ((ins.action !== "buy" && ins.action !== "sell") || ins.lots == null) return "";
  const yours = TRADE.lotSize;
  if (yours == null) {
    return `<div class="ins-size">Signal size ${ins.lots.toFixed(2)} lots · risk ${money(ins.risk_amount)} at the stop · first target ${money(ins.reward_per_lot * ins.lots)} <span class="hint">change it above</span></div>`;
  }
  const warn = yours > ins.lots * 2
    ? ` <span class="warn">more than twice the signal size (${ins.lots.toFixed(2)} lots)</span>`
    : ` <span class="hint">signal size ${ins.lots.toFixed(2)} lots</span>`;
  return `<div class="ins-size">Your size ${yours.toFixed(2)} lots · risk ${money(ins.risk_per_lot * yours)} at the stop · first target ${money(ins.reward_per_lot * yours)}${warn}</div>`;
}

function paintLotControl() {
  $("#lot-input").value = TRADE.lotSize == null ? "" : TRADE.lotSize.toFixed(2);
  $("#lot-status").textContent = TRADE.lotSize == null
    ? "Using the signal size (risk-based)."
    : `Using your size of ${TRADE.lotSize.toFixed(2)} lots.`;
}

async function loadTradeSettings() {
  let res;
  try {
    res = await apiFetch("/api/me/trade-settings");
  } catch (e) {
    return;                                // offline: keep the signal size until we can check again
  }
  if (!res.ok) return;
  const body = await res.json();
  TRADE = { lotSize: body.lot_size, loaded: true };
  paintLotControl();
  if (SCAN) renderInstructions(SCAN.instructions || []);
}

async function saveLotSize(value) {
  const res = await apiFetch("/api/me/trade-settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lot_size: value }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    $("#lot-status").textContent = body.detail || "Could not save your lot size.";
    return;
  }
  const body = await res.json();
  TRADE = { lotSize: body.lot_size, loaded: true };
  paintLotControl();
  if (SCAN) renderInstructions(SCAN.instructions || []);
}

/* ---------------- boot ---------------- */

$("#btn-refresh").addEventListener("click", () => loadScan(true));
$("#lot-save").addEventListener("click", () => {
  const raw = $("#lot-input").value.trim();
  const value = Number(raw);
  const onGrid = Math.abs(Math.round(value * 100) - value * 100) < 1e-6;
  if (raw === "" || !Number.isFinite(value) || value < 0.01 || value > 50 || !onGrid) {
    $("#lot-status").textContent = "Enter a lot size from 0.01 to 50 in steps of 0.01, or use the signal size.";
    return;
  }
  saveLotSize(Math.round(value * 100) / 100);
});
$("#lot-reset").addEventListener("click", () => saveLotSize(null));
initSubscriberGate();
// live instruction feed: re-scan every 5 minutes (models are cached, so this is cheap)
setInterval(() => { if ($("#paywall").hidden) loadScan(true); }, 5 * 60 * 1000);
// back online after a failed scan: load the signals again without asking the user
window.addEventListener("online", () => {
  if (scanFailed && !scanInProgress && $("#paywall").hidden) loadScan(true);
});
