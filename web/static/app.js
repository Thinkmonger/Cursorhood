function toast(msg) {
  const bsToast = document.getElementById("toast");
  if (bsToast && typeof bootstrap !== "undefined") {
    const body = bsToast.querySelector(".toast-body");
    if (body) body.textContent = msg;
    bootstrap.Toast.getOrCreateInstance(bsToast, { delay: 3000 }).show();
    return;
  }
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    el.className = "toast show";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 3000);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail?.error || data.detail || res.statusText);
  return data;
}

async function restartServer() {
  if (!confirm(t("system.restartConfirm"))) return;
  try {
    await api("/api/system/restart", { method: "POST", body: "{}" });
    toast(t("system.restartToast"));
    setTimeout(() => location.reload(), 2500);
  } catch (e) {
    const msg = String(e.message);
    if (/not found/i.test(msg)) {
      toast(t("system.restartApiMissing"));
    } else {
      toast(msg);
    }
  }
}

function connectWs(onMessage) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (ev) => {
    try {
      onMessage(JSON.parse(ev.data));
    } catch (_) {}
  };
  ws.onclose = () => setTimeout(() => connectWs(onMessage), 2000);
  return ws;
}

function startVisibleInterval(fn, ms) {
  return setInterval(() => {
    if (document.visibilityState === "visible") fn();
  }, ms);
}

function homePath(tab) {
  return tab === "config" ? "/?tab=config" : "/";
}

function isNavActive(key, path) {
  if (key === "dashboard") {
    return path === "/" || path === "/dashboard" || /^\/bots\/[^/]+/.test(path);
  }
  if (key === "statistics") return path.startsWith("/statistics");
  if (key === "research") return path.startsWith("/research");
  return false;
}

function initMainNav(options = {}) {
  const ul = document.getElementById("app-nav");
  if (!ul) return;

  const items = [
    { key: "dashboard", href: "/", label: t("nav.dashboard") },
    { key: "research", href: "/research", label: t("nav.research") },
    { key: "statistics", href: "/statistics", label: t("nav.statistics") },
  ];

  const path = location.pathname;
  let html = items
    .map((item) => {
      const active = isNavActive(item.key, path);
      return `<li class="nav-item"><a class="nav-link${active ? " active" : ""}" href="${item.href}" data-nav="${item.key}">${item.label}</a></li>`;
    })
    .join("");

  if (options.botSwitcher) {
    html += `<li class="nav-item ms-lg-2"><select id="bot-switcher" class="form-select form-select-sm bg-dark text-light border-secondary" style="min-width: 10rem;"></select></li>`;
  }

  ul.innerHTML = html;
  if (options.botSwitcher) populateBotSwitcher("bot-switcher");
}

function rememberBotId(botId) {
  if (botId) sessionStorage.setItem("lastBotId", botId);
}

function getBotIdFromPath() {
  const parts = location.pathname.split("/").filter(Boolean);
  if (parts[0] === "bots" && parts[1]) {
    rememberBotId(parts[1]);
    return parts[1];
  }
  return sessionStorage.getItem("lastBotId") || "default";
}

function botPath(botId, sub) {
  rememberBotId(botId);
  return sub ? `/bots/${encodeURIComponent(botId)}/${sub}` : `/bots/${encodeURIComponent(botId)}`;
}

function schedulerStatusLabel(sched) {
  if (!sched?.started) return t("scheduler.statusStopped");
  if (sched.paused) return t("scheduler.statusPaused");
  if (sched.running || sched.running_cycle) return t("scheduler.statusRunningCycle");
  return t("scheduler.statusActive");
}

function formatNextScheduledRun(sched) {
  if (!sched?.started || sched.paused || !sched.next_scheduled_run_at) return t("common.emDash");
  return formatRunTime(sched.next_scheduled_run_at);
}

function formatMoney(value, digits = 2) {
  if (value == null || Number.isNaN(Number(value))) return t("common.emDash");
  return `$${Number(value).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

function formatPct(value, digits = 2) {
  if (value == null || Number.isNaN(Number(value))) return t("common.emDash");
  const n = Number(value);
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(digits)}%`;
}

