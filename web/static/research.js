let currentSymbol = "";
let currentReport = null;
let currentChartInterval = "1d";
const reportCharts = new Map();
let priceChart = null;
const FUNDAMENTAL_SKIP = new Set(["description", "sector", "industry"]);

function labelize(key) {
  return String(key)
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function toNumber(value) {
  if (value == null || value === "") return null;
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  const n = Number(String(value).replace(/[%$,]/g, ""));
  return Number.isFinite(n) ? n : null;
}

function formatCompact(value) {
  if (value == null || value === "") return t("common.emDash");
  const n = toNumber(value);
  if (n == null) return String(value);
  const abs = Math.abs(n);
  if (abs >= 1e12) return `${(n / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (abs >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return String(Number(n.toFixed(4)));
}

function formatPct(value) {
  const n = toNumber(value);
  if (n == null) return t("common.emDash");
  const pct = Math.abs(n) <= 1 && Math.abs(n) > 0 ? n * 100 : n;
  return `${pct.toFixed(2)}%`;
}

function destroyReportCharts() {
  reportCharts.forEach((chart) => chart.destroy());
  reportCharts.clear();
  if (priceChart) {
    priceChart.remove();
    priceChart = null;
  }
}

function tradingViewSymbol(symbol) {
  return String(symbol || "").toUpperCase().replace(/-/g, "");
}

function barChange(bars) {
  if (!bars || bars.length < 2) return null;
  const first = toNumber(bars[0].close);
  const last = toNumber(bars[bars.length - 1].close);
  if (first == null || last == null || first === 0) return null;
  return ((last - first) / first) * 100;
}

function smaValue(indicators) {
  if (!indicators) return null;
  return toNumber(indicators.sma_20 ?? indicators.sma ?? indicators.sma_50);
}

function renderPriceChart(symbol, barsPayload, indicators) {
  const el = document.getElementById("report-price-chart");
  const link = document.getElementById("tv-link");
  const providerEl = document.getElementById("chart-provider");
  if (link) {
    const tv = tradingViewSymbol(symbol);
    link.href = `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(tv)}`;
    link.textContent = t("research.openTradingView");
    link.hidden = false;
  }
  const provider = barsPayload?.provider;
  if (providerEl) {
    if (provider) {
      providerEl.textContent = String(provider);
      providerEl.classList.remove("d-none");
    } else {
      providerEl.textContent = "";
      providerEl.classList.add("d-none");
    }
  }
  if (!el) return;
  if (priceChart) {
    priceChart.remove();
    priceChart = null;
  }
  const bars = (barsPayload && barsPayload.bars) || [];
  if (!bars.length || typeof LightweightCharts === "undefined") {
    emptyNote("report-price-chart");
    return;
  }
  el.innerHTML = "";
  const hourly = currentChartInterval === "1h";
  priceChart = LightweightCharts.createChart(el, {
    layout: { background: { color: "transparent" }, textColor: "#8b9cb3" },
    grid: {
      vertLines: { color: "rgba(255,255,255,0.06)" },
      horzLines: { color: "rgba(255,255,255,0.06)" },
    },
    rightPriceScale: { borderColor: "#2d3a4d" },
    timeScale: { borderColor: "#2d3a4d", timeVisible: hourly, secondsVisible: false },
    width: el.clientWidth || el.parentElement?.clientWidth || 640,
    height: 420,
  });
  const series = priceChart.addCandlestickSeries({
    upColor: "#00c805",
    downColor: "#ff5a5f",
    borderUpColor: "#00c805",
    borderDownColor: "#ff5a5f",
    wickUpColor: "#00c805",
    wickDownColor: "#ff5a5f",
  });
  series.setData(bars);
  const sma = smaValue(indicators);
  if (sma != null) {
    series.createPriceLine({
      price: sma,
      color: "#6ea8fe",
      lineWidth: 1,
      lineStyle: 2,
      axisLabelVisible: true,
      title: t("research.sma"),
    });
  }
  priceChart.timeScale().fitContent();
}

function setChartInterval(interval) {
  currentChartInterval = interval === "1h" ? "1h" : "1d";
  document.getElementById("chart-int-1h")?.classList.toggle("active", currentChartInterval === "1h");
  document.getElementById("chart-int-1d")?.classList.toggle("active", currentChartInterval === "1d");
  if (currentSymbol) loadChartBars(currentSymbol, currentChartInterval);
}

async function loadChartBars(symbol, interval) {
  try {
    const chart = await api(
      `/api/research/charts/${encodeURIComponent(symbol)}?interval=${encodeURIComponent(interval)}`
    );
    if (!chart.ok) throw new Error(chart.error || t("research.noData"));
    renderPriceChart(symbol, chart, currentReport?.indicators);
  } catch (err) {
    emptyNote("report-price-chart");
    const status = document.getElementById("report-status");
    if (status) status.textContent = String(err.message || err);
  }
}

function chartOrEmpty(target, html) {
  const el = document.getElementById(target);
  if (!el) return null;
  el.innerHTML = html;
  return el;
}

function emptyNote(target) {
  chartOrEmpty(target, `<p class="text-secondary small mb-0">${escapeHtml(t("research.noData"))}</p>`);
}

function metricCard(label, value) {
  return `<div class="col-6 col-md-4 col-xl-2">
    <div class="metric-card">
      <div class="metric-label">${escapeHtml(label)}</div>
      <div class="metric-value">${escapeHtml(value)}</div>
    </div>
  </div>`;
}

function renderHero(symbol, fundamentals, book, bars) {
  const el = document.getElementById("report-hero");
  if (!el) return;
  const chips = [fundamentals?.sector, fundamentals?.industry].filter(Boolean);
  const lastBar = bars?.bars?.length ? bars.bars[bars.bars.length - 1] : null;
  const last = book?.last ?? lastBar?.close ?? fundamentals?.open;
  const chg = barChange(bars?.bars);
  const chgCls = chg == null ? "text-secondary" : chg >= 0 ? "text-success" : "text-danger";
  const chgTxt = chg == null ? t("common.emDash") : `${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%`;
  el.innerHTML = `
    <div class="d-flex flex-wrap align-items-center gap-2 mb-2">
      <div class="fs-4 fw-semibold">${escapeHtml(symbol)}</div>
      ${chips.map((c) => `<span class="chip">${escapeHtml(c)}</span>`).join("")}
    </div>
    <div class="row g-2">
      ${metricCard(t("research.last"), formatCompact(last))}
      ${metricCard(t("research.change"), chgTxt)}
      ${metricCard(t("research.bid"), formatCompact(book?.bid))}
      ${metricCard(t("research.ask"), formatCompact(book?.ask))}
      ${metricCard(t("research.volume"), formatCompact(fundamentals?.volume || fundamentals?.average_volume))}
      ${metricCard(t("research.week52"), `${formatCompact(fundamentals?.low_52_weeks)} – ${formatCompact(fundamentals?.high_52_weeks)}`)}
    </div>
    ${fundamentals?.description ? `<p class="small text-secondary mt-3 mb-0">${escapeHtml(fundamentals.description)}</p>` : ""}
  `;
  const changeEl = el.querySelectorAll(".metric-value")[1];
  if (changeEl) changeEl.classList.add(chgCls);
}

const FUND_LABELS = {
  market_cap: "research.marketCap",
  pe_ratio: "research.peRatio",
  pb_ratio: "research.pbRatio",
  dividend_yield: "research.divYield",
  volume: "research.volume",
  average_volume: "research.volume",
};

function renderStats(fundamentals) {
  const el = document.getElementById("report-stats");
  if (!el) return;
  if (!fundamentals || !Object.keys(fundamentals).length) {
    emptyNote("report-stats");
    return;
  }
  const cards = Object.entries(fundamentals)
    .filter(([k]) => !FUNDAMENTAL_SKIP.has(k))
    .map(([k, v]) => {
      const label = FUND_LABELS[k] ? t(FUND_LABELS[k]) : labelize(k);
      const formatted = k === "dividend_yield" ? formatPct(v) : formatCompact(v);
      return metricCard(label, formatted);
    });
  el.innerHTML = cards.join("") || `<p class="text-secondary small mb-0">${escapeHtml(t("research.noData"))}</p>`;
}

function renderRange(fundamentals, book) {
  const el = document.getElementById("report-range");
  if (!el) return;
  const low = toNumber(fundamentals?.low_52_weeks);
  const high = toNumber(fundamentals?.high_52_weeks);
  const last = toNumber(book?.last ?? fundamentals?.open);
  if (low == null || high == null || high <= low) {
    el.innerHTML = "";
    return;
  }
  const pct = last == null ? 0 : Math.min(100, Math.max(0, ((last - low) / (high - low)) * 100));
  el.innerHTML = `
    <div class="stat-label mb-2">${escapeHtml(t("research.week52"))}</div>
    <div class="d-flex justify-content-between small text-secondary mb-1">
      <span>${escapeHtml(formatCompact(low))}</span>
      <span>${escapeHtml(formatCompact(high))}</span>
    </div>
    <div class="range-track">
      <div class="range-fill" style="width:${pct}%"></div>
      <div class="range-marker" style="left:${pct}%"></div>
    </div>
  `;
}

function makeBarChart(canvasId, labels, datasets) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || typeof Chart === "undefined") return;
  if (reportCharts.has(canvasId)) reportCharts.get(canvasId).destroy();
  const chart = new Chart(canvas, {
    type: "bar",
    data: { labels, datasets },
    options: {
      plugins: { legend: { labels: { color: "#adb5bd", boxWidth: 10 } } },
      scales: {
        x: { ticks: { color: "#8b9cb3" }, grid: { color: "rgba(255,255,255,0.06)" } },
        y: { ticks: { color: "#8b9cb3" }, grid: { color: "rgba(255,255,255,0.06)" } },
      },
      maintainAspectRatio: false,
    },
  });
  reportCharts.set(canvasId, chart);
}

function periodLabel(row, index) {
  return (
    row.period ||
    row.fiscal_period ||
    (row.year && row.quarter ? `${row.year} Q${row.quarter}` : "") ||
    (row.fiscal_year && row.fiscal_quarter ? `${row.fiscal_year} Q${row.fiscal_quarter}` : "") ||
    row.report_date ||
    row.period_end_date ||
    row.date ||
    `#${index + 1}`
  );
}

