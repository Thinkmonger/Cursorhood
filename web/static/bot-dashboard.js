const botId = getBotIdFromPath();
const settingsBase = `/api/bots/${encodeURIComponent(botId)}/settings`;
let paused = false;
let started = false;
let configLoaded = false;
let portfolioSparkline = null;
let portfolioRequestSeq = 0;

const debouncedRefresh = debounce(() => {
  refresh();
  refreshPortfolio();
}, 2000);

function money(value, digits = 2) {
  if (typeof formatMoney === "function") return formatMoney(value, digits);
  if (value == null || Number.isNaN(Number(value))) return t("common.emDash");
  return `$${Number(value).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

function pct(value, digits = 2) {
  if (typeof formatPct === "function") return formatPct(value, digits);
  if (value == null || Number.isNaN(Number(value))) return "—";
  const n = Number(value);
  return `${n > 0 ? "+" : ""}${n.toFixed(digits)}%`;
}

function plClass(value) {
  if (typeof plTextClass === "function") return plTextClass(value);
  if (value == null || Number(value) === 0) return "text-secondary";
  return Number(value) > 0 ? "text-success" : "text-danger";
}

function toggleSimConfigFields() {
  const simOn = document.getElementById("simulation-mode")?.checked;
  const block = document.getElementById("sim-cash-block");
  if (block) block.classList.toggle("d-none", !simOn);
  const realism = document.getElementById("sim-realism-block");
  if (realism) realism.classList.toggle("d-none", !simOn);
}

function toggleAssetClassFields() {
  const cls = document.getElementById("asset-class")?.value || "equity";
  document.getElementById("options-block")?.classList.toggle("d-none", cls !== "option");
  document.getElementById("crypto-block")?.classList.toggle("d-none", cls !== "crypto");
}

function toggleSymbolSourceFields() {
  const source = document.getElementById("symbol-source")?.value || "static";
  document.getElementById("symbol-source-ref-block")?.classList.toggle("d-none", source === "static");
  loadWatchlistOptions();
}

function parseOptionalInt(value) {
  const trimmed = String(value ?? "").trim();
  if (!trimmed) return null;
  const n = Number(trimmed);
  return Number.isFinite(n) && n >= 1 ? Math.floor(n) : null;
}

function parseOptionalMoney(value) {
  const trimmed = String(value ?? "").trim();
  if (!trimmed) return null;
  const n = Number(trimmed);
  return Number.isFinite(n) && n > 0 ? n : null;
}

function showTab(tab) {
  const overview = tab === "overview";
  document.getElementById("panel-overview").classList.toggle("d-none", !overview);
  document.getElementById("panel-configuration").classList.toggle("d-none", overview);
  document.querySelectorAll("#bot-tabs .nav-link").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tab);
  });
  const url = new URL(location.href);
  if (tab === "config") url.searchParams.set("tab", "config");
  else url.searchParams.delete("tab");
  history.replaceState(null, "", url.pathname + url.search);
  if (tab === "config" && !configLoaded) loadBotConfig();
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

function setPortfolioState(state, message) {
  const loading = document.getElementById("portfolio-loading");
  const content = document.getElementById("portfolio-content");
  const unavailable = document.getElementById("portfolio-unavailable");
  if (!loading || !content || !unavailable) return false;
  loading.classList.toggle("d-none", state !== "loading");
  content.classList.toggle("d-none", state !== "ready");
  unavailable.classList.toggle("d-none", state !== "error");
  if (state === "error" && message) {
    unavailable.innerHTML = `<i class="bi bi-info-circle me-1"></i>${escapeHtml(message)}`;
  }
  return true;
}

function renderPortfolioSparkline(liveSeries, simSeries) {
  const canvas = document.getElementById("portfolio-sparkline");
  if (!canvas || typeof Chart === "undefined") return;
  if (portfolioSparkline) {
    portfolioSparkline.destroy();
    portfolioSparkline = null;
  }
  const live = liveSeries || [];
  const sim = simSeries || [];
  if (!live.length && !sim.length) return;

  const labels = (live.length >= sim.length ? live : sim).map((p) => p.ts?.slice(5, 16) || "");
  const datasets = [];
  if (live.length) {
    datasets.push({
      label: t("portfolio.chartLive"),
      data: live.map((p) => p.value),
      borderColor: "#00c805",
      backgroundColor: "rgba(0,200,5,0.08)",
      fill: false,
      tension: 0.3,
      pointRadius: 0,
    });
  }
  if (sim.length) {
    datasets.push({
      label: t("portfolio.chartSimulated"),
      data: sim.map((p) => p.value),
      borderColor: "#6ea8fe",
      backgroundColor: "rgba(110,168,254,0.08)",
      fill: false,
      tension: 0.3,
      pointRadius: 0,
      borderDash: [4, 3],
    });
  }

  portfolioSparkline = new Chart(canvas, {
    type: "line",
    data: { labels, datasets },
    options: {
      plugins: {
        legend: {
          display: datasets.length > 1,
          labels: { color: "#adb5bd", boxWidth: 12 },
        },
      },
      scales: {
        x: { display: false },
        y: {
          ticks: { color: "#6c757d", callback: (v) => money(v, 2) },
          grid: { color: "rgba(255,255,255,0.06)" },
        },
      },
      maintainAspectRatio: false,
    },
  });
}

function holdingsRowsHtml(holdings) {
  return holdings
    .map((h) => {
      const plCls = plClass(h.unrealized_pl);
      const dayCls = plClass(h.day_change_pct);
      return `<tr>
          <td><span class="badge text-bg-dark border border-secondary">${escapeHtml(h.symbol)}</span></td>
          <td class="text-end font-monospace small">${Number(h.quantity).toFixed(4)}</td>
          <td class="text-end">${money(h.average_buy_price)}</td>
          <td class="text-end">${money(h.last_price)}</td>
          <td class="text-end fw-semibold">${money(h.market_value)}</td>
          <td class="text-end ${plCls}">
            ${h.unrealized_pl != null ? money(h.unrealized_pl) : t("common.emDash")}
            ${h.unrealized_pl_pct != null ? `<div class="small">${pct(h.unrealized_pl_pct)}</div>` : ""}
          </td>
          <td class="text-end ${dayCls}">${h.day_change_pct != null ? pct(h.day_change_pct) : t("common.emDash")}</td>
        </tr>`;
    })
    .join("");
}

function renderHoldingsBlock(holdings, tbodyId, emptyId, wrapId) {
  const tbody = document.getElementById(tbodyId);
  const empty = document.getElementById(emptyId);
  const wrap = document.getElementById(wrapId);
  if (!tbody || !empty || !wrap) return;

  if (!holdings.length) {
    tbody.innerHTML = "";
    wrap.classList.add("d-none");
    empty.classList.remove("d-none");
    return;
  }
  wrap.classList.remove("d-none");
  empty.classList.add("d-none");
  tbody.innerHTML = holdingsRowsHtml(holdings);
}

function renderAllocationBlock(portfolio, summary, prefix) {
  const investedPct = summary.invested_pct || 0;
  const cashPct = summary.cash_pct || 0;
  const investedLabel = document.getElementById(`${prefix}alloc-invested-label`);
  const cashLabel = document.getElementById(`${prefix}alloc-cash-label`);
  const investedBar = document.getElementById(`${prefix}alloc-invested-bar`);
  const cashBar = document.getElementById(`${prefix}alloc-cash-bar`);
  if (investedLabel) investedLabel.textContent = `${money(summary.invested_value)} (${investedPct}%)`;
  if (cashLabel) cashLabel.textContent = `${money(portfolio.cash)} (${cashPct}%)`;
  if (investedBar) investedBar.style.width = `${Math.min(100, investedPct)}%`;
  if (cashBar) cashBar.style.width = `${Math.min(100, cashPct)}%`;
}

function renderKpiRow(containerId, portfolio, summary, badgeKey) {
  const el = document.getElementById(containerId);
  if (!el) return;
  const unrealized = summary.total_unrealized_pl;
  const badge = badgeKey ? t(badgeKey) : "";
  el.innerHTML = [
    kpiCard(t("portfolio.kpiTotal", { badge }), money(portfolio.total_value), t("portfolio.kpiAccountValue"), "bi-wallet2"),
    kpiCard(t("portfolio.kpiEquity", { badge }), money(portfolio.equity_value), t("portfolio.kpiPositionsCount", { count: summary.position_count || 0 }), "bi-bar-chart"),
    kpiCard(t("portfolio.kpiCash", { badge }), money(portfolio.cash), summary.cash_pct != null ? t("portfolio.kpiCashPct", { pct: summary.cash_pct }) : "", "bi-cash-stack"),
    kpiCard(
      t("portfolio.kpiUnrealized", { badge }),
      unrealized != null ? money(unrealized, 2) : t("common.emDash"),
      summary.position_count ? t("portfolio.kpiOpenPositions") : t("portfolio.kpiNoPositions"),
      "bi-graph-up"
    ),
  ].join("");
}

function renderPortfolioOverview(overview) {
  if (!overview?.ok) {
    setPortfolioState("error", t("portfolio.errorUnavailable"));
    return;
  }

  if (!setPortfolioState("ready")) {
    return;
  }

  try {
    const simMode = !!overview.simulation_mode;
    const live = overview.live || {};
    const livePortfolio = live.portfolio || overview.portfolio || {};
    const liveSummary = live.summary || overview.summary || {};
    const liveHoldings = live.holdings || overview.holdings || [];
    const simulation = overview.simulation || null;
    const perf = overview.performance || {};
    const liveChange = perf.portfolio_change || {};
    const simChange = perf.simulation_change || (simulation?.performance || {}).portfolio_change || {};

    const banner = document.getElementById("sim-mode-banner");
    if (banner) banner.classList.toggle("d-none", !simMode);

    const liveLabel = document.getElementById("live-kpi-label");
    if (liveLabel) liveLabel.classList.toggle("d-none", !simMode);

    const sourceLabel =
      overview.source === "live"
        ? t("portfolio.sourceLive")
        : overview.source === "cached"
          ? t("portfolio.sourceCached")
          : "";
    const asOfEl = document.getElementById("portfolio-as-of");
    if (asOfEl) {
      asOfEl.textContent = overview.as_of
        ? `${sourceLabel} · ${formatRunTime(overview.as_of)}`
        : sourceLabel;
    }

    renderKpiRow("portfolio-kpis", livePortfolio, liveSummary, simMode ? "portfolio.kpiBadgeLive" : "");

    const simKpisBlock = document.getElementById("sim-kpis-block");
    if (simMode && simulation?.portfolio) {
      if (simKpisBlock) simKpisBlock.classList.remove("d-none");
      renderKpiRow(
        "portfolio-kpis-sim",
        simulation.portfolio,
        simulation.summary || {},
        "portfolio.kpiBadgeSim"
      );
    } else {
      if (simKpisBlock) simKpisBlock.classList.add("d-none");
      const simKpis = document.getElementById("portfolio-kpis-sim");
      if (simKpis) simKpis.innerHTML = "";
    }

    renderHoldingsBlock(liveHoldings, "holdings-body", "holdings-empty", "holdings-wrap");

    const simHoldingsSection = document.getElementById("sim-holdings-section");
    if (simMode && simulation) {
      if (simHoldingsSection) simHoldingsSection.classList.remove("d-none");
      renderHoldingsBlock(
        simulation.holdings || [],
        "sim-holdings-body",
        "sim-holdings-empty",
        "sim-holdings-wrap"
      );
    } else if (simHoldingsSection) {
      simHoldingsSection.classList.add("d-none");
    }

    renderAllocationBlock(livePortfolio, liveSummary, "");

    const simAllocBlock = document.getElementById("sim-alloc-block");
    if (simMode && simulation?.portfolio) {
      if (simAllocBlock) simAllocBlock.classList.remove("d-none");
      renderAllocationBlock(simulation.portfolio, simulation.summary || {}, "sim-");
    } else if (simAllocBlock) {
      simAllocBlock.classList.add("d-none");
    }

    const perfRuns = document.getElementById("perf-runs");
    const perfSuccess = document.getElementById("perf-success");
    const perfTrades = document.getElementById("perf-trades");
    if (perfRuns) perfRuns.textContent = String(perf.total_runs ?? 0);
    if (perfSuccess) perfSuccess.textContent = `${perf.success_rate ?? 0}%`;
    if (perfTrades) {
      perfTrades.textContent = simMode
        ? String(perf.simulation_trades ?? 0)
        : String(perf.trades_placed ?? 0);
    }

    const tradesSim = document.getElementById("perf-trades-sim");
    if (tradesSim) {
      if (simMode) {
        tradesSim.classList.remove("d-none");
        tradesSim.textContent = t("portfolio.perfTradesLiveSim", {
          live: perf.trades_placed ?? 0,
          sim: perf.simulation_trades ?? 0,
        });
      } else {
        tradesSim.classList.add("d-none");
        tradesSim.textContent = "";
      }
    }

    const changeEl = document.getElementById("perf-change");
    if (changeEl) {
      const change = simMode && simChange.change_usd != null ? simChange : liveChange;
      if (change.change_usd != null) {
        changeEl.textContent = `${money(change.change_usd)} (${pct(change.change_pct)})`;
        changeEl.className = `fs-6 fw-semibold ${plClass(change.change_usd)}`;
      } else {
        changeEl.textContent = t("common.emDash");
        changeEl.className = "fs-6 fw-semibold text-secondary";
      }
    }

    const changeSim = document.getElementById("perf-change-sim");
    if (changeSim) {
      if (simMode && liveChange.change_usd != null) {
        changeSim.classList.remove("d-none");
        changeSim.className = `small ${plClass(liveChange.change_usd)}`;
        changeSim.textContent = t("portfolio.perfChangeLive", {
          amount: money(liveChange.change_usd),
          pct: pct(liveChange.change_pct),
        });
      } else {
        changeSim.classList.add("d-none");
        changeSim.textContent = "";
      }
    }

    renderPortfolioSparkline(
      perf.portfolio_series || [],
      simMode ? perf.simulation_series || simulation?.performance?.portfolio_series || [] : null
    );

    const actions = perf.actions || {};
    const actionColors = { buy: "text-bg-success", sell: "text-bg-danger", none: "text-bg-secondary" };
    const perfActions = document.getElementById("perf-actions");
    if (perfActions) {
      perfActions.innerHTML = Object.keys(actions).length
        ? Object.entries(actions)
            .map(
              ([action, count]) =>
                `<span class="badge ${actionColors[action] || "text-bg-secondary"}">${escapeHtml(action)}: ${count}</span>`
            )
            .join("")
        : `<span class="text-secondary small">${escapeHtml(t("portfolio.perfActionsEmpty"))}</span>`;
    }
  } catch (err) {
    setPortfolioState("error", t("portfolio.errorRenderFailed", { message: err?.message || err }));
  }
}

async function refreshPortfolio() {
  const seq = ++portfolioRequestSeq;
  setPortfolioState("loading");

  try {
    const data = await api(`/api/bots/${encodeURIComponent(botId)}/portfolio`);
    if (seq !== portfolioRequestSeq) return;
    renderPortfolioOverview(data.portfolio_overview);
  } catch (err) {
    if (seq !== portfolioRequestSeq) return;
    setPortfolioState("error", t("portfolio.errorLoadFailed", { message: err?.message || err }));
  }
}

async function refresh() {
  try {
    const d = await api(`/api/bots/${encodeURIComponent(botId)}/dashboard`);
    renderProfileGateBanner(d.profile_gate, "profile-gate-banner");

    document.getElementById("bot-title").textContent = d.bot?.name || botId;
    setBotPageTitle(d.bot?.name || botId);

    paused = d.scheduler?.paused ?? false;
    started = d.scheduler?.started ?? false;
    const app = d.app || {};
    const sched = d.scheduler || {};

    const badge = document.getElementById("bot-sched-badge");
    badge.textContent = schedulerStatusLabel(sched);
    badge.className = "badge " + schedulerBadgeClass(sched);

    const simBadge = document.getElementById("bot-sim-badge");
    if (simBadge) {
      simBadge.classList.toggle("d-none", !app.simulation_mode);
    }

    const schedLine = document.getElementById("scheduler-status");
    let schedText = t("scheduler.lineBase", {
      interval: app.cycle_interval_seconds,
      model: app.cursor_model,
      status: schedulerStatusLabel(sched),
      nextRun: formatNextScheduledRun(sched),
    });
    if (sched.max_runs != null) {
      schedText += t("scheduler.lineRunCount", { current: sched.run_count ?? 0, max: sched.max_runs });
      if (sched.max_runs_reached) schedText += t("scheduler.lineMaxRunsReached");
    }
    schedLine.textContent = schedText;

    document.getElementById("pause-btn").textContent = paused ? t("bot.actionResume") : t("bot.actionPause");
    document.getElementById("pause-btn").disabled = !started;
    document.getElementById("start-btn").textContent = started ? t("bot.actionStop") : t("bot.actionStart");
    const limitsEl = document.getElementById("limits");
    if (limitsEl) limitsEl.innerHTML = renderRiskLimitsHtml(d.limits);

    renderAssetPanels(d);

    const list = document.getElementById("runs");
    list.innerHTML = "";
    (d.last_runs || []).forEach((r) => list.appendChild(renderDashboardRunItem(r, botId)));
  } catch (err) {
    toast(String(err?.message || err));
  }
}

function showCard(id, visible) {
  document.getElementById(id)?.classList.toggle("d-none", !visible);
}

function renderAssetPanels(d) {
  const limits = d.limits || {};
  renderOptionPositions(d.option_positions || [], (limits.asset_class || "") === "option");
  renderCryptoPositions(d.crypto_positions || [], (limits.asset_class || "") === "crypto");
  renderPaperPanel(d.paper);
  renderWatchlistsPanel(d.watchlists, d.symbol_source);
}

function emptyRow(colspan, key) {
  return `<tr><td colspan="${colspan}" class="text-center text-secondary py-3">${escapeHtml(t(key))}</td></tr>`;
}

function renderOptionPositions(rows, enabled) {
  showCard("option-positions-card", enabled);
  const body = document.getElementById("option-positions-body");
  if (!body) return;
  if (!rows.length) {
    body.innerHTML = emptyRow(4, "assets.noOptionPositions");
    return;
  }
  body.innerHTML = rows
    .map((r) => {
      const label = `${r.symbol} ${r.expiry || ""} ${r.strike ?? ""}${String(r.type || "").charAt(0).toUpperCase()}`;
      return `<tr>
        <td class="font-monospace small">${escapeHtml(label.trim())}</td>
        <td class="text-end">${escapeHtml(String(r.qty ?? ""))}</td>
        <td class="text-end">${escapeHtml(formatMoney(r.mark))}</td>
        <td class="text-end ${plTextClass(r.pnl)}">${escapeHtml(formatMoney(r.pnl))}</td>
      </tr>`;
    })
    .join("");
}

function renderCryptoPositions(rows, enabled) {
  showCard("crypto-positions-card", enabled);
  const body = document.getElementById("crypto-positions-body");
  if (!body) return;
  if (!rows.length) {
    body.innerHTML = emptyRow(4, "assets.noCryptoPositions");
    return;
  }
  body.innerHTML = rows
    .map(
      (r) => `<tr>
        <td class="font-monospace small">${escapeHtml(r.pair || r.symbol || "")}</td>
        <td class="text-end">${escapeHtml(String(r.qty ?? ""))}</td>
        <td class="text-end">${escapeHtml(formatMoney(r.price ?? r.mark))}</td>
        <td class="text-end ${plTextClass(r.pnl)}">${escapeHtml(formatMoney(r.pnl))}</td>
      </tr>`
    )
    .join("");
}

function renderPaperPanel(paper) {
  showCard("paper-trading-card", !!paper);
  if (!paper) return;

  const badge = document.getElementById("paper-pnl-badge");
  if (badge) {
    badge.textContent = t("paper.realizedBadge", { value: formatMoney(paper.realized_pnl) });
    badge.className = "badge " + (Number(paper.realized_pnl) >= 0 ? "text-bg-success" : "text-bg-danger");
  }

  const ordersEl = document.getElementById("paper-open-orders");
  if (ordersEl) {
    const orders = paper.open_orders || [];
    ordersEl.innerHTML = orders.length
      ? orders
          .map(
            (o) => `<div class="d-flex justify-content-between align-items-center gap-2 py-1 border-bottom border-secondary-subtle">
              <span class="small"><span class="badge text-bg-secondary me-1">${escapeHtml(String(o.side || "").toUpperCase())}</span>${escapeHtml(o.symbol || "")} <span class="text-secondary">${escapeHtml(o.order_type || "market")}</span></span>
              <button class="btn btn-outline-danger btn-sm py-0" onclick="cancelPaperOrder('${escapeHtml(o.id)}')">${escapeHtml(t("paper.cancel"))}</button>
            </div>`
          )
          .join("")
      : `<p class="text-secondary small mb-0">${escapeHtml(t("paper.noOpenOrders"))}</p>`;
  }

  const fillsEl = document.getElementById("paper-fills");
  if (fillsEl) {
    const fills = (paper.recent_fills || []).slice().reverse();
    fillsEl.innerHTML = fills.length
      ? fills
          .map(
            (f) => `<div class="d-flex justify-content-between gap-2 py-1 small">
              <span><span class="badge ${f.action === "buy" ? "text-bg-primary" : "text-bg-warning text-dark"} me-1">${escapeHtml(String(f.action || "").toUpperCase())}</span>${escapeHtml(f.symbol || "")}</span>
              <span class="text-secondary">${escapeHtml(String(Number(f.qty).toFixed(4)))} @ ${escapeHtml(formatMoney(f.price))}${f.realized_pnl ? ` · <span class="${plTextClass(f.realized_pnl)}">${escapeHtml(formatMoney(f.realized_pnl))}</span>` : ""}</span>
            </div>`
          )
          .join("")
      : `<p class="text-secondary small mb-0">${escapeHtml(t("paper.noFills"))}</p>`;
  }
}

function renderWatchlistsPanel(watchlists, symbolSource) {
  const rows = watchlists || [];
  showCard("watchlists-card", rows.length > 0 || !!symbolSource);

  const badge = document.getElementById("symbol-source-badge");
  if (badge) {
    badge.textContent = symbolSource?.source || t("watchlists.sourceStatic");
    badge.className =
      "badge " + (symbolSource?.source?.startsWith("static") ? "text-bg-secondary" : "text-bg-info");
  }

  const el = document.getElementById("watchlists-list");
  if (!el) return;
  const resolved = (symbolSource?.symbols || []).filter(Boolean);
  let html = "";
  if (resolved.length) {
    html += `<div class="small mb-2"><span class="text-secondary">${escapeHtml(t("watchlists.thisCycle"))}:</span> <span class="font-monospace">${escapeHtml(resolved.join(", "))}</span></div>`;
  }
  if (!rows.length) {
    el.innerHTML = html + `<p class="text-secondary small mb-0">${escapeHtml(t("watchlists.empty"))}</p>`;
    return;
  }
  const activeRef = String(symbolSource?.source || "").split(":")[1] || "";
  el.innerHTML =
    html +
    rows
      .map((w) => {
        const active = w.name && w.name.toLowerCase() === activeRef.toLowerCase();
        const symbols = (w.symbols || []).join(", ");
        return `<div class="d-flex justify-content-between align-items-start gap-2 py-2 border-bottom border-secondary-subtle">
        <div class="flex-grow-1">
          <div class="fw-semibold small">${escapeHtml(w.name || "")}${active ? ` <span class="badge text-bg-info ms-1">${escapeHtml(t("watchlists.active"))}</span>` : ""}</div>
          <div class="text-secondary small text-truncate">${escapeHtml(symbols)}</div>
        </div>
        <button class="btn btn-outline-secondary btn-sm py-0" onclick="useWatchlist('${escapeHtml(w.name || "")}')">${escapeHtml(t("watchlists.use"))}</button>
      </div>`;
      })
      .join("");
}

async function cancelPaperOrder(orderId) {
  try {
    await api(`/api/bots/${encodeURIComponent(botId)}/paper-orders/${encodeURIComponent(orderId)}`, {
      method: "DELETE",
    });
    toast(t("paper.cancelled"));
    refresh();
    refreshPortfolio();
  } catch (err) {
    toast(String(err?.message || err));
  }
}

async function useWatchlist(name) {
  if (!name) return;
  try {
    await api(`${settingsBase}/limits`, {
      method: "PATCH",
      body: JSON.stringify({ symbol_source: "watchlist", symbol_source_ref: name }),
    });
    toast(t("watchlists.switched", { name }));
    refresh();
  } catch (err) {
    toast(String(err?.message || err));
  }
}

let subscriptionModelIds = new Set(["auto", "default", "composer", "composer-2", "composer-2.5", "composer-2-fast"]);
let cursorModelCatalog = null;

function isSubscriptionModel(modelId) {
  const m = String(modelId || "").trim().toLowerCase();
  if (!m) return false;
  if (subscriptionModelIds.has(m) || m.startsWith("composer") || m.startsWith("grok") || m.startsWith("cursor-grok")) {
    return true;
  }
  return false;
}

function selectedModelBilling(select) {
  const opt = select?.selectedOptions?.[0];
  if (opt?.dataset?.billing === "ide" || opt?.dataset?.billing === "api") {
    return opt.dataset.billing;
  }
  return isSubscriptionModel(select?.value) ? "ide" : "api";
}

function updateModelBillingWarning() {
  const select = document.getElementById("model");
  const warning = document.getElementById("model-subscription-warning");
  if (!select || !warning) return;
  warning.classList.toggle("d-none", selectedModelBilling(select) !== "ide");
}

function makeModelOption(entry) {
  const option = document.createElement("option");
  const id = typeof entry === "string" ? entry : entry.id;
  option.value = id;
  option.textContent = (typeof entry === "string" ? entry : entry.label) || id;
  option.dataset.billing = typeof entry === "string"
    ? (isSubscriptionModel(id) ? "ide" : "api")
    : (entry.billing || (isSubscriptionModel(id) ? "ide" : "api"));
  return option;
}

function ensureModelOption(select, modelId) {
  if (!select || !modelId) return;
  const exists = Array.from(select.options).some((opt) => opt.value === modelId);
  if (exists) return;
  const option = makeModelOption({
    id: modelId,
    label: t("bot.modelCurrentOption", { id: modelId }),
    billing: isSubscriptionModel(modelId) ? "ide" : "api",
  });
  const group = document.createElement("optgroup");
  group.label = t("bot.modelCurrentOption", { id: modelId });
  group.appendChild(option);
  select.insertBefore(group, select.firstChild);
}

function fillModelSelect(data, selectedId) {
  const select = document.getElementById("model");
  if (!select) return;
  if (Array.isArray(data?.subscription_models)) {
    subscriptionModelIds = new Set(
      data.subscription_models.map((id) => String(id).trim().toLowerCase()).filter(Boolean)
    );
  }
  const wanted = String(selectedId || select.value || data?.default || "").trim();
  select.innerHTML = "";
  const groups = Array.isArray(data?.groups) && data.groups.length
    ? data.groups
    : [
        {
          id: "ide",
          label: t("bot.modelGroupIde"),
          billing: "ide",
          models: (data?.subscription_models || []).map((id) => ({ id, label: id, billing: "ide" })),
        },
        {
          id: "api",
          label: t("bot.modelGroupApi"),
          billing: "api",
          models: (data?.models || [])
            .filter((id) => !isSubscriptionModel(id))
            .map((id) => ({ id, label: id, billing: "api" })),
        },
      ];
  groups.forEach((group) => {
    const models = group.models || [];
    if (!models.length) return;
    const optgroup = document.createElement("optgroup");
    const i18nKey = group.id === "ide" ? "bot.modelGroupIde" : group.id === "api" ? "bot.modelGroupApi" : "";
    const translated = i18nKey ? t(i18nKey) : "";
    optgroup.label = translated && translated !== i18nKey ? translated : (group.label || i18nKey || group.id);
    models.forEach((entry) => optgroup.appendChild(makeModelOption(entry)));
    select.appendChild(optgroup);
  });
  if (!select.options.length && data?.models) {
    (data.models || []).forEach((id) => select.appendChild(makeModelOption(id)));
  }
  ensureModelOption(select, wanted);
  if (wanted) select.value = wanted;
  else if (data?.default) select.value = data.default;
  updateModelBillingWarning();
}

async function loadCursorApiModels(selectedId) {
  const select = document.getElementById("model");
  if (!select) return;
  try {
    if (!cursorModelCatalog) {
      cursorModelCatalog = await api("/api/settings/cursor/models");
    }
    fillModelSelect(cursorModelCatalog, selectedId);
  } catch {
    fillModelSelect({ groups: [], models: [], default: selectedId }, selectedId);
  }
}

async function loadBotConfig() {
  const s = await api(settingsBase);
  document.getElementById("bot-name").value = s.name || "";
  document.getElementById("strategy").value = s.strategy;
  const l = s.limits;
  document.getElementById("max-order").value = l.max_order_notional_usd;
  document.getElementById("max-loss").value = l.max_daily_loss_usd;
  document.getElementById("max-positions").value = l.max_open_positions;
  document.getElementById("symbols").value = (l.allowed_symbols || []).join(", ");
  document.getElementById("min-gap").value = l.min_seconds_between_orders;
  document.getElementById("market-hours").checked = l.market_hours_only;
  const a = s.app;
  document.getElementById("interval").value = a.cycle_interval_seconds;
  await loadCursorApiModels(a.cursor_model);
  if (a.cursor_model) document.getElementById("model").value = a.cursor_model;
  updateModelBillingWarning();
  document.getElementById("auto-start").checked = a.auto_start_scheduler;
  const simEl = document.getElementById("simulation-mode");
  if (simEl) simEl.checked = !!a.simulation_mode;
  const simIncludeLive = document.getElementById("sim-include-live");
  if (simIncludeLive) simIncludeLive.checked = !!a.simulation_include_live_portfolio;
  const simCashEl = document.getElementById("sim-cash-start");
  if (simCashEl) {
    simCashEl.value =
      a.simulated_cash_starting_value != null ? String(a.simulated_cash_starting_value) : "";
  }
  const maxRunsEl = document.getElementById("max-runs");
  if (maxRunsEl) maxRunsEl.value = a.max_runs != null ? String(a.max_runs) : "";

  setValue("symbol-source", l.symbol_source || "static");
  setValue("symbol-source-ref", l.symbol_source_ref || "");
  setValue("symbol-source-limit", l.symbol_source_limit ?? 20);
  setValue("context-profile", a.context_profile || "minimal");
  setChecked("scanners-enabled", a.scanners_enabled !== false);

  const assetClass =
    l.asset_class || (l.crypto_enabled ? "crypto" : l.options_enabled ? "option" : "equity");
  setValue("asset-class", assetClass);
  setValue("max-option-contracts", l.max_option_contracts ?? 1);
  setValue("max-option-notional", l.max_option_notional_usd ?? 100);
  setValue("allowed-option-types", (l.allowed_option_types || ["call", "put"]).join(", "));
  setValue("min-dte", l.min_days_to_expiry ?? 7);
  setValue("max-dte", l.max_days_to_expiry ?? 60);
  setChecked("allow-option-selling", !!l.allow_option_selling);

  setValue("allowed-crypto-pairs", (l.allowed_crypto_pairs || []).join(", "));
  setValue("max-crypto-notional", l.max_crypto_notional_usd ?? 25);

  setValue("sim-slippage", a.simulation_slippage_bps ?? 0);
  setValue("sim-commission", a.simulation_commission_per_order ?? 0);
  setValue("sim-settlement", a.simulation_settlement_days ?? 0);

  toggleSimConfigFields();
  toggleAssetClassFields();
  toggleSymbolSourceFields();
  loadWatchlistOptions();
  setupBotIdField();
  configLoaded = true;
}

function setValue(id, value) {
  const el = document.getElementById(id);
  if (el) el.value = value == null ? "" : String(value);
}

function setChecked(id, value) {
  const el = document.getElementById(id);
  if (el) el.checked = !!value;
}

function commaList(id) {
  return (document.getElementById(id)?.value || "")
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
}

async function loadWatchlistOptions() {
  const datalist = document.getElementById("watchlist-options");
  if (!datalist) return;
  try {
    const [{ watchlists }, popular, scansRes] = await Promise.all([
      api("/api/watchlists"),
      api("/api/watchlists/popular").catch(() => ({ watchlists: [] })),
      api("/api/scans").catch(() => ({ scans: [] })),
    ]);
    const source = document.getElementById("symbol-source")?.value || "static";
    const rows =
      source === "scan"
        ? scansRes.scans || []
        : source === "popular_watchlist"
          ? popular.watchlists || []
          : watchlists || [];
    const names = new Set();
    const options = [];
    for (const w of rows) {
      const name = w.name || w.id || "";
      if (!name || names.has(name)) continue;
      names.add(name);
      options.push(`<option value="${escapeHtml(name)}"></option>`);
    }
    datalist.innerHTML = options.join("");
  } catch {
    /* watchlist suggestions are optional */
  }
}

function setupBotIdField() {
  const input = document.getElementById("bot-id-input");
  const slugBtn = document.getElementById("bot-slug-btn");
  const hint = document.getElementById("bot-id-hint");
  const isDefault = botId === "default";
  if (input) {
    input.value = botId;
    input.disabled = isDefault;
  }
  if (slugBtn) slugBtn.disabled = isDefault;
  if (hint) hint.classList.toggle("d-none", !isDefault);
}

async function generateBotSlug() {
  if (botId === "default") return toast(t("bot.idFixedDefault"));
  const name = document.getElementById("bot-name").value.trim();
  const { slug } = await api(`/api/bots/${encodeURIComponent(botId)}/slug-from-name`, {
    method: "POST",
    body: JSON.stringify({ name: name || undefined }),
  });
  const input = document.getElementById("bot-id-input");
  if (input) input.value = slug;
  toast(t("bot.idRefreshed"));
}

async function saveBotDetails() {
  const name = document.getElementById("bot-name").value.trim();
  if (!name) return toast(t("bot.detailsNameRequired"));

  const body = { name };
  const idInput = document.getElementById("bot-id-input");
  const newId = idInput?.value.trim().toLowerCase();
  if (botId !== "default" && newId && newId !== botId) {
    body.id = newId;
  }

  const res = await api(`/api/bots/${encodeURIComponent(botId)}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });

  toast(t("bot.detailsSaved"));
  if (res.redirect) {
    location.href = res.redirect + (location.search.includes("tab=config") ? "?tab=config" : "");
    return;
  }

  document.getElementById("bot-title").textContent = name;
  setBotPageTitle(name);
  populateBotSwitcher("bot-switcher");
  refresh();
}