function renderRiskLimitsHtml(limits) {
  if (!limits || typeof limits !== "object") {
    return `<p class="text-secondary small mb-0">${escapeHtml(t("limits.empty"))}</p>`;
  }

  const symbols = limits.allowed_symbols || [];
  const symbolsValue = symbols.length
    ? escapeHtml(symbols.join(", "))
    : `<span class="text-secondary">${escapeHtml(t("limits.allSymbols"))}</span>`;

  const marketHours = limits.market_hours_only
    ? `<span class="text-success">${escapeHtml(t("common.yes"))}</span>`
    : `<span class="text-secondary">${escapeHtml(t("common.no"))}</span>`;

  const minGap =
    limits.min_seconds_between_orders != null
      ? t("limits.secondsSuffix", { n: Number(limits.min_seconds_between_orders).toLocaleString() })
      : t("common.emDash");

  const rows = [
    [t("limits.maxOrder"), escapeHtml(formatMoney(limits.max_order_notional_usd))],
    [t("limits.maxDailyLoss"), escapeHtml(formatMoney(limits.max_daily_loss_usd))],
    [t("limits.allowedSymbols"), symbolsValue],
    [
      t("limits.maxOpenPositions"),
      limits.max_open_positions != null ? escapeHtml(String(limits.max_open_positions)) : t("common.emDash"),
    ],
    [t("limits.marketHoursOnly"), marketHours],
    [t("limits.minTimeBetweenOrders"), escapeHtml(minGap)],
  ];

  return `<dl class="summary-dl mb-0">${rows
    .map(([label, value]) => `<dt>${escapeHtml(label)}</dt><dd>${value}</dd>`)
    .join("")}</dl>`;
}

function plTextClass(value) {
  if (value == null || Number(value) === 0) return "text-secondary";
  return Number(value) > 0 ? "text-success" : "text-danger";
}

function schedulerBadgeClass(sched) {
  if (!sched?.started) return "text-bg-secondary";
  if (sched.paused) return "text-bg-warning text-dark";
  if (sched.running || sched.running_cycle) return "text-bg-success";
  return "text-bg-primary";
}

function assetClassBadge(assetClass) {
  const key = String(assetClass || "equity");
  const label = {
    equity: t("dashboard.assetEquity"),
    option: t("dashboard.assetOption"),
    crypto: t("dashboard.assetCrypto"),
  }[key] || key;
  return `<span class="badge text-bg-secondary">${escapeHtml(label)}</span>`;
}

function runStatusLabel(status) {
  const key = String(status || "").toLowerCase();
  const map = {
    finished: t("run.statusFinished"),
    error: t("run.statusError"),
    running: t("run.statusRunning"),
    cancelled: t("run.statusCancelled"),
  };
  return map[key] || (status ? String(status) : t("common.emDash"));
}

function runStatusBadge(status) {
  const key = String(status || "").toLowerCase();
  const cls = {
    finished: "text-bg-success",
    error: "text-bg-danger",
    running: "text-bg-primary",
    cancelled: "text-bg-secondary",
  }[key] || "text-bg-secondary";
  return `<span class="badge ${cls}">${escapeHtml(runStatusLabel(status))}</span>`;
}

async function populateBotSwitcher(selectId) {
  const el = document.getElementById(selectId);
  if (!el) return;
  const current = getBotIdFromPath();
  try {
    const { bots } = await api("/api/bots");
    el.innerHTML = bots
      .map(
        (b) =>
          `<option value="${escapeHtml(b.id)}"${b.id === current ? " selected" : ""}>${escapeHtml(b.name)}</option>`
      )
      .join("");
    el.onchange = () => {
      const botId = el.value;
      rememberBotId(botId);
      const path = location.pathname;
      const tab = new URLSearchParams(location.search).get("tab");
      if (path.includes("/agents")) location.href = botPath(botId, "agents");
      else if (tab === "config") location.href = `${botPath(botId)}?tab=config`;
      else location.href = botPath(botId);
    };
  } catch (_) {
    el.innerHTML = `<option value="${current}">${current}</option>`;
  }
}