function renderEarnings(rows) {
  if (!rows || !rows.length) {
    emptyNote("report-earnings");
    return;
  }
  chartOrEmpty(
    "report-earnings",
    `<div class="research-chart"><canvas id="earnings-chart"></canvas></div>`
  );
  makeBarChart(
    "earnings-chart",
    rows.map(periodLabel),
    [
      {
        label: "EPS actual",
        data: rows.map((r) => toNumber(r.eps_actual)),
        backgroundColor: "#00c805",
      },
      {
        label: "EPS estimate",
        data: rows.map((r) => toNumber(r.eps_estimate)),
        backgroundColor: "#6c757d",
      },
    ]
  );
}

function renderFinancials(rows) {
  if (!rows || !rows.length) {
    emptyNote("report-financials");
    return;
  }
  const keys = ["revenue", "gross_profit", "net_income", "operating_income", "eps", "free_cash_flow"];
  const tableHead = `<th></th>${rows.map((r, i) => `<th>${escapeHtml(periodLabel(r, i))}</th>`).join("")}`;
  const tableBody = keys
    .map((key) => {
      const cells = rows.map((r) => `<td>${escapeHtml(formatCompact(r[key]))}</td>`).join("");
      if (!rows.some((r) => r[key] != null && r[key] !== "")) return "";
      return `<tr><th class="text-secondary small">${escapeHtml(labelize(key))}</th>${cells}</tr>`;
    })
    .join("");
  chartOrEmpty(
    "report-financials",
    `<div class="table-responsive mb-3">
      <table class="table table-sm table-dark mb-0 align-middle">
        <thead><tr>${tableHead}</tr></thead>
        <tbody>${tableBody}</tbody>
      </table>
    </div>
    <div class="research-chart"><canvas id="financials-chart"></canvas></div>`
  );
  makeBarChart(
    "financials-chart",
    rows.map(periodLabel),
    [
      {
        label: "Revenue",
        data: rows.map((r) => toNumber(r.revenue)),
        backgroundColor: "#0dcaf0",
      },
      {
        label: "Net income",
        data: rows.map((r) => toNumber(r.net_income ?? r.gross_profit)),
        backgroundColor: "#00c805",
      },
    ]
  );
}

