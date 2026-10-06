/** Statistics console — Overview + Bots */

const charts = new Map();
let lastData = null;
let statsTab = "overview";
let botView = "cards";

function setView(mode) {
  botView = mode;
  document.getElementById("cards-view").classList.toggle("d-none", mode !== "cards");
  document.getElementById("table-view").classList.toggle("d-none", mode !== "table");
  document.getElementById("view-cards").classList.toggle("active", mode === "cards");
  document.getElementById("view-table").classList.toggle("active", mode === "table");
}

function showStatsTab(tab) {
  statsTab = tab === "bots" ? "bots" : "overview";
  document.getElementById("panel-overview").classList.toggle("d-none", statsTab !== "overview");
  document.getElementById("panel-bots").classList.toggle("d-none", statsTab !== "bots");
  document.querySelectorAll("#stats-tabs .nav-link").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === statsTab);
  });
  if (statsTab === "bots" && lastData) {
    requestAnimationFrame(() => renderCards(lastData.bots || []));
  }
}

function kpiCard(title, value, sub, icon) {
  return `
    <div class="col-6 col-lg-3">
      <div class="rh-card p-3 h-100">
        <div class="d-flex justify-content-between align-items-start mb-1">
          <div class="text-secondary small">${escapeHtml(title)}</div>
          ${icon ? `<i class="bi ${icon} text-secondary opacity-75"></i>` : ""}
        </div>
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

function mixLabel(mix) {
  const parts = [];
  const order = [
    ["active", t("scheduler.statusActive")],
    ["paused", t("scheduler.statusPaused")],
    ["stopped", t("scheduler.statusStopped")],
    ["running_cycle", t("scheduler.statusRunningCycle")],
  ];
  for (const [key, label] of order) {
    const n = mix[key] || 0;
    if (n) parts.push(`${n} ${label}`);
  }
  return parts.join(" · ") || t("common.emDash");
}

function statusMixLabel(counts) {
  const parts = Object.entries(counts || {}).map(
    ([status, n]) => `${n} ${runStatusLabel(status)}`
  );
  return parts.join(" · ") || t("common.emDash");
}

function renderAggregate(agg) {
  document.getElementById("aggregate-kpis").innerHTML = [
    kpiCard(t("stats.aggregateBots"), agg.total_bots ?? 0, t("stats.aggregateBotsSub"), "bi-cpu"),
    kpiCard(
      t("stats.aggregateRunsToday"),
      agg.runs_today ?? 0,
      t("stats.aggregateRunsTotalSub", { total: agg.total_runs ?? 0 }),
      "bi-arrow-repeat"
    ),
    kpiCard(t("stats.aggregateTradesPlaced"), agg.total_trades ?? 0, t("stats.aggregateTradesSub"), "bi-graph-up-arrow"),
    kpiCard(
      t("stats.aggregateCombinedPortfolio"),
      `$${Number(agg.combined_portfolio || 0).toFixed(2)}`,
      t("stats.aggregatePortfolioSub"),
      "bi-wallet2"
    ),
  ].join("");
}

function renderSystem(agg) {
  const el = document.getElementById("system-card");
  if (!el) return;
  const usage = agg.usage || {};
  const cursor = usage.cursor || {};
  const tokens = usage.tokens_today || 0;
  const quota = cursor.quota_available && cursor.quota_percent_remaining != null
    ? `${cursor.quota_percent_remaining}%`
    : t("stats.systemQuotaUnavailable");
  const cursorLine = [
    cursor.user_email,
    cursor.cloud_agents != null ? `${cursor.cloud_agents} ${t("footer.cloudAgents")}` : null,
  ].filter(Boolean).join(" · ") || t("common.emDash");
  el.innerHTML = `
    <div class="rh-card">
      <div class="rh-card-head"><i class="bi bi-activity me-1"></i> ${escapeHtml(t("stats.systemTitle"))}</div>
      <div class="p-3">
        <div class="row g-3 small">
          <div class="col-md-4">
            <div class="text-secondary">${escapeHtml(t("stats.systemScheduler"))}</div>
            <div class="fw-semibold">${escapeHtml(mixLabel(agg.scheduler || {}))}</div>
          </div>
          <div class="col-md-4">
            <div class="text-secondary">${escapeHtml(t("stats.systemLastRuns"))}</div>
            <div class="fw-semibold">${escapeHtml(statusMixLabel(agg.last_run_status || {}))}</div>
          </div>
          <div class="col-md-4">
            <div class="text-secondary">${escapeHtml(t("stats.systemMcp"))}</div>
            <div class="fw-semibold">${usage.tool_calls ?? 0}</div>
          </div>
          <div class="col-md-4">
            <div class="text-secondary">${escapeHtml(t("stats.systemLinked"))}</div>
            <div class="fw-semibold">${usage.cursor_linked_runs ?? 0}</div>
          </div>
          <div class="col-md-4">
            <div class="text-secondary">${escapeHtml(t("stats.systemTokens"))}</div>
            <div class="fw-semibold">${tokens ? Number(tokens).toLocaleString() : t("common.emDash")}</div>
          </div>
          <div class="col-md-4">
            <div class="text-secondary">${escapeHtml(t("stats.systemCursorAccount"))}</div>
            <div class="fw-semibold">${escapeHtml(cursorLine)}</div>
          </div>
          <div class="col-md-8">
            <div class="text-secondary">${escapeHtml(t("stats.systemQuota"))}</div>
            <div class="fw-semibold small">${escapeHtml(quota)}</div>
            <a class="small" href="https://cursor.com/settings" target="_blank" rel="noopener">cursor.com/settings</a>
          </div>
        </div>
      </div>
    </div>`;
}

function renderCards(bots) {
  const grid = document.getElementById("cards-view");
  charts.forEach((chart) => chart.destroy());
  charts.clear();
  if (!bots.length) {
    grid.innerHTML = `<div class="col-12"><p class="text-secondary mb-0">${escapeHtml(t("stats.emptyBots"))}</p></div>`;
    return;
  }
  grid.innerHTML = "";
  bots.forEach((b) => {
    const s = b.stats || {};
    const sched = b.scheduler || {};
    const col = document.createElement("div");
    col.className = "col-md-6 col-xl-4";
    const sparkId = `spark-${b.id.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
    const donutId = `donut-${b.id.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
    const pv =
      s.latest_portfolio_value != null
        ? `$${Number(s.latest_portfolio_value).toFixed(2)}`
        : t("common.emDash");

    col.innerHTML = `
      <div class="rh-card h-100">
        <div class="rh-card-head d-flex justify-content-between align-items-start gap-2">
          <div>
            <strong>${escapeHtml(b.name)}</strong>
            <div class="small text-secondary">${escapeHtml(cursorModelLabel(b.cursor_model) || t("common.emDash"))}</div>
          </div>
          <div class="d-flex flex-wrap gap-1 justify-content-end">
            ${assetClassBadge(b.asset_class)}
            ${b.simulation_mode ? `<span class="badge text-bg-info">${escapeHtml(t("dashboard.badgeSim"))}</span>` : ""}
            <span class="badge ${schedulerBadgeClass(sched)}">${escapeHtml(schedulerStatusLabel(sched))}</span>
            ${runStatusBadge(s.last_run_status)}
          </div>
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
  if (!bots.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="text-secondary">${escapeHtml(t("stats.emptyBots"))}</td></tr>`;
    return;
  }
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
        <td>${runStatusBadge(s.last_run_status)}</td>
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
  try {
    const [data] = await Promise.all([api("/api/statistics"), loadCursorModelLabels()]);
    lastData = data;
    const agg = data.aggregate || {};
    renderAggregate(agg);
    renderSystem(agg);
    renderCards(data.bots || []);
    renderTable(data.bots || []);
    const asOf = document.getElementById("stats-as-of");
    if (asOf) {
      asOf.textContent = t("stats.asOf", {
        time: data.as_of ? formatRunTime(data.as_of) : formatRunTime(new Date().toISOString()),
      });
    }
    if (typeof refreshAppFooter === "function") refreshAppFooter();
  } catch (err) {
    document.getElementById("aggregate-kpis").innerHTML =
      `<div class="col-12"><div class="alert alert-danger mb-0">${escapeHtml(String(err.message || err))}</div></div>`;
  }
}

const debounced = debounce(refresh, 2000);
connectWs((msg) => {
  if (msg.type === "agent_event") debounced();
});

whenI18nReady(() => {
  document.querySelectorAll("#stats-tabs .nav-link").forEach((btn) => {
    btn.addEventListener("click", () => showStatsTab(btn.dataset.tab));
  });
  refresh();
  startVisibleInterval(refresh, 60000);
});