function markActiveNav() {
  const nav = document.getElementById("app-nav");
  initMainNav({ botSwitcher: nav?.dataset?.botSwitcher === "true" });
}

document.addEventListener("DOMContentLoaded", async () => {
  try {
    await initI18n();
  } catch (err) {
    console.error("i18n init failed", err);
    window.__i18nReady = true;
    document.dispatchEvent(new CustomEvent("i18n:ready"));
  }
  markActiveNav();
});

const TRADING_TOOL_LABELS = {
  // Account
  get_portfolio: "activity.toolGetPortfolio",
  get_accounts: "activity.toolGetAccounts",
  get_realized_pnl: "activity.toolGetRealizedPnl",
  get_pnl_trade_history: "activity.toolGetPnlTradeHistory",
  search: "activity.toolSearch",
  // Watchlists
  get_watchlists: "activity.toolGetWatchlists",
  get_watchlist_items: "activity.toolGetWatchlistItems",
  get_option_watchlist: "activity.toolGetOptionWatchlist",
  get_popular_watchlists: "activity.toolGetPopularWatchlists",
  create_watchlist: "activity.toolCreateWatchlist",
  update_watchlist: "activity.toolUpdateWatchlist",
  follow_watchlist: "activity.toolFollowWatchlist",
  unfollow_watchlist: "activity.toolUnfollowWatchlist",
  add_to_watchlist: "activity.toolAddToWatchlist",
  remove_from_watchlist: "activity.toolRemoveFromWatchlist",
  add_option_to_watchlist: "activity.toolAddOptionToWatchlist",
  remove_option_from_watchlist: "activity.toolRemoveOptionFromWatchlist",
  // Market data
  get_equity_historicals: "activity.toolGetEquityHistoricals",
  get_equity_fundamentals: "activity.toolGetEquityFundamentals",
  get_financials: "activity.toolGetFinancials",
  get_equity_price_book: "activity.toolGetEquityPriceBook",
  get_equity_technical_indicators: "activity.toolGetTechnicalIndicators",
  get_earnings_results: "activity.toolGetEarningsResults",
  get_earnings_calendar: "activity.toolGetEarningsCalendar",
  get_indexes: "activity.toolGetIndexes",
  get_index_quotes: "activity.toolGetIndexQuotes",
  get_index_historicals: "activity.toolGetIndexHistoricals",
  get_equity_news: "activity.toolGetEquityNews",
  get_equity_analyst_ratings: "activity.toolGetAnalystRatings",
  get_politician_trades: "activity.toolGetPoliticianTrades",
  get_sec_filing: "activity.toolGetSecFiling",
  get_sec_filing_index: "activity.toolGetSecFilingIndex",
  get_sec_filing_facts: "activity.toolGetSecFilingFacts",
  get_sec_filing_facts_catalog: "activity.toolGetSecFilingFactsCatalog",
  // Equities
  get_equity_positions: "activity.toolGetEquityPositions",
  get_equity_tax_lots: "activity.toolGetEquityTaxLots",
  get_equity_quotes: "activity.toolGetEquityQuotes",
  get_equity_orders: "activity.toolGetEquityOrders",
  get_equity_tradability: "activity.toolGetEquityTradability",
  review_equity_order: "activity.toolReviewEquityOrder",
  place_equity_order: "activity.toolPlaceEquityOrder",
  cancel_equity_order: "activity.toolCancelEquityOrder",
  get_limited_margin_upgrade_info: "activity.toolGetMarginUpgradeInfo",
  get_advanced_orders: "activity.toolGetAdvancedOrders",
  review_advanced_order: "activity.toolReviewAdvancedOrder",
  place_advanced_order: "activity.toolPlaceAdvancedOrder",
  cancel_advanced_order: "activity.toolCancelAdvancedOrder",
  // Options
  get_option_level_upgrade_info: "activity.toolGetOptionLevelUpgradeInfo",
  get_option_historicals: "activity.toolGetOptionHistoricals",
  get_option_chains: "activity.toolGetOptionChains",
  get_option_instruments: "activity.toolGetOptionInstruments",
  get_option_quotes: "activity.toolGetOptionQuotes",
  get_option_positions: "activity.toolGetOptionPositions",
  get_option_orders: "activity.toolGetOptionOrders",
  review_option_order: "activity.toolReviewOptionOrder",
  place_option_order: "activity.toolPlaceOptionOrder",
  cancel_option_order: "activity.toolCancelOptionOrder",
  exercise_option: "activity.toolExerciseOption",
  cancel_option_exercise: "activity.toolCancelOptionExercise",
  // Crypto
  get_currency_pairs: "activity.toolGetCurrencyPairs",
  get_crypto_account_onboarding_info: "activity.toolGetCryptoOnboarding",
  get_crypto_quotes: "activity.toolGetCryptoQuotes",
  get_crypto_positions: "activity.toolGetCryptoPositions",
  get_crypto_orders: "activity.toolGetCryptoOrders",
  preview_crypto_order: "activity.toolPreviewCryptoOrder",
  place_crypto_order: "activity.toolPlaceCryptoOrder",
  cancel_crypto_order: "activity.toolCancelCryptoOrder",
  // Scanners
  get_scans: "activity.toolGetScans",
  get_scanner_filter_specs: "activity.toolGetScannerFilterSpecs",
  create_scan: "activity.toolCreateScan",
  run_scan: "activity.toolRunScan",
  update_scan_filters: "activity.toolUpdateScanFilters",
  update_scan_config: "activity.toolUpdateScanConfig",
  // Alerts
  get_alerts: "activity.toolGetAlerts",
  create_alert: "activity.toolCreateAlert",
  update_alert: "activity.toolUpdateAlert",
  delete_alert: "activity.toolDeleteAlert",
  get_alert_log: "activity.toolGetAlertLog",
  mark_alerts_read: "activity.toolMarkAlertsRead",
};