function renderRatings(ratings) {
  const el = document.getElementById("report-ratings");
  if (!el) return;
  if (!ratings || !Object.keys(ratings).length) {
    emptyNote("report-ratings");
    return;
  }
  const buy = toNumber(ratings.num_buy_ratings) || 0;
  const hold = toNumber(ratings.num_hold_ratings) || 0;
  const sell = toNumber(ratings.num_sell_ratings) || 0;
  const hasCounts = buy + hold + sell > 0;
  el.innerHTML = `
    <div class="row g-2">
      ${hasCounts ? `<div class="col-md-6"><div class="research-chart"><canvas id="ratings-chart"></canvas></div></div>` : ""}
      <div class="${hasCounts ? "col-md-6" : "col-12"}">
        <div class="row g-2">
          ${metricCard(t("research.highTarget"), formatCompact(ratings.high_price_target))}
          ${metricCard(t("research.meanTarget"), formatCompact(ratings.mean_price_target || ratings.target_price))}
          ${metricCard(t("research.lowTarget"), formatCompact(ratings.low_price_target))}
        </div>
        ${ratings.summary ? `<p class="small text-secondary mt-2 mb-0">${escapeHtml(ratings.summary)}</p>` : ""}
      </div>
    </div>
  `;
  if (hasCounts && typeof Chart !== "undefined") {
    const canvas = document.getElementById("ratings-chart");
    if (canvas) {
      if (reportCharts.has("ratings-chart")) reportCharts.get("ratings-chart").destroy();
      reportCharts.set(
        "ratings-chart",
        new Chart(canvas, {
          type: "doughnut",
          data: {
            labels: [t("research.buy"), t("research.hold"), t("research.sell")],
            datasets: [{ data: [buy, hold, sell], backgroundColor: ["#00c805", "#ffc107", "#ff5000"] }],
          },
          options: {
            plugins: { legend: { position: "bottom", labels: { color: "#adb5bd", boxWidth: 10 } } },
            maintainAspectRatio: false,
          },
        })
      );
    }
  }
}

