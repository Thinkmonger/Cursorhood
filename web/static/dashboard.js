/** Main dashboard — bot overview + global connections */

const DEFAULT_BOT_ID = "default";
let connectionsLoaded = false;
const botCharts = new Map();

function connBadge(cls, text) {
  return `<span class="badge ${cls}">${text}</span>`;
}

function seriesHasVariance(values, digits = 2) {
  const nums = values.filter((v) => v != null && !Number.isNaN(v));
  if (nums.length < 2) return false;
  const keys = new Set(nums.map((v) => Number(v).toFixed(digits)));
  return keys.size > 1;
}

function trimLeadingZeroPortfolio(series) {
  if (!series?.length) return [];
  const firstPositive = series.findIndex((p) => Number(p.value) > 0);
  if (firstPositive > 0) return series.slice(firstPositive);
  return series;
}

function portfolioChangeLabel(series) {
  const trimmed = trimLeadingZeroPortfolio(series);
  if (trimmed.length < 2) return "";

  const portfolioVals = trimmed.map((p) => Number(p.value)).filter((v) => !Number.isNaN(v));
  if (seriesHasVariance(portfolioVals)) {
    const start = portfolioVals[0];
    const end = portfolioVals[portfolioVals.length - 1];
    const change = end - start;
    const pct = start ? (change / start) * 100 : 0;
    const cls = change >= 0 ? "text-success" : "text-danger";
    return `<div class="small ${cls}">${formatMoney(change)} (${formatPct(pct)})</div>`;
  }

  if (trimmed.some((p) => p.unrealized_pl != null)) {
    const upl = trimmed.map((p) => Number(p.unrealized_pl ?? 0));
    if (seriesHasVariance(upl, 4)) {
      const change = upl[upl.length - 1] - upl[0];
      const cls = change >= 0 ? "text-success" : "text-danger";
      return `<div class="small ${cls}">${escapeHtml(t("dashboard.holdingsPl", { amount: formatMoney(change) }))}</div>`;
    }
  }

  return "";
}

function botChartValues(series) {
  const trimmed = trimLeadingZeroPortfolio(series);
  if (!trimmed.length) return { values: [], mode: "empty" };

  const portfolioVals = trimmed.map((p) => Number(p.value)).filter((v) => !Number.isNaN(v));
  if (seriesHasVariance(portfolioVals)) {
    return { values: portfolioVals, mode: "portfolio" };
  }

  if (trimmed.some((p) => p.holdings_value != null)) {
    const holdings = trimmed.map((p) => Number(p.holdings_value ?? 0));
    if (seriesHasVariance(holdings)) {
      return { values: holdings, mode: "holdings" };
    }
  }

  if (trimmed.some((p) => p.unrealized_pl != null)) {
    const upl = trimmed.map((p) => Number(p.unrealized_pl ?? 0));
    if (seriesHasVariance(upl, 4)) {
      return { values: upl, mode: "unrealized_pl" };
    }
  }

  if (portfolioVals.length >= 2) {
    const base = portfolioVals[0];
    return {
      values: portfolioVals.map((v) => v - base),
      mode: "portfolio_delta",
    };
  }

  return { values: [], mode: "flat" };
}

function renderBotCardSparkline(canvasId, series, botId, simulationMode = false) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || typeof Chart === "undefined") return null;

  if (botCharts.has(canvasId)) {
    botCharts.get(canvasId).destroy();
    botCharts.delete(canvasId);
  }

  const trimmed = trimLeadingZeroPortfolio(series);
  if (!trimmed.length) return null;

  const { values, mode } = botChartValues(trimmed);
  if (!values.length) return null;

  const trend = values.length > 1 ? values[values.length - 1] - values[0] : 0;
  const up = trend >= 0;
  const borderColor = simulationMode ? "#6ea8fe" : up ? "#00c805" : "#ff5000";
  const fillColor = simulationMode
    ? "rgba(110,168,254,0.18)"
    : up
      ? "rgba(0,200,5,0.18)"
      : "rgba(255,80,0,0.18)";
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = Math.max((max - min) * 0.15, 0.01);

  const chart = new Chart(canvas, {
    type: "line",
    data: {
      labels: trimmed.map((p) => p.ts?.slice(5, 10) || ""),
      datasets: [
        {
          data: values,
          borderColor,
          backgroundColor: fillColor,
          fill: true,
          tension: 0.35,
          pointRadius: 0,
          borderWidth: 1.5,
        },
      ],
    },
    options: {
      plugins: { legend: { display: false }, tooltip: { enabled: trimmed.length > 1 } },
      scales: {
        x: { display: false },
        y: {
          display: false,
          min: min - pad,
          max: max + pad,
        },
      },
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
    },
  });

  botCharts.set(canvasId, chart);
  return chart;
}

