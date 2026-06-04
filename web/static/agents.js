/** Agents page — per-bot run history */

const botId = getBotIdFromPath();
let selectedRun = null;
let followLive = true;
let pinnedRun = false;
let runsCache = [];

const streamEl = document.getElementById("stream");
const activityScroll = document.getElementById("activity-scroll");
let autoScroll = true;

document.getElementById("back-dash").href = "/";

whenI18nReady(() => {
  api(`/api/bots/${encodeURIComponent(botId)}`)
    .then(({ bot }) => {
      document.getElementById("bot-label").textContent = bot?.name || botId;
    })
    .catch(() => {
      document.getElementById("bot-label").textContent = botId;
    });
});

activityScroll.addEventListener("scroll", () => {
  autoScroll =
    activityScroll.scrollHeight - activityScroll.scrollTop - activityScroll.clientHeight < 64;
});

document.getElementById("follow").addEventListener("change", (e) => {
  followLive = e.target.checked;
  if (followLive) {
    pinnedRun = false;
    refreshRuns();
  }
});

function scrollActivityToBottom() {
  if (autoScroll) activityScroll.scrollTop = activityScroll.scrollHeight;
}

function parseSummaryJson(run) {
  if (!run?.summary) return null;
  try {
    return JSON.parse(run.summary);
  } catch (_) {
    return null;
  }
}

function updateSummaryPanel(run) {
  const meta = document.getElementById("summary-meta");
  const body = document.getElementById("summary");
  const stats = document.getElementById("summary-stats");

  if (!run) {
    meta.textContent = t("agents.selectRunTitle");
    body.innerHTML = `<p class="text-secondary mb-0">${escapeHtml(t("agents.selectRunBody"))}</p>`;
    stats.innerHTML = "";
    return;
  }

  meta.textContent = `#${runDisplayNumber(run)} · ${run.trigger} · ${formatRunTime(run.started_at)}`;
  body.innerHTML = renderSummaryContent(run);

  const j = parseSummaryJson(run);
  if (j && j.action != null) {
    stats.innerHTML = `
      <div class="col-4">
        <div class="border rounded p-2">
          <div class="stat-label">${escapeHtml(t("run.labelAction"))}</div>
          <div class="stat-value text-uppercase">${escapeHtml(String(j.action))}</div>
        </div>
      </div>
      <div class="col-4">
        <div class="border rounded p-2">
          <div class="stat-label">${escapeHtml(t("run.labelPortfolio"))}</div>
          <div class="stat-value">${j.portfolio_value != null ? "$" + escapeHtml(String(j.portfolio_value)) : t("common.emDash")}</div>
        </div>
      </div>
      <div class="col-4">
        <div class="border rounded p-2">
          <div class="stat-label">${escapeHtml(t("run.labelSymbols"))}</div>
          <div class="stat-value small">${j.symbols?.length ? escapeHtml(j.symbols.join(", ")) : t("common.emDash")}</div>
        </div>
      </div>`;
  } else if (run.error) {
    stats.innerHTML = `<div class="col-12"><div class="alert alert-danger py-2 mb-0 small">${escapeHtml(run.error)}</div></div>`;
  } else {
    stats.innerHTML = `<div class="col-12"><span class="badge ${runStatusBadgeClass(run.status)}">${formatRunStatus(run.status)}</span></div>`;
  }
}

function paintRunList() {
  const list = document.getElementById("run-list");
  list.innerHTML = "";
  runsCache.forEach((r) => {
    const item = renderRunListItem(r, selectedRun);
    item.addEventListener("click", () => selectRun(r.id, { userClick: true }));
    list.appendChild(item);
  });
  document.getElementById("run-count").textContent = String(runsCache.length);

  const active = runsCache.find((r) => r.status === "running");
  const state = document.getElementById("agent-state");
  state.textContent = active ? t("agents.stateRunning") : t("agents.stateIdle");
  state.className = "badge rounded-pill " + (active ? "text-bg-success" : "text-bg-secondary");
}

