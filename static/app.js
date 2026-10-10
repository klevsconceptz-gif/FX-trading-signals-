/* Klevs FX Trading App — dashboard client.
   Vanilla JS + canvas charts (zero external dependencies). */
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

let SCAN = null;          // last scan report
let PAIR = null;          // last pair detail
let sortKey = "confidence";
let sortDir = -1;

/* ---------------- helpers ---------------- */

const fmt = (v, d = 5) =>
  v === null || v === undefined || Number.isNaN(v)
    ? "—"
    : Number(v).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: Math.min(d, 4) });

const pct = (v, d = 2) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(d)}%`);

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

async function loadScan(refresh = false) {
  const btn = $("#btn-refresh");
  btn.disabled = true;
  btn.textContent = "Scanning…";
  try {
    const res = await fetch(`/api/scan${refresh ? "?refresh=true" : ""}`);
    SCAN = await res.json();
    renderScan();
  } catch (e) {
    $("#insights").innerHTML = `<li class="warn">Failed to load scan: ${e}</li>`;
  } finally {
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
    `${SCAN.summary.buy_signals ?? 0}▲ / ${SCAN.summary.sell_signals ?? 0}▼`;

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
      <td>${pct(p.confidence, 0)}</td>
      <td>${String(p.regime).replace("_", " ")}</td>
      <td>${fmt(p.price)}</td>
      <td>${pct(p.p_up, 0)}</td>
      <td class="${p.expected_move_pct >= 0 ? "num-pos" : "num-neg"}">${p.expected_move_pct ? pct(p.expected_move_pct, 2) : "—"}</td>
      <td>${fmt(p.entry)}</td>
      <td>${fmt(p.target)}</td>
      <td>${fmt(p.stop)}</td>
      <td>${p.risk_reward ? p.risk_reward.toFixed(2) : "—"}</td>
      <td>${p.lots ? p.lots.toFixed(2) : "—"}</td>
      <td>${p.risk_amount ? "$" + fmt(p.risk_amount, 0) : "—"}</td>
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

  renderCorrelation(SCAN.correlation);
  populateBacktestSymbols(all);
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
    const res = await fetch(`/api/pair/${symbol}`);
    PAIR = await res.json();
    renderPair();
  } catch (e) {
    $("#pair-title").innerHTML = `${symbol} <span class="hint">failed to load</span>`;
  }
}

function renderPair() {
  if (!PAIR) return;
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

function populateBacktestSymbols(rows) {
  const sel = $("#bt-symbol");
  if (sel.options.length) return;
  rows.forEach((r) => {
    const o = document.createElement("option");
    o.value = r.symbol;
    o.textContent = `${r.display} (${r.symbol})`;
    sel.appendChild(o);
  });
}

$("#btn-backtest").addEventListener("click", async () => {
  const btn = $("#btn-backtest");
  btn.disabled = true;
  btn.textContent = "Running…";
  try {
    const res = await fetch("/api/backtest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        symbol: $("#bt-symbol").value,
        equity: Number($("#bt-equity").value),
        risk_per_trade_pct: Number($("#bt-risk").value),
        bars: Number($("#bt-bars").value),
      }),
    });
    const out = await res.json();
    renderBacktest(out);
  } catch (e) {
    $("#bt-metrics").innerHTML = `<div class="metric"><div class="k">Error</div><div class="v">${e}</div></div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "▶ Run backtest";
  }
});

function renderBacktest(out) {
  const m = out.metrics || {};
  const cards = [
    ["Trades", m.trades ?? 0],
    ["Win rate", m.win_rate ? pct(m.win_rate, 1) : "—"],
    ["Profit factor", m.profit_factor ?? "—"],
    ["Total P&L", m.total_pnl ? "$" + fmt(m.total_pnl, 0) : "—"],
    ["Return", m.total_return_pct !== undefined ? m.total_return_pct + "%" : "—"],
    ["Expectancy", m.expectancy_r !== undefined ? m.expectancy_r + "R" : "—"],
    ["Sharpe", m.sharpe ?? "—"],
    ["Sortino", m.sortino ?? "—"],
    ["Max DD", m.max_drawdown_pct !== undefined ? m.max_drawdown_pct + "%" : "—"],
    ["Avg win / loss", `${m.avg_win ?? 0} / ${m.avg_loss ?? 0}`],
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

/* ---------------- boot ---------------- */

$("#btn-refresh").addEventListener("click", () => loadScan(true));
loadScan();
// live instruction feed: re-scan every 5 minutes (models are cached, so this is cheap)
setInterval(() => loadScan(true), 5 * 60 * 1000);