async function saveStrategy() {
  await api(`${settingsBase}/strategy`, {
    method: "PUT",
    body: JSON.stringify({ content: document.getElementById("strategy").value }),
  });
  toast(t("bot.strategySaved"));
  refresh();
}

async function resetSimulation() {
  if (!confirm(t("simulation.resetConfirm"))) {
    return;
  }
  const url = `/api/bots/${encodeURIComponent(botId)}/reset-simulation`;
  const simCash = parseOptionalMoney(document.getElementById("sim-cash-start")?.value);
  const resetBody = { simulated_cash_starting_value: simCash };
  try {
    const res = await api(url, {
      method: "POST",
      body: JSON.stringify(resetBody),
    });
    toast(
      t("simulation.resetSuccess", {
        runs: res.deleted_runs ?? 0,
        startingCash: money(res.starting_cash),
      })
    );
    refresh();
    refreshPortfolio();
  } catch (err) {
    const msg = String(err?.message || err);
    if (/not found/i.test(msg)) {
      toast(t("simulation.resetApiMissing"));
    } else {
      toast(msg);
    }
  }
}

async function saveBotConfig() {
  const syms = document
    .getElementById("symbols")
    .value.split(",")
    .map((x) => x.trim())
    .filter(Boolean);
  const limitsPayload = {
      max_order_notional_usd: +document.getElementById("max-order").value,
      max_daily_loss_usd: +document.getElementById("max-loss").value,
      allowed_symbols: syms,
      max_open_positions: +document.getElementById("max-positions").value,
      market_hours_only: document.getElementById("market-hours").checked,
      min_seconds_between_orders: +document.getElementById("min-gap").value,
      symbol_source: document.getElementById("symbol-source")?.value || "static",
      symbol_source_ref: document.getElementById("symbol-source-ref")?.value.trim() || null,
      symbol_source_limit: +(document.getElementById("symbol-source-limit")?.value || 20),
      asset_class: document.getElementById("asset-class")?.value || "equity",
      options_enabled: (document.getElementById("asset-class")?.value || "equity") === "option",
      max_option_contracts: +(document.getElementById("max-option-contracts")?.value || 1),
      max_option_notional_usd: +(document.getElementById("max-option-notional")?.value || 100),
      allowed_option_types: commaList("allowed-option-types"),
      min_days_to_expiry: +(document.getElementById("min-dte")?.value || 0),
      max_days_to_expiry: +(document.getElementById("max-dte")?.value || 60),
      allow_option_selling: document.getElementById("allow-option-selling")?.checked ?? false,
      crypto_enabled: (document.getElementById("asset-class")?.value || "equity") === "crypto",
      allowed_crypto_pairs: commaList("allowed-crypto-pairs"),
      max_crypto_notional_usd: +(document.getElementById("max-crypto-notional")?.value || 25),
  };
  await api(`${settingsBase}/limits`, {
    method: "PUT",
    body: JSON.stringify(limitsPayload),
  });
  const simCash = parseOptionalMoney(document.getElementById("sim-cash-start")?.value);
  const modelValue = document.getElementById("model").value;
  const appPayload = {
    cycle_interval_seconds: +document.getElementById("interval").value,
    cursor_model: modelValue,
    auto_start_scheduler: document.getElementById("auto-start").checked,
    scheduler_enabled: true,
      simulation_mode: document.getElementById("simulation-mode")?.checked ?? false,
      simulated_cash_starting_value: simCash,
      simulation_include_live_portfolio:
        document.getElementById("sim-include-live")?.checked ?? false,
      max_runs: parseOptionalInt(document.getElementById("max-runs")?.value),
      context_profile: document.getElementById("context-profile")?.value || "minimal",
      scanners_enabled: document.getElementById("scanners-enabled")?.checked ?? true,
      simulation_slippage_bps: +(document.getElementById("sim-slippage")?.value || 0),
      simulation_commission_per_order: +(document.getElementById("sim-commission")?.value || 0),
      simulation_settlement_days: +(document.getElementById("sim-settlement")?.value || 0),
  };
  await api(`${settingsBase}/app`, {
    method: "PUT",
    body: JSON.stringify(appPayload),
  });
  toast(t("bot.configSaved"));
  if (isSubscriptionModel(modelValue)) {
    toast(t("bot.modelSubscriptionWarning"));
  }
  refresh();
  refreshPortfolio();
}

