/* LiquidSky dashboard — polls the Flask API and renders the instrument panel. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const qs = (s) => (s ? `?strategy=${encodeURIComponent(s)}` : "");
  const api = {
    strategies: () => fetch("/api/strategies").then((r) => r.json()),
    status: (s) => fetch(`/api/status${qs(s)}`).then((r) => r.json()),
    report: (s) => fetch(`/api/report${qs(s)}`).then((r) => r.json()),
    equity: (s) => fetch(`/api/equity${qs(s)}`).then((r) => r.json()),
    compare: () => fetch("/api/compare").then((r) => r.json()),
    logs: (after) => fetch(`/api/logs?after=${after}`).then((r) => r.json()),
    post: (path) => fetch(path, { method: "POST" }).then((r) => r.json().then((j) => ({ ok: r.ok, j }))),
  };

  let lastLogId = 0;
  let startingBalance = null;
  let cityFilter = "all";
  let lastPositions = [];

  // Strategy switching: activeView is "compare" or a strategy key.
  const STRAT_COLORS = ["#67d6ff", "#ffae5c", "#5ef2b0", "#c9a3ff", "#ff6b81"];
  let strategies = [];          // [{key, name, blurb}]
  let stratColor = {};          // key -> css color
  let activeView = "compare";   // current view: strategy key or "compare"
  let lastCompareSig = null;    // skip re-render when compare data is unchanged

  function isCompare() { return activeView === "compare"; }
  function currentStrategy() { return isCompare() ? null : activeView; }

  // ---- strategy bar + view router ---------------------------------------
  async function initStrategies() {
    let data;
    try { data = await api.strategies(); } catch { return; }
    strategies = data.strategies || [];
    strategies.forEach((s, i) => { stratColor[s.key] = STRAT_COLORS[i % STRAT_COLORS.length]; });
    activeView = strategies.length > 1 ? "compare" : (data.default || strategies[0]?.key);
    renderStrategyBar();
    applyView();
  }

  function renderStrategyBar() {
    const bar = $("strategyBar");
    let html = "";
    for (const s of strategies) {
      const c = stratColor[s.key];
      html += `<button class="strat-tab" data-view="${s.key}" style="--accent:${c}">
        <span class="st-name"><span class="st-dot" style="background:${c}"></span>${s.name}</span>
        <span class="st-sub">${s.blurb}</span>
      </button>`;
    }
    if (strategies.length > 1) {
      html += `<button class="strat-tab compare-tab" data-view="compare" style="--accent:var(--live)">
        <span class="st-name">⊞ compare</span>
        <span class="st-sub">side by side</span>
      </button>`;
    }
    bar.innerHTML = html;
    bar.querySelectorAll(".strat-tab").forEach((el) => {
      el.addEventListener("click", () => { activeView = el.dataset.view; applyView(); });
    });
  }

  function applyView() {
    document.querySelectorAll(".strat-tab").forEach((el) =>
      el.classList.toggle("active", el.dataset.view === activeView));
    const compare = isCompare();
    $("dashboardView").hidden = compare;
    $("compareView").hidden = !compare;
    // Refresh immediately on switch so the view isn't stale until next poll.
    if (compare) refreshCompare();
    else { refreshStatus(); refreshReport(); refreshEquity(); }
  }

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
    // Always runs (even in compare view) to keep the masthead env/runner live;
    // the strategy-specific panels below are hidden in compare and skipped.
    let s;
    try { s = await api.status(currentStrategy()); } catch { return; }
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

    // The panels below live in #dashboardView (hidden in compare view).
    if (isCompare()) { $("lastSync").textContent = "synced " + new Date().toLocaleTimeString(); return; }

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
    if (isCompare()) return;
    let r;
    try { r = await api.report(currentStrategy()); } catch { return; }
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

  // Size the viewBox to the SVG's real pixel box so 1 unit == 1px. Without
  // this the fixed 800-wide viewBox is stretched to the container width and the
  // axis labels render horizontally distorted. Falls back to a sane default
  // when the element isn't laid out yet (e.g. hidden view).
  function fitViewBox(svg, fallbackW, fallbackH) {
    const W = Math.round(svg.clientWidth) || fallbackW;
    const H = Math.round(svg.clientHeight) || fallbackH;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    return { W, H };
  }

  // ---- equity curve (hand-drawn SVG) ------------------------------------
  async function refreshEquity() {
    if (isCompare()) return;
    let data;
    try { data = await api.equity(currentStrategy()); } catch { return; }
    const pts = (data.points || []).map((p) => p.equity);
    const svg = $("equityChart");
    const wrap = svg.closest(".chart-wrap");

    if (pts.length < 2) { wrap.classList.add("empty"); svg.innerHTML = ""; return; }
    wrap.classList.remove("empty");

    const { W, H } = fitViewBox(svg, 800, 260);
    const pad = { l: 8, r: 56, t: 16, b: 22 };
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

  // ---- compare view ------------------------------------------------------
  async function refreshCompare() {
    if (!isCompare()) return;
    let data;
    try { data = await api.compare(); } catch { return; }
    // Re-rendering identical innerHTML every poll causes a visible flash/repaint;
    // only rebuild the DOM when the payload actually changed.
    const sig = JSON.stringify(data);
    if (sig === lastCompareSig) return;
    lastCompareSig = sig;
    const rows = data.strategies || [];
    const leader = rows.reduce((best, r) => (!best || r.equity > best.equity ? r : best), null);

    // strategy cards
    $("cmpCards").innerHTML = rows.map((r) => {
      const c = stratColor[r.key] || "var(--cool)";
      const isLeader = leader && r.key === leader.key && rows.length > 1;
      const brier = r.brier_score == null ? "—" : r.brier_score.toFixed(3);
      return `<article class="cmp-card${isLeader ? " leader" : ""}" style="--accent:${c}">
        <div class="cc-head">
          <span class="cc-dot" style="background:${c}"></span>
          <span class="cc-name">${r.name}</span>
          ${isLeader ? `<span class="cc-crown">▲ leading</span>` : ""}
        </div>
        <div class="cc-equity">${usd(r.equity)}</div>
        <div class="cc-ret ${cls(r.return_pct)}">${r.return_pct > 0 ? "+" : ""}${r.return_pct}% vs. start</div>
        <div class="cc-grid">
          <div><dt>win rate</dt><dd>${r.win_rate}%</dd></div>
          <div><dt>closed</dt><dd>${r.trades_closed}</dd></div>
          <div><dt>open</dt><dd>${r.open_count}</dd></div>
          <div><dt>max dd</dt><dd>${r.max_drawdown_pct}%</dd></div>
          <div><dt>realized</dt><dd class="${cls(r.realized_pnl)}">${usd(r.realized_pnl)}</dd></div>
          <div><dt>brier</dt><dd>${brier}</dd></div>
        </div>
        <div class="cc-blurb">${r.blurb}</div>
      </article>`;
    }).join("");

    // scoreboard table
    const body = $("cmpBody");
    if (!rows.length) {
      body.innerHTML = `<tr class="empty-row"><td colspan="7">no data yet</td></tr>`;
    } else {
      body.innerHTML = rows
        .slice().sort((a, b) => b.equity - a.equity)
        .map((r) => {
          const c = stratColor[r.key] || "var(--cool)";
          const isLeader = leader && r.key === leader.key && rows.length > 1;
          const brier = r.brier_score == null ? "—" : r.brier_score.toFixed(3);
          return `<tr class="${isLeader ? "lead-row" : ""}" style="--accent:${c}">
            <td class="market"><span class="st-dot" style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${c};margin-right:7px"></span>${r.name}</td>
            <td class="num">${usd(r.equity)}</td>
            <td class="num ${cls(r.return_pct)}">${r.return_pct > 0 ? "+" : ""}${r.return_pct}%</td>
            <td class="num">${r.win_rate}%</td>
            <td class="num">${r.trades_closed}</td>
            <td class="num">${r.max_drawdown_pct}%</td>
            <td class="num">${brier}</td>
          </tr>`;
        }).join("");
    }

    renderCompareChart(data);
  }

  function renderCompareChart(data) {
    const svg = $("compareChart");
    const wrap = svg.closest(".chart-wrap");
    const series = data.equity || {};
    const keys = Object.keys(series).filter((k) => (series[k] || []).length >= 2);

    if (!keys.length) { wrap.classList.add("empty"); svg.innerHTML = ""; $("cmpLegend").innerHTML = ""; return; }
    wrap.classList.remove("empty");

    const { W, H } = fitViewBox(svg, 800, 280);
    const pad = { l: 8, r: 56, t: 16, b: 22 };
    const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
    const start = data.starting_balance;

    let lo = Infinity, hi = -Infinity, maxLen = 0;
    for (const k of keys) {
      for (const p of series[k]) { lo = Math.min(lo, p.equity); hi = Math.max(hi, p.equity); }
      maxLen = Math.max(maxLen, series[k].length);
    }
    if (start != null) { lo = Math.min(lo, start); hi = Math.max(hi, start); }
    if (hi === lo) { hi += 1; lo -= 1; }
    const padv = (hi - lo) * 0.12; lo -= padv; hi += padv;

    const x = (i, n) => pad.l + (n <= 1 ? 0 : (i / (n - 1)) * iw);
    const y = (v) => pad.t + (1 - (v - lo) / (hi - lo)) * ih;

    let grid = "";
    const ticks = 4;
    for (let i = 0; i <= ticks; i++) {
      const v = lo + ((hi - lo) * i) / ticks;
      const gy = y(v).toFixed(1);
      grid += `<line class="eq-grid" x1="${pad.l}" y1="${gy}" x2="${pad.l + iw}" y2="${gy}"/>`;
      grid += `<text class="eq-label" x="${pad.l + iw + 6}" y="${(+gy + 4).toFixed(1)}">$${v.toFixed(0)}</text>`;
    }
    let base = "";
    if (start != null && start >= lo && start <= hi) {
      const by = y(start).toFixed(1);
      base = `<line class="eq-base" x1="${pad.l}" y1="${by}" x2="${pad.l + iw}" y2="${by}"/>`;
    }

    let lines = "";
    for (const k of keys) {
      const pts = series[k].map((p) => p.equity);
      const c = stratColor[k] || "var(--cool)";
      const path = pts.map((v, i) => `${i ? "L" : "M"}${x(i, pts.length).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
      const lx = x(pts.length - 1, pts.length).toFixed(1), ly = y(pts[pts.length - 1]).toFixed(1);
      lines += `<path class="eq-line multi" style="stroke:${c}" d="${path}"/>
        <circle cx="${lx}" cy="${ly}" r="3.2" style="fill:${c}"/>`;
    }

    svg.innerHTML = `${grid}${base}${lines}`;
    $("cmpChartMeta").textContent = `${maxLen} snapshots`;
    $("cmpLegend").innerHTML = keys.map((k) => {
      const r = (data.strategies || []).find((s) => s.key === k);
      return `<span class="lg"><span class="sw" style="background:${stratColor[k]}"></span>${r ? r.name : k}</span>`;
    }).join("");
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
      refreshCompare();
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

  // Refit charts to the new pixel size on resize (the compare poll otherwise
  // skips re-rendering when the data hasn't changed).
  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      lastCompareSig = null;
      if (isCompare()) refreshCompare();
      else refreshEquity();
    }, 150);
  });

  function poll(fn, ms) { fn(); setInterval(fn, ms); }
  initStrategies();
  poll(refreshStatus, 3000);
  poll(refreshReport, 6000);
  poll(refreshEquity, 6000);
  poll(refreshCompare, 3000);
  poll(refreshLogs, 1500);
})();
