/* LiquidSky dashboard — polls the Flask API and renders the instrument panel. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const api = {
    status: () => fetch("/api/status").then((r) => r.json()),
    report: () => fetch("/api/report").then((r) => r.json()),
    equity: () => fetch("/api/equity").then((r) => r.json()),
    logs: (after) => fetch(`/api/logs?after=${after}`).then((r) => r.json()),
    post: (path) => fetch(path, { method: "POST" }).then((r) => r.json().then((j) => ({ ok: r.ok, j }))),
  };

  let lastLogId = 0;
  let startingBalance = null;
  let cityFilter = "all";
  let lastPositions = [];

  const CITIES = {
    "KXHIGHNY":   { name: "New York",     abbr: "NYC", lat: 40.779, lon: -73.969 },
    "KXHIGHCHI":  { name: "Chicago",      abbr: "CHI", lat: 41.787, lon: -87.752 },
    "KXHIGHLAX":  { name: "Los Angeles",  abbr: "LAX", lat: 33.942, lon: -118.408 },
    "KXHIGHMIA":  { name: "Miami",        abbr: "MIA", lat: 25.796, lon: -80.287 },
    "KXHIGHAUS":  { name: "Austin",       abbr: "AUS", lat: 30.198, lon: -97.666 },
    "KXHIGHDEN":  { name: "Denver",       abbr: "DEN", lat: 39.862, lon: -104.673 },
    "KXHIGHPHIL": { name: "Philadelphia", abbr: "PHL", lat: 39.873, lon: -75.244 },
  };

  // Simplified continental US boundary (lon, lat pairs, clockwise from NW)
  const US_OUTLINE = [
    [-124.7,48.4],[-124.6,46.2],[-124.3,43.4],[-124.2,41.8],[-124.4,40.4],
    [-120.9,38.3],[-117.2,32.5],[-114.7,32.7],[-111.0,31.3],[-106.6,31.8],
    [-104.5,29.7],[-100.5,28.0],[-97.4,26.0],[-97.1,27.8],[-94.7,29.4],
    [-89.6,29.0],[-88.0,30.2],[-84.9,29.7],[-83.0,29.5],[-81.8,24.5],
    [-80.1,25.8],[-80.0,27.0],[-81.4,31.0],[-81.2,32.0],[-79.5,34.5],
    [-77.3,36.0],[-76.0,37.1],[-75.1,38.8],[-74.0,40.6],[-72.0,41.1],
    [-70.6,41.5],[-70.0,43.1],[-67.9,47.1],[-69.2,47.5],[-71.0,45.3],
    [-72.5,45.0],[-76.9,43.6],[-79.0,43.1],[-82.4,41.7],[-83.1,42.0],
    [-84.8,46.0],[-86.0,46.5],[-87.8,47.8],[-90.4,48.0],[-92.4,46.8],
    [-95.1,49.4],[-100.0,49.0],[-104.0,49.0],[-110.0,49.0],[-120.0,49.0],
    [-124.7,48.4]
  ];

  // ---- formatting helpers ------------------------------------------------
  const usd = (n) =>
    (n < 0 ? "-$" : "$") +
    Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const signed = (n) => (n > 0 ? "+" : "") + usd(n).replace("$", "$");
  const cls = (n) => (n > 0 ? "pos" : n < 0 ? "neg" : "");

  function setText(el, txt) {
    if (el.textContent !== txt) {
      el.textContent = txt;
      el.classList.remove("flash");
      void el.offsetWidth; // reflow to restart animation
      el.classList.add("flash");
    }
  }

  // ---- US map ------------------------------------------------------------
  function renderMap(positions) {
    const svg = $("usMap");
    const W = 800, H = 420;
    const LON_MIN = -127, LON_MAX = -65, LAT_MIN = 23, LAT_MAX = 50;
    function proj(lon, lat) {
      return [
        ((lon - LON_MIN) / (LON_MAX - LON_MIN) * W).toFixed(1),
        ((LAT_MAX - lat) / (LAT_MAX - LAT_MIN) * H).toFixed(1),
      ];
    }

    const activeByCity = {};
    for (const p of positions) {
      const c = (p.city || "").toUpperCase();
      if (!activeByCity[c]) activeByCity[c] = [];
      activeByCity[c].push(p);
    }

    const outlinePath = US_OUTLINE
      .map(([lon, lat], i) => { const [x, y] = proj(lon, lat); return `${i ? "L" : "M"}${x} ${y}`; })
      .join(" ") + " Z";

    let grid = "";
    for (let lat = 25; lat <= 50; lat += 5) {
      const [, y] = proj(0, lat);
      grid += `<line x1="0" y1="${y}" x2="${W}" y2="${y}"/>`;
    }
    for (let lon = -120; lon <= -65; lon += 10) {
      const [x] = proj(lon, 0);
      grid += `<line x1="${x}" y1="0" x2="${x}" y2="${H}"/>`;
    }

    let markers = "";
    for (const [series, city] of Object.entries(CITIES)) {
      const [cx, cy] = proj(city.lon, city.lat);
      const active = activeByCity[series] || [];
      const isActive = active.length > 0;
      const side = isActive ? active[0].side : null;
      const color = side === "yes" ? "var(--cool)" : side === "no" ? "var(--heat)" : "var(--ink-soft)";
      const r = isActive ? 7 : 5;
      const opacity = isActive ? 1 : 0.4;
      const isFiltered = cityFilter === series;
      markers += `
        <g class="city-marker${isActive ? " city-active" : ""}${isFiltered ? " city-filtered" : ""}"
           data-series="${series}" style="cursor:pointer">
          ${isActive ? `<circle cx="${cx}" cy="${cy}" r="18" fill="${color}" opacity="0.08" class="city-pulse"/>` : ""}
          ${isFiltered ? `<circle cx="${cx}" cy="${cy}" r="14" fill="none" stroke="${color}" stroke-width="1.5" opacity="0.5"/>` : ""}
          <circle cx="${cx}" cy="${cy}" r="${r}" fill="${color}" opacity="${opacity}" filter="url(#cglow)"/>
          <text x="${cx}" y="${+cy - 12}" class="city-label">${city.abbr}</text>
        </g>`;
    }

    const activeCount = Object.keys(activeByCity).filter((k) => CITIES[k]).length;
    svg.innerHTML = `
      <defs>
        <filter id="cglow" x="-80%" y="-80%" width="260%" height="260%">
          <feGaussianBlur stdDeviation="3.5" result="b"/>
          <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
        <filter id="mglow" x="-1%" y="-1%" width="102%" height="102%">
          <feGaussianBlur stdDeviation="1.2" result="b"/>
          <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
      </defs>
      <g class="map-grid-lines">${grid}</g>
      <path class="us-fill" d="${outlinePath}"/>
      <path class="us-outline" d="${outlinePath}" filter="url(#mglow)"/>
      <g class="city-markers">${markers}</g>`;

    svg.querySelectorAll(".city-marker").forEach((el) => {
      el.addEventListener("click", () => {
        const s = el.dataset.series;
        setCityFilter(cityFilter === s ? "all" : s);
      });
    });
    $("mapMeta").textContent = activeCount
      ? `${activeCount} cit${activeCount > 1 ? "ies" : "y"} active`
      : "no active positions";
  }

  function setCityFilter(city) {
    cityFilter = city;
    document.querySelectorAll(".city-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.city === city);
    });
    renderPositions(lastPositions);
    renderMap(lastPositions);
  }

  document.querySelectorAll(".city-btn").forEach((btn) => {
    btn.addEventListener("click", () => setCityFilter(btn.dataset.city));
  });

  // ---- status + stat cards ----------------------------------------------
  async function refreshStatus() {
    let s;
    try { s = await api.status(); } catch { return; }
    startingBalance = s.starting_balance;

    // env badge
    const badge = $("envBadge");
    badge.textContent = s.env;
    badge.className = "env-badge " + s.env;

    // runner pill + buttons
    const run = s.runner || {};
    const pill = $("runPill");
    let state = "idle", label = "idle";
    if (run.looping) { state = "looping"; label = "looping"; }
    else if (run.busy) { state = "busy"; label = "scanning"; }
    pill.className = "run-pill " + state;
    $("runLabel").textContent = label;
    $("intervalLbl").textContent = run.scan_interval_minutes ?? "—";

    $("btnOnce").disabled = run.busy || run.looping;
    $("btnStart").disabled = run.looping || run.busy;
    $("btnStop").disabled = !run.looping;

    // stat cards
    setText($("equityVal"), usd(s.equity));
    const delta = s.equity - s.starting_balance;
    const deltaEl = $("equityDelta");
    deltaEl.textContent = `${signed(delta)} vs. start`;
    deltaEl.className = "stat-sub " + cls(delta);

    setText($("cashVal"), usd(s.cash));
    $("openValSub").textContent = `deployed ${usd(s.open_value)}`;

    setText($("openCount"), String(s.open_count));
    const unreal = s.positions.reduce((a, p) => a + (p.unrealized_pnl || 0), 0);
    const unrealEl = $("unrealSub");
    unrealEl.textContent = `unrealized ${signed(unreal)}`;
    unrealEl.className = "stat-sub " + cls(unreal);

    lastPositions = s.positions;
    renderPositions(s.positions);
    renderMap(s.positions);
    $("posMeta").textContent = `${s.open_count} held`;
    $("lastSync").textContent = "synced " + new Date().toLocaleTimeString();
  }

  function renderPositions(positions) {
    const body = $("posBody");
    const filtered = cityFilter === "all"
      ? positions
      : positions.filter((p) => (p.city || "").toUpperCase() === cityFilter);
    if (!filtered.length) {
      const msg = cityFilter === "all" ? "no open positions" : `no positions for ${CITIES[cityFilter]?.abbr ?? cityFilter}`;
      body.innerHTML = `<tr class="empty-row"><td colspan="9">${msg}</td></tr>`;
      return;
    }
    body.innerHTML = filtered
      .sort((a, b) => (b.unrealized_pnl || 0) - (a.unrealized_pnl || 0))
      .map((p) => {
        const last = p.last_price_cents != null ? p.last_price_cents + "¢" : "—";
        const pnl = p.unrealized_pnl || 0;
        const pct = p.pnl_pct ? ` (${p.pnl_pct > 0 ? "+" : ""}${p.pnl_pct}%)` : "";
        return `<tr>
          <td class="market">${p.ticker}</td>
          <td><span class="side-badge ${p.side}">${p.side}</span></td>
          <td class="num">${p.count}</td>
          <td class="num">${p.entry_price_cents}¢</td>
          <td class="num">${last}</td>
          <td class="num">${p.stop_cents}¢</td>
          <td class="num">${usd(p.cost)}</td>
          <td class="num">${usd(p.value)}</td>
          <td class="num ${cls(pnl)}">${signed(pnl)}${pct}</td>
        </tr>`;
      })
      .join("");
  }

  // ---- report ------------------------------------------------------------
  async function refreshReport() {
    let r;
    try { r = await api.report(); } catch { return; }
    $("rOpened").textContent = r.trades_opened;
    $("rClosed").textContent = r.trades_closed;
    $("rWin").textContent = `${r.win_rate}% (${r.wins}/${r.trades_closed})`;
    const rr = $("rRealized");
    rr.textContent = usd(r.realized_pnl);
    rr.className = cls(r.realized_pnl);
    $("rPeak").textContent = usd(r.peak_equity);
    $("rDraw").textContent = `${r.max_drawdown_pct}%`;

    setText($("realizedVal"), usd(r.realized_pnl));
    $("realizedVal").className = "stat-val " + cls(r.realized_pnl);
    $("winSub").textContent = `win rate ${r.win_rate}%`;
  }

  // ---- equity curve (hand-drawn SVG) ------------------------------------
  async function refreshEquity() {
    let data;
    try { data = await api.equity(); } catch { return; }
    const pts = (data.points || []).map((p) => p.equity);
    const svg = $("equityChart");
    const wrap = svg.closest(".chart-wrap");

    if (pts.length < 2) { wrap.classList.add("empty"); svg.innerHTML = ""; return; }
    wrap.classList.remove("empty");

    const W = 800, H = 260, pad = { l: 8, r: 56, t: 16, b: 22 };
    const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
    let lo = Math.min(...pts), hi = Math.max(...pts);
    if (startingBalance != null) { lo = Math.min(lo, startingBalance); hi = Math.max(hi, startingBalance); }
    if (hi === lo) { hi += 1; lo -= 1; }
    const padv = (hi - lo) * 0.12; lo -= padv; hi += padv;

    const x = (i) => pad.l + (i / (pts.length - 1)) * iw;
    const y = (v) => pad.t + (1 - (v - lo) / (hi - lo)) * ih;

    const line = pts.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
    const area = `${line} L${x(pts.length - 1).toFixed(1)} ${(pad.t + ih).toFixed(1)} L${pad.l} ${(pad.t + ih).toFixed(1)} Z`;

    // horizontal gridlines + labels
    let grid = "";
    const ticks = 4;
    for (let i = 0; i <= ticks; i++) {
      const v = lo + ((hi - lo) * i) / ticks;
      const gy = y(v).toFixed(1);
      grid += `<line class="eq-grid" x1="${pad.l}" y1="${gy}" x2="${pad.l + iw}" y2="${gy}"/>`;
      grid += `<text class="eq-label" x="${pad.l + iw + 6}" y="${(+gy + 4).toFixed(1)}">$${v.toFixed(0)}</text>`;
    }
    // start-balance baseline
    let base = "";
    if (startingBalance != null && startingBalance >= lo && startingBalance <= hi) {
      const by = y(startingBalance).toFixed(1);
      base = `<line class="eq-base" x1="${pad.l}" y1="${by}" x2="${pad.l + iw}" y2="${by}"/>`;
    }
    const lastV = pts[pts.length - 1], lx = x(pts.length - 1).toFixed(1), ly = y(lastV).toFixed(1);
    const up = startingBalance == null || lastV >= startingBalance;
    const stroke = up ? "var(--pos)" : "var(--neg)";

    svg.innerHTML = `
      <defs>
        <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${stroke}" stop-opacity="0.28"/>
          <stop offset="100%" stop-color="${stroke}" stop-opacity="0"/>
        </linearGradient>
      </defs>
      ${grid}${base}
      <path class="eq-area" d="${area}"/>
      <path class="eq-line" style="stroke:${stroke}" d="${line}"/>
      <circle class="eq-dot" style="fill:${stroke}" cx="${lx}" cy="${ly}" r="3.5"/>`;

    $("chartMeta").textContent = `${pts.length} snapshots · ${usd(lastV)}`;
  }

  // ---- log console -------------------------------------------------------
  const consoleEl = $("console");
  consoleEl.innerHTML = '<div class="placeholder">awaiting activity…</div>';

  async function refreshLogs() {
    let data;
    try { data = await api.logs(lastLogId); } catch { return; }
    if (!data.lines.length) return;
    const ph = consoleEl.querySelector(".placeholder");
    if (ph) ph.remove();
    const atBottom = consoleEl.scrollHeight - consoleEl.scrollTop - consoleEl.clientHeight < 40;
    for (const ln of data.lines) {
      lastLogId = ln.id;
      const div = document.createElement("div");
      let extra = "";
      const t = ln.text.toUpperCase();
      if (t.includes("BUY")) extra = " buy";
      else if (t.includes("SELL")) extra = " sell";
      else if (t.includes("SETTLE")) extra = " settle";
      div.className = "line " + ln.level + extra;
      div.textContent = ln.text;
      consoleEl.appendChild(div);
    }
    while (consoleEl.childElementCount > 500) consoleEl.removeChild(consoleEl.firstChild);
    if (atBottom) consoleEl.scrollTop = consoleEl.scrollHeight;
  }

  // ---- commands ----------------------------------------------------------
  function bindCommand(id, path) {
    $(id).addEventListener("click", async () => {
      $(id).disabled = true;
      try { await api.post(path); } catch {}
      await refreshStatus();
      // pull logs quickly after a command kicks off
      setTimeout(refreshLogs, 400);
    });
  }
  bindCommand("btnOnce", "/api/run-once");
  bindCommand("btnStart", "/api/loop/start");
  bindCommand("btnStop", "/api/loop/stop");
  $("btnClearLog").addEventListener("click", () => {
    consoleEl.innerHTML = '<div class="placeholder">cleared — awaiting activity…</div>';
  });

  // ---- clock + polling loops --------------------------------------------
  function tick() { $("clock").textContent = new Date().toLocaleTimeString(); }
  setInterval(tick, 1000); tick();

  function poll(fn, ms) { fn(); setInterval(fn, ms); }
  poll(refreshStatus, 3000);
  poll(refreshReport, 6000);
  poll(refreshEquity, 6000);
  poll(refreshLogs, 1500);
})();