const STATUS_ONLY_TOOLS = new Set(["log_event", "get_last_cycle"]);

function debounce(fn, ms) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function formatRunStatus(status) {
  const map = {
    running: t("run.statusRunning"),
    finished: t("run.statusFinished"),
    error: t("run.statusError"),
    cancelled: t("run.statusCancelled"),
  };
  return map[status] || status;
}

function formatRunTime(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch (_) {
    return iso;
  }
}

function portfolioEquity(snapshot) {
  if (!snapshot || !snapshot.ok) return null;
  const p = snapshot.portfolio;
  if (!p) return null;
  if (typeof p === "object") {
    const inner = p.result || p;
    return inner.equity ?? inner.portfolio_equity ?? inner.market_value ?? null;
  }
  return null;
}

/** Keep only the lines worth showing in Activity. */
function compactEventsForDisplay(events) {
  const seenTools = new Set();
  const out = [];

  for (const ev of events) {
    const type = ev.type || "message";
    const payload = ev.payload || {};

    if (type === "assistant_text" || type === "tool_result" || type === "message") continue;
    if (type === "cursor_run" || type === "thinking" || type === "status") continue;

    if (type === "tool_call") {
      const name = payload.name || "";
      if (STATUS_ONLY_TOOLS.has(name)) continue;
      const key = name + JSON.stringify(payload.input || {});
      if (seenTools.has(key)) continue;
      seenTools.add(key);
      out.push(ev);
      continue;
    }

    out.push(ev);
  }
  return out;
}

function renderSummary(run) {
  if (!run) return t("run.summarySelectRun");
  if (run.error) return `Error\n\n${run.error}`;
  if (run.summary) {
    try {
      const j = JSON.parse(run.summary);
      const lines = [];
      if (j.action != null) lines.push(`${t("run.labelAction")}: ${j.action}`);
      if (j.portfolio_value != null) lines.push(`${t("run.labelPortfolio")}: $${j.portfolio_value}`);
      if (j.symbols?.length) lines.push(`${t("run.labelSymbols")}: ${j.symbols.join(", ")}`);
      if (j.reason) lines.push("", j.reason);
      if (lines.length) return lines.join("\n");
    } catch (_) {}
    return run.summary;
  }
  if (run.status === "running") return t("run.summaryInProgress");
  return formatRunStatus(run.status);
}

