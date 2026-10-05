/** Shared app footer — centered tech stack + live Cursor / local usage stats */

function renderFooterBadges(technologies) {
  return (technologies || [])
    .map((tech) => {
      const label = escapeHtml(tech.label);
      if (tech.url) {
        return `<a class="app-footer-badge" href="${escapeHtml(tech.url)}" target="_blank" rel="noopener noreferrer" title="${label}">${label}</a>`;
      }
      return `<span class="app-footer-badge" title="${label}">${label}</span>`;
    })
    .join("");
}

function renderFooterStats(data) {
  const local = data.local || {};
  const cursor = data.cursor || {};
  const stats = [];

  if (cursor.ok && cursor.cloud_agents != null) {
    stats.push(
      `<span class="app-footer-stat"><span class="label">${escapeHtml(t("footer.cloudAgents"))}</span> ${cursor.cloud_agents}</span>`
    );
  }

  stats.push(
    `<span class="app-footer-stat"><span class="label">${escapeHtml(t("footer.botCycles"))}</span> ${local.total_runs ?? 0}</span>`
  );
  stats.push(
    `<span class="app-footer-stat"><span class="label">${escapeHtml(t("footer.today"))}</span> ${local.runs_today ?? 0}</span>`
  );
  stats.push(
    `<span class="app-footer-stat"><span class="label">${escapeHtml(t("footer.mcpCalls"))}</span> ${local.tool_calls ?? 0}</span>`
  );
  stats.push(
    `<span class="app-footer-stat"><span class="label">${escapeHtml(t("footer.linkedRuns"))}</span> ${local.cursor_linked_runs ?? 0}</span>`
  );

  const tokens = local.tokens_today || 0;
  if (tokens) {
    stats.push(
      `<span class="app-footer-stat"><span class="label">${escapeHtml(t("footer.tokensToday"))}</span> ${Number(tokens).toLocaleString()}</span>`
    );
  }

  if (cursor.quota_available && cursor.quota_percent_remaining != null) {
    stats.push(
      `<span class="app-footer-stat"><span class="label">${escapeHtml(t("footer.quotaRemaining"))}</span> ${cursor.quota_percent_remaining}%</span>`
    );
  }

  if (local.last_run_status) {
    stats.push(
      `<span class="app-footer-stat"><span class="label">${escapeHtml(t("footer.lastRun"))}</span> ${escapeHtml(runStatusLabel(local.last_run_status))}</span>`
    );
  }

  return stats.join('<span class="app-footer-sep" aria-hidden="true">·</span>');
}

function ensureAppFooter() {
  let footer = document.getElementById("app-footer");
  if (footer) return footer;

  footer = document.createElement("footer");
  footer.id = "app-footer";
  footer.className = "app-footer";
  footer.innerHTML = `
    <div class="container-fluid px-3 px-lg-4">
      <div class="app-footer-row">
        <div class="app-footer-section">
          <span class="app-footer-heading">${escapeHtml(t("footer.stack"))}</span>
          <div id="app-footer-tech" class="app-footer-badges"></div>
        </div>
        <div class="app-footer-section">
          <span class="app-footer-heading">${escapeHtml(t("footer.usage"))}</span>
          <div id="app-footer-stats" class="app-footer-stats">${escapeHtml(t("footer.loading"))}</div>
        </div>
      </div>
    </div>`;
  document.body.appendChild(footer);
  return footer;
}

async function refreshAppFooter() {
  const techEl = document.getElementById("app-footer-tech");
  const statsEl = document.getElementById("app-footer-stats");
  if (!techEl || !statsEl) return;

  try {
    const data = await api("/api/system/footer");
    techEl.innerHTML = renderFooterBadges(data.technologies);
    statsEl.innerHTML = renderFooterStats(data);
  } catch (err) {
    statsEl.innerHTML = `<span class="text-secondary">${escapeHtml(t("footer.unavailable"))}</span>`;
  }
}

function initAppFooter() {
  if (!document.body.classList.contains("app-body")) return;
  ensureAppFooter();
  refreshAppFooter();
  if (typeof startVisibleInterval === "function") {
    startVisibleInterval(refreshAppFooter, 30000);
  }
  if (typeof connectWs === "function") {
    connectWs((msg) => {
      if (msg && msg.type === "agent_event") refreshAppFooter();
    });
  }
}

whenI18nReady(initAppFooter);
