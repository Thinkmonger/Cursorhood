const WL_PAGE_SIZE = 16;
let wlSymbols = [];
let wlBars = {};
let wlPage = 0;
let wlCharts = [];

function watchlistIdFromPath() {
  const parts = location.pathname.split("/").filter(Boolean);
  const idx = parts.lastIndexOf("watchlists");
  return idx >= 0 && parts[idx + 1] ? decodeURIComponent(parts[idx + 1]) : "";
}

function formatCompact(value) {
  if (value == null || value === "") return t("common.emDash");
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  const abs = Math.abs(n);
  if (abs >= 1e12) return `${(n / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return String(Number(n.toFixed(4)));
}

function barChangePct(bars) {
  if (!bars || bars.length < 2) return null;
  const first = Number(bars[0].close);
  const last = Number(bars[bars.length - 1].close);
  if (!Number.isFinite(first) || !Number.isFinite(last) || first === 0) return null;
  return ((last - first) / first) * 100;
}

function cssId(symbol) {
  return String(symbol).replace(/[^a-zA-Z0-9_-]/g, "_");
}

function disposeWlCharts() {
  wlCharts.forEach((c) => {
    try {
      c.remove();
    } catch (_) {}
  });
  wlCharts = [];
}

function mountMiniChart(elId, bars) {
  const el = document.getElementById(elId);
  if (!el || !bars?.length || typeof LightweightCharts === "undefined") return;
  el.innerHTML = "";
  const width = el.clientWidth || el.parentElement?.clientWidth || 160;
  if (width < 8) return;
  const chart = LightweightCharts.createChart(el, {
    layout: { background: { color: "transparent" }, textColor: "#8b9cb3" },
    grid: {
      vertLines: { visible: false },
      horzLines: { color: "rgba(255,255,255,0.05)" },
    },
    rightPriceScale: { visible: true, borderVisible: false },
    timeScale: { visible: false, borderVisible: false },
    width,
    height: 140,
    handleScroll: false,
    handleScale: false,
  });
  const series = chart.addCandlestickSeries({
    upColor: "#00c805",
    downColor: "#ff5a5f",
    borderUpColor: "#00c805",
    borderDownColor: "#ff5a5f",
    wickUpColor: "#00c805",
    wickDownColor: "#ff5a5f",
  });
  series.setData(bars);
  chart.timeScale().fitContent();
  wlCharts.push(chart);
}

function visibleSymbols() {
  const start = wlPage * WL_PAGE_SIZE;
  return wlSymbols.slice(start, start + WL_PAGE_SIZE);
}

function updatePager() {
  const pager = document.getElementById("watchlist-pager");
  const pages = Math.max(1, Math.ceil(wlSymbols.length / WL_PAGE_SIZE));
  const show = wlSymbols.length > WL_PAGE_SIZE;
  pager?.classList.toggle("d-none", !show);
  pager?.classList.toggle("d-flex", show);
  const label = document.getElementById("wl-page-label");
  if (label) label.textContent = `${wlPage + 1} / ${pages}`;
  const prev = document.getElementById("wl-prev");
  const next = document.getElementById("wl-next");
  if (prev) prev.disabled = wlPage <= 0;
  if (next) next.disabled = wlPage >= pages - 1;
}

function chartCell(symbol) {
  const sid = cssId(symbol);
  const entry = wlBars[symbol];
  const bars = entry?.bars || [];
  const chg = barChangePct(bars);
  const chgCls = chg == null ? "text-secondary" : chg >= 0 ? "text-success" : "text-danger";
  const chgTxt = chg == null ? "…" : `${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%`;
  let body = "";
  if (!entry) body = `<p class="text-secondary small px-2 mb-0">…</p>`;
  else if (!bars.length) body = `<p class="text-secondary small px-2 mb-0">${escapeHtml(t("research.noData"))}</p>`;
  return `<div class="bot-chart-cell">
    <div class="bot-chart-cell-head">
      <a class="text-decoration-none text-reset fw-semibold small" href="/research?symbol=${encodeURIComponent(symbol)}">${escapeHtml(symbol)}</a>
      <span class="small ${chgCls}">${escapeHtml(chgTxt)}</span>
    </div>
    <div class="bot-chart-mini" id="wl-mini-${sid}">${body}</div>
  </div>`;
}

function renderGrid() {
  const grid = document.getElementById("watchlist-grid");
  if (!grid) return;
  disposeWlCharts();
  updatePager();
  const visible = visibleSymbols();
  if (!visible.length) {
    grid.className = "row g-3";
    grid.innerHTML = `<div class="col-12"><p class="text-secondary small mb-0">${escapeHtml(t("watchlist.empty"))}</p></div>`;
    return;
  }
  grid.className = "bot-chart-grid";
  const cells = visible.map(chartCell);
  const pad = Math.max(0, WL_PAGE_SIZE - cells.length);
  for (let i = 0; i < pad; i += 1) {
    cells.push(`<div class="bot-chart-cell is-empty" aria-hidden="true"></div>`);
  }
  grid.innerHTML = cells.join("");
  requestAnimationFrame(() => {
    for (const symbol of visible) {
      mountMiniChart(`wl-mini-${cssId(symbol)}`, wlBars[symbol]?.bars);
    }
  });
}

async function loadVisibleBars() {
  const missing = visibleSymbols().filter((s) => !wlBars[s]);
  if (!missing.length) {
    renderGrid();
    return;
  }
  renderGrid();
  try {
    const data = await api("/api/research/charts", {
      method: "POST",
      body: JSON.stringify({ symbols: missing, interval: "1d" }),
    });
    const rows = data.symbols || {};
    for (const symbol of missing) {
      wlBars[symbol] = rows[symbol] || { ok: false, bars: [] };
    }
  } catch (_) {
    for (const symbol of missing) {
      if (!wlBars[symbol]) wlBars[symbol] = { ok: false, bars: [] };
    }
  }
  renderGrid();
}

async function loadWatchlistOverview() {
  const id = watchlistIdFromPath();
  const status = document.getElementById("watchlist-status");
  if (!id) {
    if (status) status.textContent = t("watchlist.loadFailed");
    return;
  }
  if (status) status.textContent = t("common.loading");
  try {
    const data = await api(`/api/watchlists/${encodeURIComponent(id)}/overview`);
    if (!data.ok) throw new Error(data.error || t("watchlist.loadFailed"));
    const title = document.getElementById("watchlist-title");
    const origin = document.getElementById("watchlist-origin");
    const count = document.getElementById("watchlist-count");
    const name = `${data.icon_emoji ? `${data.icon_emoji} ` : ""}${data.name || id}`;
    if (title) title.textContent = name;
    document.title = `${data.name || "Watchlist"} — ${t("app.nameShort")}`;
    if (origin) {
      const yours = data.origin === "user";
      origin.textContent = yours ? t("watchlist.yours") : t("watchlist.robinhood");
      origin.className = `badge ${yours ? "text-bg-success" : "text-bg-secondary"}`;
      origin.classList.remove("d-none");
    }
    const holdings = data.holdings || [];
    wlSymbols = holdings.map((h) => h.symbol).filter(Boolean);
    const n = data.item_count ?? wlSymbols.length;
    if (count) count.textContent = t("research.symbolsCount", { count: n });
    if (status) status.textContent = "";
    await loadVisibleBars();
  } catch (err) {
    if (status) status.textContent = String(err.message || err);
  }
}

document.addEventListener("i18n:ready", () => {
  document.getElementById("wl-prev")?.addEventListener("click", () => {
    if (wlPage > 0) {
      wlPage -= 1;
      loadVisibleBars();
    }
  });
  document.getElementById("wl-next")?.addEventListener("click", () => {
    const pages = Math.max(1, Math.ceil(wlSymbols.length / WL_PAGE_SIZE));
    if (wlPage < pages - 1) {
      wlPage += 1;
      loadVisibleBars();
    }
  });
  loadWatchlistOverview();
});