function splitSummaryParts(summary) {
  if (!summary) return { json: null, notes: "" };
  const sep = summary.indexOf("\n\n---\n\n");
  if (sep >= 0) {
    const head = summary.slice(0, sep).trim();
    const tail = summary.slice(sep + 7).trim();
    try {
      return { json: JSON.parse(head), notes: tail };
    } catch (_) {
      return { json: null, notes: summary };
    }
  }
  try {
    return { json: JSON.parse(summary), notes: "" };
  } catch (_) {
    return { json: null, notes: summary };
  }
}

function renderSummaryHumanBlock(json) {
  const rows = [];
  if (json.action != null) {
    rows.push(
      `<dt>${escapeHtml(t("run.labelAction"))}</dt><dd class="text-uppercase fw-semibold">${escapeHtml(String(json.action))}</dd>`
    );
  }
  if (json.portfolio_value != null) {
    rows.push(
      `<dt>${escapeHtml(t("run.labelPortfolio"))}</dt><dd>$${escapeHtml(String(json.portfolio_value))}</dd>`
    );
  }
  if (json.symbols?.length) {
    rows.push(
      `<dt>${escapeHtml(t("run.labelSymbols"))}</dt><dd>${escapeHtml(json.symbols.join(", "))}</dd>`
    );
  }
  if (json.reason) {
    rows.push(`<dt>${escapeHtml(t("run.labelReason"))}</dt><dd class="summary-prose">${escapeHtml(json.reason)}</dd>`);
  }
  if (!rows.length) return "";
  return `<dl class="summary-dl mb-0">${rows.join("")}</dl>`;
}

function renderSummaryContent(run) {
  if (!run) {
    return `<p class="text-secondary mb-0">${escapeHtml(t("run.summarySelectRun"))}</p>`;
  }
  if (run.error && !run.summary) {
    return `<div class="alert alert-danger py-2 mb-0">${escapeHtml(run.error)}</div>`;
  }
  if (!run.summary) {
    if (run.status === "running") {
      return `<p class="text-secondary mb-0">${escapeHtml(t("run.summaryInProgress"))}</p>`;
    }
    if (run.error) {
      return `<div class="alert alert-danger py-2 mb-0">${escapeHtml(run.error)}</div>`;
    }
    return `<p class="text-secondary mb-0">${escapeHtml(formatRunStatus(run.status))}</p>`;
  }

  const { json, notes } = splitSummaryParts(run.summary);
  const parts = [];

  if (json && typeof json === "object") {
    const human = renderSummaryHumanBlock(json);
    if (human) parts.push(human);
    parts.push(
      `<details class="summary-raw mt-3"><summary class="text-secondary small">${escapeHtml(t("run.summaryRawJson"))}</summary><pre class="summary-json small mb-0 mt-2">${escapeHtml(JSON.stringify(json, null, 2))}</pre></details>`
    );
  } else {
    const text = renderSummary(run);
    parts.push(
      `<div class="summary-prose">${escapeHtml(text).replace(/\n/g, "<br>")}</div>`
    );
    if (run.summary.trim().startsWith("{")) {
      parts.push(
        `<details class="summary-raw mt-3"><summary class="text-secondary small">${escapeHtml(t("run.summaryRawOutput"))}</summary><pre class="summary-json small mb-0 mt-2">${escapeHtml(run.summary)}</pre></details>`
      );
    }
  }

  if (notes) {
    parts.push(
      `<div class="summary-notes mt-3 pt-3 border-top border-secondary-subtle"><div class="stat-label mb-1">${escapeHtml(t("run.summaryAgentNotes"))}</div><div class="summary-prose">${escapeHtml(notes).replace(/\n/g, "<br>")}</div></div>`
    );
  }

  if (run.error) {
    parts.push(
      `<div class="alert alert-danger py-2 mt-3 mb-0 small">${escapeHtml(run.error)}</div>`
    );
  }

  return parts.join("") || `<p class="text-secondary mb-0">${escapeHtml(t("run.summaryNone"))}</p>`;
}