async function runNow() {
  await api(`/api/bots/${encodeURIComponent(botId)}/run-now`, { method: "POST", body: "{}" });
  toast(t("bot.cycleStarted"));
  refresh();
  refreshPortfolio();
}

async function togglePause() {
  if (!started) return toast(t("bot.schedulerStartFirst"));
  if (paused) await api(`/api/bots/${encodeURIComponent(botId)}/resume`, { method: "POST", body: "{}" });
  else await api(`/api/bots/${encodeURIComponent(botId)}/pause`, { method: "POST", body: "{}" });
  refresh();
}

async function toggleStart() {
  if (started) await api(`/api/bots/${encodeURIComponent(botId)}/stop`, { method: "POST", body: "{}" });
  else await api(`/api/bots/${encodeURIComponent(botId)}/start`, { method: "POST", body: "{}" });
  refresh();
}

window.onProfileGateAcknowledged = () => {
  refresh();
  refreshPortfolio();
};

function bootstrapDashboard() {
  document.getElementById("agents-link").href = botPath(botId, "agents");
  document.getElementById("all-runs-link").href = botPath(botId, "agents");
  setupBotIdField();

  document.querySelectorAll("#bot-tabs .nav-link").forEach((btn) => {
    btn.addEventListener("click", () => showTab(btn.dataset.tab));
  });

  const initialTab =
    new URLSearchParams(location.search).get("tab") === "config" ? "config" : "overview";
  showTab(initialTab);

  connectWs((msg) => {
    if (msg.type === "agent_event" && msg.bot_id === botId) debouncedRefresh();
  });

  refresh();
  refreshPortfolio();
  setInterval(refresh, 30000);
  setInterval(refreshPortfolio, 60000);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => whenI18nReady(bootstrapDashboard));
} else {
  whenI18nReady(bootstrapDashboard);
}