function botActionButton(label, className, botId, action, icon) {
  const iconHtml = icon ? `<i class="bi ${icon} me-1"></i>` : "";
  return `<button class="btn ${className} btn-sm" onclick="botAction('${escapeHtml(botId)}','${action}')">${iconHtml}${label}</button>`;
}

function botCardActionGrid(botId, botName, sched, isDefault) {
  const spacer = `<span class="btn btn-sm invisible" aria-hidden="true"><i class="bi bi-play-fill me-1"></i>${escapeHtml(t("bot.actionRunCycle"))}</span>`;
  const row2Control = sched.started
    ? sched.paused
      ? botActionButton(t("bot.actionResume"), "btn-outline-primary", botId, "resume", "bi-play-fill")
      : botActionButton(t("bot.actionPause"), "btn-outline-secondary", botId, "pause", "bi-pause-fill")
    : spacer;
  const row2Power = sched.started
    ? botActionButton(t("bot.actionStop"), "btn-outline-warning", botId, "stop", "bi-stop-fill")
    : botActionButton(t("bot.actionStart"), "btn-outline-success", botId, "start", "bi-play-circle");
  const row2Remove = isDefault
    ? spacer
    : `<button class="btn btn-outline-danger btn-sm" onclick="removeBot('${escapeHtml(botId)}','${escapeHtml(botName)}')"><i class="bi bi-trash me-1"></i>${escapeHtml(t("bot.actionRemove"))}</button>`;

  return `
    <div class="bot-card-actions">
      <a href="${botPath(botId)}" class="btn btn-outline-light btn-sm"><i class="bi bi-sliders me-1"></i>${escapeHtml(t("bot.actionManage"))}</a>
      <a href="${botPath(botId, "agents")}" class="btn btn-outline-secondary btn-sm"><i class="bi bi-list-ul me-1"></i>${escapeHtml(t("bot.actionRuns"))}</a>
      ${botActionButton(t("bot.actionRunCycle"), "btn-outline-secondary", botId, "run-now", "bi-play-fill")}
      ${row2Power}
      ${row2Control}
      ${row2Remove}
    </div>`;
}

function showDashTab(tab) {
  const overview = tab === "overview";
  document.getElementById("panel-overview").classList.toggle("d-none", !overview);
  document.getElementById("panel-configuration").classList.toggle("d-none", overview);
  document.querySelectorAll("#dash-tabs .nav-link").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tab);
  });
  const url = new URL(location.href);
  if (tab === "config") url.searchParams.set("tab", "config");
  else url.searchParams.delete("tab");
  history.replaceState(null, "", url.pathname + url.search);
  if (tab === "config" && !connectionsLoaded) loadConnections();
}

document.querySelectorAll("#dash-tabs .nav-link").forEach((btn) => {
  btn.addEventListener("click", () => showDashTab(btn.dataset.tab));
});