function renderCompactEvent(ev) {
  const type = ev.type || "message";
  const payload = ev.payload || {};
  const div = document.createElement("div");
  div.className = `timeline-item ${type}`;

  if (type === "cursor_run" || type === "thinking" || type === "status") {
    return null;
  }

  if (type === "run_start") {
    div.innerHTML = `<span class="timeline-icon"><i class="bi bi-play-fill"></i></span><span>${escapeHtml(t("activity.runStart", { trigger: payload.trigger || "manual" }))}</span>`;
    return div;
  }

  if (type === "run_end") {
    if (payload.error) div.classList.add("is-error");
    const text = payload.error
      ? escapeHtml(t("activity.runEndFailed", { error: payload.error }))
      : escapeHtml(t("activity.runEndFinished", { status: formatRunStatus(payload.status || "finished") }));
    div.innerHTML = `<span class="timeline-icon"><i class="bi bi-stop-fill"></i></span><span>${text}</span>`;
    return div;
  }

  if (type === "portfolio_snapshot") {
    if (!payload.ok) {
      div.classList.add("is-error");
      div.innerHTML = `<span class="timeline-icon"><i class="bi bi-exclamation-triangle"></i></span><span>${escapeHtml(t("activity.portfolioPrefetchFailed", { error: payload.error || "?" }))}</span>`;
      return div;
    }
    if (payload.mode === "cursor_mcp" && payload.prefetch === false) {
      div.innerHTML = `<span class="timeline-icon"><i class="bi bi-box-arrow-up-right"></i></span><span>${escapeHtml(t("activity.portfolioViaMcp"))}</span>`;
      return div;
    }
    const eq = portfolioEquity(payload);
    div.innerHTML = `<span class="timeline-icon"><i class="bi bi-currency-dollar"></i></span><span>${escapeHtml(t("activity.portfolioLoaded"))}${eq != null ? ` · <strong>$${eq}</strong>` : ""}</span>`;
    return div;
  }

  if (type === "tool_call") {
    const name = payload.name || "?";
    const labelKey = TRADING_TOOL_LABELS[name];
    const label = labelKey ? t(labelKey) : name;
    const syms = payload.input?.symbols;
    const extra = Array.isArray(syms) ? ` <span class="text-secondary">(${escapeHtml(syms.join(", "))})</span>` : "";
    div.innerHTML = `<span class="timeline-icon"><i class="bi bi-arrow-right"></i></span><span>${escapeHtml(label)}${extra}</span>`;
    return div;
  }

  if (type === "status_log") {
    const action = payload.action || "none";
    const pv = payload.portfolio_value != null ? `$${payload.portfolio_value}` : "—";
    div.innerHTML = `<span class="timeline-icon"><i class="bi bi-diamond-fill"></i></span><span><span class="badge text-bg-warning text-dark me-1">${escapeHtml(action.toUpperCase())}</span> Portfolio ${escapeHtml(String(pv))}<div class="text-secondary small mt-1">${escapeHtml(payload.reason || "")}</div></span>`;
    return div;
  }

  if (type === "hook_deny") {
    div.classList.add("is-error");
    div.innerHTML = `<span class="timeline-icon"><i class="bi bi-shield-x"></i></span><span>${escapeHtml(payload.message || t("activity.hookDenyDefault"))}</span>`;
    return div;
  }

  div.innerHTML = `<span class="timeline-icon">·</span><span>${escapeHtml(type)}</span>`;
  return div;
}

function runDisplayNumber(run) {
  if (run && run.run_number != null && run.run_number !== "") return run.run_number;
  return run?.id ?? "?";
}

function runStatusBadgeClass(status) {
  if (status === "running") return "text-bg-success";
  if (status === "error") return "text-bg-danger";
  if (status === "finished") return "text-bg-primary";
  return "text-bg-secondary";
}

function renderRunListItem(run, selectedId) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className =
    "list-group-item list-group-item-action rh-run-item" +
    (run.id === selectedId ? " active" : "");
  btn.innerHTML = `
    <div class="d-flex justify-content-between align-items-start gap-2">
      <strong>#${runDisplayNumber(run)}</strong>
      <span class="badge ${runStatusBadgeClass(run.status)}">${formatRunStatus(run.status)}</span>
    </div>
    <div class="small text-secondary mt-1">${escapeHtml(run.trigger)} · ${formatRunTime(run.started_at)}</div>`;
  return btn;
}

