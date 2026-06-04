/** Statistics dashboard */

const charts = new Map();
let lastData = null;

function setView(mode) {
  document.getElementById("cards-view").classList.toggle("d-none", mode !== "cards");
  document.getElementById("table-view").classList.toggle("d-none", mode !== "table");
  document.getElementById("view-cards").classList.toggle("active", mode === "cards");
  document.getElementById("view-table").classList.toggle("active", mode === "table");
}

function kpiCard(title, value, sub) {
  return `
    <div class="col-6 col-lg-3">
      <div class="rh-card p-3 h-100">
        <div class="text-secondary small">${escapeHtml(title)}</div>
        <div class="fs-4 fw-semibold">${escapeHtml(String(value))}</div>
        ${sub ? `<div class="small text-secondary">${escapeHtml(sub)}</div>` : ""}
      </div>
    </div>`;
}

function actionDonut(canvasId, actions) {
  const labels = Object.keys(actions);
  const values = Object.values(actions);
  if (!labels.length) return null;
  const ctx = document.getElementById(canvasId);
  if (!ctx) return null;
  if (charts.has(canvasId)) charts.get(canvasId).destroy();
  const chart = new Chart(ctx, {
    type: "doughnut",
    data: {
      labels,
      datasets: [
        {
          data: values,
          backgroundColor: ["#00c805", "#ff5000", "#6c757d", "#ffc107", "#0dcaf0"],
        },
      ],
    },
    options: {
      plugins: { legend: { position: "bottom", labels: { color: "#adb5bd", boxWidth: 10 } } },
      maintainAspectRatio: false,
    },
  });
  charts.set(canvasId, chart);
  return chart;
}

function sparkline(canvasId, series) {
  const ctx = document.getElementById(canvasId);
  if (!ctx) return null;
  if (charts.has(canvasId)) charts.get(canvasId).destroy();
  const labels = series.map((p) => p.ts?.slice(5, 16) || "");
  const values = series.map((p) => p.value);
  const chart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          data: values,
          borderColor: "#00c805",
          backgroundColor: "rgba(0,200,5,0.12)",
          fill: true,
          tension: 0.3,
          pointRadius: 0,
        },
      ],
    },
    options: {
      plugins: { legend: { display: false } },
      scales: {
        x: { display: false },
        y: { ticks: { color: "#6c757d" }, grid: { color: "rgba(255,255,255,0.06)" } },
      },
      maintainAspectRatio: false,
    },
  });
  charts.set(canvasId, chart);
  return chart;
}

function renderAggregate(agg) {
  document.getElementById("aggregate-kpis").innerHTML = [
    kpiCard(t("stats.aggregateBots"), agg.total_bots, t("stats.aggregateBotsSub")),
    kpiCard(t("stats.aggregateRunsToday"), agg.runs_today, t("stats.aggregateRunsTotalSub", { total: agg.total_runs })),
    kpiCard(t("stats.aggregateTradesPlaced"), agg.total_trades, t("stats.aggregateTradesSub")),
    kpiCard(t("stats.aggregateCombinedPortfolio"), `$${Number(agg.combined_portfolio || 0).toFixed(2)}`, t("stats.aggregatePortfolioSub")),
  ].join("");
}

function renderCards(bots) {
  const grid = document.getElementById("cards-view");
  grid.innerHTML = "";
  bots.forEach((b) => {
    const s = b.stats || {};
    const sched = b.scheduler || {};
    const col = document.createElement("div");
    col.className = "col-md-6 col-xl-4";
    const sparkId = `spark-${b.id}`;
    const donutId = `donut-${b.id}`;
    const pv =
      s.latest_portfolio_value != null
        ? `$${Number(s.latest_portfolio_value).toFixed(2)}`
        : t("common.emDash");

    col.innerHTML = `
      <div class="rh-card h-100">
        <div class="rh-card-head d-flex justify-content-between">
          <strong>${escapeHtml(b.name)}</strong>
          <span class="badge ${schedulerBadgeClass(sched)}">${escapeHtml(schedulerStatusLabel(sched))}</span>
        </div>
        <div class="p-3">
          <div class="row g-2 mb-2 small">
            <div class="col-4"><div class="text-secondary">${escapeHtml(t("common.runs"))}</div><div class="fw-semibold">${s.total_runs ?? 0}</div></div>
            <div class="col-4"><div class="text-secondary">${escapeHtml(t("common.success"))}</div><div class="fw-semibold">${s.success_rate ?? 0}%</div></div>
            <div class="col-4"><div class="text-secondary">${escapeHtml(t("common.trades"))}</div><div class="fw-semibold">${s.trades_placed ?? 0}</div></div>
          </div>
          <div class="mb-2"><span class="text-secondary small">${escapeHtml(t("stats.tablePortfolio"))} </span><strong>${escapeHtml(pv)}</strong></div>
          <div style="height: 72px;" class="mb-2"><canvas id="${sparkId}"></canvas></div>
          <div style="height: 120px;"><canvas id="${donutId}"></canvas></div>
          <div class="mt-2">
            <a href="${botPath(b.id)}" class="btn btn-outline-secondary btn-sm">${escapeHtml(t("stats.cardDashboardLink"))}</a>
          </div>
        </div>
      </div>`;
    grid.appendChild(col);
    sparkline(sparkId, s.portfolio_series || []);
    actionDonut(donutId, s.actions || {});
  });
}

function renderTable(bots) {
  const tbody = document.getElementById("stats-table-body");
  tbody.innerHTML = bots
    .map((b) => {
      const s = b.stats || {};
      const sched = b.scheduler || {};
      const pv =
        s.latest_portfolio_value != null
          ? `$${Number(s.latest_portfolio_value).toFixed(2)}`
          : t("common.emDash");
      return `<tr>
        <td><a href="${botPath(b.id)}" class="link-light">${escapeHtml(b.name)}</a></td>
        <td><span class="badge ${schedulerBadgeClass(sched)}">${escapeHtml(schedulerStatusLabel(sched))}</span></td>
        <td>${s.total_runs ?? 0}</td>
        <td>${s.success_rate ?? 0}%</td>
        <td>${s.trades_placed ?? 0}</td>
        <td>${escapeHtml(pv)}</td>
        <td class="text-secondary small">${s.last_run_at ? escapeHtml(formatRunTime(s.last_run_at)) : t("common.emDash")}</td>
      </tr>`;
    })
    .join("");
}

async function refresh() {
  const data = await api("/api/statistics");
  lastData = data;
  renderAggregate(data.aggregate || {});
  renderCards(data.bots || []);
  renderTable(data.bots || []);
}

const debounced = debounce(refresh, 2000);
connectWs((msg) => {
  if (msg.type === "agent_event") debounced();
});

whenI18nReady(() => {
  refresh();
  setInterval(refresh, 30000);
});