async function refreshBots() {
  const grid = document.getElementById("bots-grid");
  if (!grid) return;

  try {
    const { bots } = await api("/api/bots");
    grid.innerHTML = "";

    botCharts.forEach((chart) => chart.destroy());
    botCharts.clear();

    if (!bots.length) {
      grid.innerHTML = `<div class="col-12"><p class="text-secondary">${escapeHtml(t("dashboard.botsEmpty"))}</p></div>`;
      return;
    }

    bots.forEach((b) => {
    const sched = b.scheduler || {};
    const stats = b.stats || {};
    const col = document.createElement("div");
    col.className = "col-md-6 col-xl-4";
    const isDefault = b.id === DEFAULT_BOT_ID;
    const sparkId = `bot-spark-${b.id.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
    const portfolioLabel = b.simulation_mode ? t("dashboard.cardPortfolioSim") : t("dashboard.cardPortfolioLive");
    const managed =
      stats.managed_portfolio_value != null
        ? formatMoney(stats.managed_portfolio_value)
        : t("common.emDash");
    const changeHtml = portfolioChangeLabel(stats.portfolio_series || []);

    col.innerHTML = `
      <div class="rh-card h-100">
        <div class="rh-card-head d-flex justify-content-between align-items-start gap-2">
          <div>
            <strong>${escapeHtml(b.name)}</strong>
            <div class="small text-secondary">${escapeHtml(b.id)}</div>
          </div>
          <div class="d-flex flex-wrap gap-1 justify-content-end">
            ${assetClassBadge(b.asset_class)}
            ${b.simulation_mode ? `<span class="badge text-bg-info">${escapeHtml(t("dashboard.badgeSim"))}</span>` : ""}
            <span class="badge ${schedulerBadgeClass(sched)}">${escapeHtml(schedulerStatusLabel(sched))}</span>
          </div>
        </div>
        <div class="p-3 bot-card-body">
          <div class="row g-2 mb-1 small align-items-start">
            <div class="col-5">
              <div class="text-secondary">${escapeHtml(portfolioLabel)}</div>
              <div class="fw-semibold fs-5">${escapeHtml(managed)}</div>
              ${changeHtml}
            </div>
            <div class="col-3">
              <div class="text-secondary">${escapeHtml(t("common.runs"))}</div>
              <div class="fw-semibold fs-5">${stats.total_runs ?? 0}</div>
            </div>
            <div class="col-4">
              <div class="bot-card-sparkline" style="height: 56px;">
                <canvas id="${sparkId}"></canvas>
              </div>
            </div>
          </div>
          <div class="row g-2 mb-0 small text-secondary">
            <div class="col-6">
              ${escapeHtml(t("dashboard.lastRun"))} ${b.last_run ? escapeHtml(formatRunTime(b.last_run.started_at)) : t("common.emDash")}
            </div>
            <div class="col-6">
              ${escapeHtml(t("dashboard.nextRun"))} ${escapeHtml(formatNextScheduledRun(sched))}
            </div>
          </div>
          ${botCardActionGrid(b.id, b.name, sched, isDefault)}
        </div>
      </div>`;
    grid.appendChild(col);
    renderBotCardSparkline(sparkId, stats.portfolio_series || [], b.id, !!b.simulation_mode);
    });
  } catch (err) {
    grid.innerHTML = `<div class="col-12"><div class="alert alert-danger mb-0">${escapeHtml(String(err.message || err))}</div></div>`;
  }
}

async function botAction(botId, action) {
  await api(`/api/bots/${encodeURIComponent(botId)}/${action}`, { method: "POST", body: "{}" });
  toast(t("dashboard.actionToast", { action: action.replace("-", " ") }));
  refreshBots();
}

async function createBot() {
  const name = document.getElementById("new-bot-name").value.trim();
  if (!name) return toast(t("dashboard.createNameRequired"));
  const assetClass = document.querySelector("input[name='new-bot-class']:checked")?.value || "equity";
  const { bot } = await api("/api/bots", {
    method: "POST",
    body: JSON.stringify({ name, asset_class: assetClass }),
  });
  bootstrap.Modal.getInstance(document.getElementById("addBotModal"))?.hide();
  document.getElementById("new-bot-name").value = "";
  document.getElementById("new-bot-class-equity").checked = true;
  toast(t("dashboard.createSuccess", { name: bot.name }));
  refreshBots();
}

async function removeBot(botId, name) {
  if (!confirm(t("dashboard.removeConfirm", { name }))) return;
  await api(`/api/bots/${encodeURIComponent(botId)}`, { method: "DELETE" });
  toast(t("dashboard.removeSuccess"));
  refreshBots();
}

async function checkSetup() {
  try {
    const s = await api("/api/settings");
    const c = s.connections || {};
    const ok = c.cursor_api_key_set && (c.robinhood_has_token || c.robinhood_via_cursor);
    document.getElementById("setup-banner")?.classList.toggle("d-none", ok);
  } catch (_) {}
}

async function loadConnections() {
  const s = await api("/api/settings");
  const c = s.connections || {};
  const chips = [];
  chips.push(connBadge(c.cursor_api_key_set ? "text-bg-success" : "text-bg-danger", t("connections.badgeCursorKey")));
  chips.push(connBadge(c.massive_api_key_set ? "text-bg-success" : "text-bg-secondary", t("connections.badgeMassive")));
  if (c.robinhood_has_token) chips.push(connBadge("text-bg-success", t("connections.badgeRobinhoodToken")));
  else if (c.robinhood_via_cursor) chips.push(connBadge("text-bg-warning text-dark", t("connections.badgeConnectBot")));
  else chips.push(connBadge("text-bg-danger", t("connections.badgeRobinhood")));
  if (c.robinhood_via_cursor && c.robinhood_has_token) chips.push(connBadge("text-bg-info", t("connections.badgeCursorMcp")));
  document.getElementById("conn-chips").innerHTML = chips.join(" ");
  connectionsLoaded = true;
  checkSetup();
  if (c.robinhood_has_token) loadCapabilityChips();
}

const CAPABILITY_LABELS = {
  equity: "capabilities.equity",
  option: "capabilities.option",
  crypto: "capabilities.crypto",
  market_data: "capabilities.marketData",
  watchlist: "capabilities.watchlist",
  scanner: "capabilities.scanner",
  alert: "capabilities.alert",
};

async function loadCapabilityChips() {
  const el = document.getElementById("capability-chips");
  if (!el) return;
  try {
    const caps = await api("/api/trading/capabilities");
    if (!caps.ok) {
      el.innerHTML = "";
      return;
    }
    const chips = Object.entries(CAPABILITY_LABELS).map(([key, labelKey]) =>
      connBadge(
        caps.asset_classes?.[key] ? "text-bg-success" : "text-bg-secondary",
        t(labelKey)
      )
    );
    chips.unshift(connBadge("text-bg-info", t("capabilities.toolCount", { count: caps.tool_count })));
    el.innerHTML = chips.join(" ");
  } catch {
    el.innerHTML = "";
  }
}

async function saveKey() {
  const k = document.getElementById("cursor-key").value.trim();
  if (!k) return toast(t("connections.cursorKeyRequired"));
  await api("/api/settings/connections", { method: "PUT", body: JSON.stringify({ cursor_api_key: k }) });
  toast(t("connections.cursorKeySaved"));
  loadConnections();
}

async function connectRh() {
  await api("/api/settings/robinhood/connect", { method: "POST", body: "{}" });
  toast(t("connections.browserOpened"));
}

async function useCursorMcp() {
  await api("/api/settings/robinhood/use-cursor-mcp", { method: "POST", body: "{}" });
  toast(t("connections.mcpEnabled"));
  loadConnections();
}

async function testRh() {
  const result = await api("/api/settings/robinhood/test", { method: "POST", body: "{}" });
  const count = result.tool_count;
  toast(
    count != null
      ? t("connections.mcpOkWithTools", { count })
      : t("connections.mcpOk")
  );
}

async function saveMassiveKey() {
  const k = document.getElementById("massive-key").value.trim();
  if (!k) return toast(t("connections.massiveKeyRequired"));
  const result = await api("/api/settings/massive", {
    method: "PUT",
    body: JSON.stringify({ massive_api_key: k }),
  });
  document.getElementById("massive-key").value = "";
  toast(result.ok ? t("connections.massiveOk") : result.error || t("connections.massiveKeySaved"));
  loadConnections();
}

async function testMassive() {
  try {
    const result = await api("/api/settings/massive/test", { method: "POST", body: "{}" });
    const remaining = result.rate_limit?.remaining;
    const suffix =
      remaining != null ? t("connections.massiveRateLimit", { remaining }) : "";
    toast(t("connections.massiveOk") + suffix);
  } catch (e) {
    toast(String(e.message || e));
  }
}

async function disconnectRh() {
  await api("/api/settings/robinhood", { method: "DELETE" });
  toast(t("connections.disconnected"));
  loadConnections();
}

const debounced = debounce(refreshBots, 2000);

let dashboardStarted = false;
let dashboardWsStarted = false;

function startDashboardWs() {
  if (dashboardWsStarted) return;
  dashboardWsStarted = true;
  connectWs((msg) => {
    if (msg.type === "agent_event") debounced();
  });
}

function showBotsLoading() {
  const grid = document.getElementById("bots-grid");
  if (grid && !grid.querySelector(".rh-card")) {
    grid.innerHTML = `<div class="col-12"><p class="text-secondary mb-0">Loading bots…</p></div>`;
  }
}

function startDashboard() {
  if (dashboardStarted) return;
  dashboardStarted = true;

  const initialTab = new URLSearchParams(location.search).get("tab") === "config" ? "config" : "overview";
  showDashTab(initialTab);
  checkSetup();
  refreshBots();
  startDashboardWs();
  startVisibleInterval(refreshBots, 90000);
}

function bootstrapDashboard() {
  showBotsLoading();
  startDashboard();
}

document.addEventListener("DOMContentLoaded", bootstrapDashboard);
whenI18nReady(() => refreshBots());
if (document.readyState !== "loading") bootstrapDashboard();