function renderIndicators(indicators) {
  const el = document.getElementById("report-indicators");
  if (!el) return;
  if (!indicators || !Object.keys(indicators).length) {
    emptyNote("report-indicators");
    return;
  }
  const cards = [];
  const rsi = indicators.rsi ?? indicators.rsi_14;
  if (rsi != null) cards.push(metricCard(t("research.rsi"), formatCompact(rsi)));
  const macd = indicators.macd ?? indicators.macd_line;
  if (macd != null) cards.push(metricCard(t("research.macd"), formatCompact(macd)));
  const sma = indicators.sma_20 ?? indicators.sma ?? indicators.sma_50;
  if (sma != null) cards.push(metricCard(t("research.sma"), formatCompact(sma)));
  const extras = Object.entries(indicators).filter(
    ([k]) => !["rsi", "rsi_14", "macd", "macd_line", "sma_20", "sma", "sma_50"].includes(k)
  );
  extras.slice(0, 8).forEach(([k, v]) => cards.push(metricCard(labelize(k), formatCompact(v))));
  el.innerHTML = cards.length
    ? `<div class="row g-2">${cards.join("")}</div>`
    : `<p class="text-secondary small mb-0">${escapeHtml(t("research.noData"))}</p>`;
}

function renderNews(target, rows) {
  const el = document.getElementById(target);
  if (!el) return;
  if (!rows || !rows.length) {
    el.innerHTML = `<p class="text-secondary small mb-0">${escapeHtml(t("research.noData"))}</p>`;
    return;
  }
  el.innerHTML = rows
    .map((n) => {
      const title = escapeHtml(n.title || "");
      const heading = n.url
        ? `<a href="${escapeHtml(n.url)}" target="_blank" rel="noopener">${title}</a>`
        : title;
      return `<div class="metric-card mb-2">
        <div class="small fw-semibold">${heading}</div>
        <div class="text-secondary small">${escapeHtml([n.source, n.published_at].filter(Boolean).join(" · "))}</div>
        ${n.summary ? `<div class="small mt-1">${escapeHtml(n.summary)}</div>` : ""}
      </div>`;
    })
    .join("");
}