function renderDashboardRunItem(run, botId) {
  const bid = botId || run.bot_id || getBotIdFromPath();
  const a = document.createElement("a");
  a.href = `${botPath(bid, "agents")}?run=${run.id}`;
  a.className = "list-group-item list-group-item-action";
  a.innerHTML = `
    <div class="d-flex justify-content-between align-items-start gap-2">
      <strong>#${runDisplayNumber(run)}</strong>
      <span class="badge ${runStatusBadgeClass(run.status)}">${formatRunStatus(run.status)}</span>
    </div>
    <div class="small text-secondary">${escapeHtml(run.trigger)} · ${formatRunTime(run.started_at)}</div>`;
  const summary = run.summary || run.error || "";
  if (summary) {
    const snippet = summary.length > 120 ? summary.slice(0, 117) + "…" : summary;
    a.innerHTML += `<div class="small text-secondary mt-1 text-truncate">${escapeHtml(snippet)}</div>`;
  }
  return a;
}

function paintActivityStream(streamEl, events) {
  streamEl.innerHTML = "";
  const compact = compactEventsForDisplay(events);
  if (!compact.length) {
    streamEl.innerHTML = `<p class="text-secondary small mb-0">${escapeHtml(t("activity.empty"))}</p>`;
    return;
  }
  compact.forEach((ev) => {
    const node = renderCompactEvent(ev);
    if (node) streamEl.appendChild(node);
  });
}

function createLiveActivityAppender(streamEl, onAppended) {
  const seenTools = new Set();
  return function appendLive(ev) {
    const type = ev.type || "message";
    if (type === "assistant_text" || type === "tool_result" || type === "message") return;
    if (type === "cursor_run" || type === "thinking" || type === "status") return;

    if (type === "tool_call") {
      const name = ev.payload?.name || "";
      if (STATUS_ONLY_TOOLS.has(name)) return;
      const key = name + JSON.stringify(ev.payload?.input || {});
      if (seenTools.has(key)) return;
      seenTools.add(key);
    }

    const node = renderCompactEvent(ev);
    if (node) streamEl.appendChild(node);
    if (typeof onAppended === "function") onAppended();
  };
}

function renderProfileGateBanner(gate, containerId) {
  const el = document.getElementById(containerId);
  if (!el) return;

  if (!gate || !gate.active) {
    el.classList.add("d-none");
    el.innerHTML = "";
    return;
  }

  const steps = (gate.steps || [])
    .map((s, i) => `<li>${escapeHtml(s)}</li>`)
    .join("");

  el.className = "alert alert-warning mx-0 mb-0 rounded-0 border-0 border-bottom border-warning";
  el.innerHTML = `
    <div class="d-flex flex-wrap align-items-start gap-3">
      <div class="flex-grow-1">
        <strong><i class="bi bi-exclamation-triangle me-1"></i>${escapeHtml(gate.title || t("profileGate.titleDefault"))}</strong>
        <p class="mb-2 mt-1 small">${escapeHtml(gate.message || "")}</p>
        <p class="mb-2 small text-secondary">${escapeHtml(gate.why_link_fails || "")}</p>
        <ol class="small mb-0 ps-3">${steps}</ol>
      </div>
      <button type="button" class="btn btn-sm btn-warning" id="profile-gate-ack-btn">
        ${escapeHtml(t("profileGate.ackButton"))}
      </button>
    </div>`;

  el.classList.remove("d-none");

  const btn = document.getElementById("profile-gate-ack-btn");
  if (btn && !btn.dataset.bound) {
    btn.dataset.bound = "1";
    btn.addEventListener("click", async () => {
      try {
        const botId = getBotIdFromPath();
        await api(`/api/trading/profile-gate/acknowledge?bot_id=${encodeURIComponent(botId)}`, {
          method: "POST",
          body: "{}",
        });
        toast(t("profileGate.acknowledged"));
        if (typeof window.onProfileGateAcknowledged === "function") {
          window.onProfileGateAcknowledged();
        }
      } catch (e) {
        toast(String(e.message));
      }
    });
  }
}