async function refreshProfileGate() {
  try {
    const gate = await api(
      `/api/trading/profile-gate?bot_id=${encodeURIComponent(botId)}`
    );
    renderProfileGateBanner(gate, "profile-gate-banner");
  } catch (_) {}
}

window.onProfileGateAcknowledged = () => {
  refreshProfileGate();
  refreshRuns();
};

async function refreshRuns() {
  const { runs } = await api(
    `/api/bots/${encodeURIComponent(botId)}/runs?limit=30`
  );
  runsCache = runs;

  if (followLive && !pinnedRun) {
    const active = runs.find((r) => r.status === "running");
    if (active && active.id !== selectedRun) {
      await selectRun(active.id, { keepStream: false, fromFollow: true });
      paintRunList();
      return;
    }
  }

  paintRunList();

  if (selectedRun) {
    const cached = runs.find((r) => r.id === selectedRun);
    if (cached) {
      await loadRunDetail(selectedRun, { keepStream: true, updateStream: false });
    }
  } else if (runs[0]) {
    await selectRun(runs[0].id, { fromFollow: true });
  }
}

async function loadRunDetail(id, opts = {}) {
  const { keepStream = false, updateStream = true } = opts;
  const data = await api(
    `/api/bots/${encodeURIComponent(botId)}/runs/${id}`
  );
  updateSummaryPanel(data.run);
  if (updateStream && !keepStream) {
    paintActivityStream(streamEl, data.events);
    scrollActivityToBottom();
  }
  return data;
}

async function selectRun(id, opts = {}) {
  const { keepStream = false, userClick = false, fromFollow = false } = opts;
  selectedRun = id;
  sessionStorage.setItem(`agentsSelectedRun:${botId}`, String(id));

  if (userClick) pinnedRun = true;
  else if (fromFollow && followLive) pinnedRun = false;

  history.replaceState(null, "", `?run=${id}`);

  if (!keepStream) {
    streamEl.innerHTML = "";
    autoScroll = true;
  }

  await loadRunDetail(id, { keepStream, updateStream: !keepStream });
  paintRunList();
}

async function runNow() {
  const { run_id, run_number } = await api(
    `/api/bots/${encodeURIComponent(botId)}/run-now`,
    { method: "POST", body: "{}" }
  );
  pinnedRun = false;
  followLive = true;
  document.getElementById("follow").checked = true;
  selectedRun = run_id;
  streamEl.innerHTML = "";
  autoScroll = true;
  toast(t("agents.runNowStarted", { runNumber: run_number ?? run_id }));
  await refreshRuns();
}

const debouncedRefresh = debounce(refreshRuns, 2500);

const appendLive = createLiveActivityAppender(streamEl, () => {
  scrollActivityToBottom();
  return autoScroll;
});

connectWs((msg) => {
  if (msg.type !== "agent_event") return;
  if (msg.bot_id && msg.bot_id !== botId) return;

  const isSelected = msg.run_id === selectedRun;

  if (followLive && !pinnedRun && msg.run_id !== selectedRun) {
    selectRun(msg.run_id, { keepStream: false, fromFollow: true });
  } else if (isSelected) {
    appendLive(msg.event);
  }

  if (msg.event?.type === "run_end" && msg.run_id === selectedRun) {
    loadRunDetail(selectedRun, { keepStream: true, updateStream: false });
  }

  const idx = runsCache.findIndex((r) => r.id === msg.run_id);
  if (idx >= 0 && msg.event?.type === "run_end") {
    runsCache[idx].status = msg.event.payload?.status === "error" ? "error" : "finished";
    paintRunList();
  }

  debouncedRefresh();
  refreshProfileGate();
});

const params = new URLSearchParams(location.search);
const stored = sessionStorage.getItem(`agentsSelectedRun:${botId}`);
if (params.get("run")) {
  selectedRun = +params.get("run");
  pinnedRun = true;
} else if (stored) {
  selectedRun = +stored;
  pinnedRun = true;
}

whenI18nReady(() => {
  refreshProfileGate();
  refreshRuns();
  setInterval(refreshRuns, 20000);
});