function renderFilings(rows) {
  const el = document.getElementById("report-filings");
  if (!el) return;
  if (!rows || !rows.length) {
    el.innerHTML = `<p class="text-secondary small mb-0">${escapeHtml(t("research.noFilings"))}</p>`;
    return;
  }
  el.innerHTML = rows
    .map((f) => {
      const title = escapeHtml(f.title || t("research.filings"));
      const heading = f.url
        ? `<a href="${escapeHtml(f.url)}" target="_blank" rel="noopener">${title}</a>`
        : title;
      return `<div class="d-flex justify-content-between gap-2 py-1 border-bottom border-secondary-subtle">
        <div class="small fw-semibold">${heading}</div>
        <div class="text-secondary small text-nowrap">${escapeHtml(f.filed_at || "")}</div>
      </div>`;
    })
    .join("");
}

function watchlistHref(row) {
  const id = row.id || row.name || "";
  return `/research/watchlists/${encodeURIComponent(id)}`;
}

function watchlistCard(row) {
  const count = row.item_count ?? (row.symbols || []).length;
  const countLabel = count ? t("research.symbolsCount", { count }) : "";
  return `<div class="col-sm-6">
    <a class="watchlist-link-card" href="${escapeHtml(watchlistHref(row))}">
      <div class="fw-semibold small">${escapeHtml(row.icon_emoji ? `${row.icon_emoji} ` : "")}${escapeHtml(row.name || row.id || "")}</div>
      <div class="text-secondary small">${escapeHtml(countLabel)}</div>
    </a>
  </div>`;
}

async function runSearch() {
  const query = document.getElementById("research-query")?.value.trim();
  if (!query) return;
  const el = document.getElementById("search-results");
  el.innerHTML = `<span class="text-secondary small">${escapeHtml(t("common.loading"))}</span>`;
  try {
    const { results } = await api(`/api/research/search?q=${encodeURIComponent(query)}`);
    el.innerHTML = results.length
      ? results
          .map(
            (r) =>
              `<button type="button" class="btn btn-outline-secondary btn-sm" onclick="loadReport('${escapeHtml(r.symbol)}')">${escapeHtml(r.symbol)}${r.name ? ` <span class="text-secondary">${escapeHtml(r.name)}</span>` : ""}</button>`
          )
          .join("")
      : `<span class="text-secondary small">${escapeHtml(t("research.noMatches"))}</span>`;
  } catch (err) {
    el.innerHTML = `<span class="text-danger small">${escapeHtml(String(err.message || err))}</span>`;
  }
}

async function runReport() {
  const query = document.getElementById("research-query")?.value.trim();
  if (query) await loadReport(query.toUpperCase());
}

async function loadReport(symbol) {
  currentSymbol = symbol;
  currentChartInterval = "1d";
  document.getElementById("chart-int-1h")?.classList.remove("active");
  document.getElementById("chart-int-1d")?.classList.add("active");
  const params = new URLSearchParams(location.search);
  if (params.get("symbol") !== symbol) {
    params.set("symbol", symbol);
    history.replaceState(null, "", `${location.pathname}?${params.toString()}`);
  }
  const input = document.getElementById("research-query");
  if (input && !input.value) input.value = symbol;
  document.getElementById("report-area")?.classList.remove("d-none");
  document.getElementById("report-symbol").textContent = symbol;
  const status = document.getElementById("report-status");
  if (status) status.textContent = t("common.loading");
  destroyReportCharts();

  try {
    const report = await api(`/api/research/${encodeURIComponent(symbol)}`);
    if (!report.ok) throw new Error(report.error || t("research.reportFailed"));
    currentReport = report;
    renderHero(symbol, report.fundamentals, report.price_book, report.bars);
    renderPriceChart(symbol, report.bars, report.indicators);
    renderStats(report.fundamentals);
    renderRange(report.fundamentals, report.price_book);
    renderEarnings(report.earnings);
    renderFinancials(report.financials);
    renderRatings(report.analyst_ratings);
    renderIndicators(report.indicators);
    renderNews("report-news", report.news);
    renderFilings(report.filings);
    if (status) status.textContent = "";
  } catch (err) {
    currentReport = null;
    if (status) status.textContent = String(err.message || err);
  }
}

async function loadScans() {
  const el = document.getElementById("scans-list");
  if (!el) return;
  try {
    const { scans } = await api("/api/scans");
    el.innerHTML = scans.length
      ? scans
          .map(
            (s) => `<div class="d-flex justify-content-between align-items-center gap-2 py-1 border-bottom border-secondary-subtle">
              <span class="small">${escapeHtml(s.name || s.id)}</span>
              <button class="btn btn-outline-secondary btn-sm py-0" onclick="runScan('${escapeHtml(s.id)}')">${escapeHtml(t("research.run"))}</button>
            </div>`
          )
          .join("")
      : `<p class="text-secondary small mb-0">${escapeHtml(t("research.noScans"))}</p>`;
  } catch (err) {
    el.innerHTML = `<p class="text-danger small mb-0">${escapeHtml(String(err.message || err))}</p>`;
  }
}

async function createScan() {
  const name = document.getElementById("scan-name")?.value.trim();
  if (!name) return toast(t("research.scanNameRequired"));
  try {
    await api("/api/scans", { method: "POST", body: JSON.stringify({ name, filters: [] }) });
    document.getElementById("scan-name").value = "";
    toast(t("research.scanCreated"));
    loadScans();
  } catch (err) {
    toast(String(err.message || err));
  }
}

async function runScan(scanId) {
  const el = document.getElementById("scan-results");
  if (el) el.textContent = t("common.loading");
  try {
    const { symbols } = await api(`/api/scans/${encodeURIComponent(scanId)}/run`, {
      method: "POST",
      body: "{}",
    });
    if (!el) return;
    el.innerHTML = symbols.length
      ? symbols
          .map(
            (s) =>
              `<button type="button" class="btn btn-outline-secondary btn-sm me-1 mb-1" onclick="loadReport('${escapeHtml(s)}')">${escapeHtml(s)}</button>`
          )
          .join("")
      : escapeHtml(t("research.noMatches"));
  } catch (err) {
    if (el) el.textContent = String(err.message || err);
  }
}

async function loadWatchlists() {
  const el = document.getElementById("research-watchlists");
  if (!el) return;
  try {
    const catalog = await api("/api/watchlists/catalog").catch(async () => {
      const [mine, popular] = await Promise.all([
        api("/api/watchlists").catch(() => ({ watchlists: [] })),
        api("/api/watchlists/popular").catch(() => ({ watchlists: [] })),
      ]);
      return { yours: mine.watchlists || [], robinhood: popular.watchlists || [] };
    });
    const sections = [
      [t("research.myWatchlists"), t("research.yoursHint"), catalog.yours || []],
      [t("research.popularWatchlists"), t("research.robinhoodHint"), catalog.robinhood || []],
    ];
    el.innerHTML = sections
      .map(([heading, hint, rows]) => {
        const body = rows.length
          ? `<div class="row g-2">${rows.map(watchlistCard).join("")}</div>`
          : `<p class="text-secondary small mb-0">${escapeHtml(t("research.noData"))}</p>`;
        return `<div class="mb-4">
          <div class="stat-label mb-1">${escapeHtml(heading)}</div>
          <p class="text-secondary small mb-2">${escapeHtml(hint)}</p>
          ${body}
        </div>`;
      })
      .join("");
  } catch (err) {
    el.innerHTML = `<p class="text-danger small mb-0">${escapeHtml(String(err.message || err))}</p>`;
  }
}

document.addEventListener("i18n:ready", () => {
  loadScans();
  loadWatchlists();
  document.getElementById("research-query")?.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") runReport();
  });
  const preset = new URLSearchParams(location.search).get("symbol");
  if (preset) loadReport(preset.toUpperCase());
});
