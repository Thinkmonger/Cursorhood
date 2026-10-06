/** Shared app footer — tech stack plus app name, version, and links. */

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

function renderFooterApp(app) {
  const info = app || {};
  const parts = [
    `<span class="app-footer-stat">${escapeHtml(info.name || t("app.nameShort"))}</span>`,
  ];
  if (info.version) {
    parts.push(`<span class="app-footer-stat">v${escapeHtml(info.version)}</span>`);
  }
  if (info.github_url) {
    parts.push(
      `<a href="${escapeHtml(info.github_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(t("footer.github"))}</a>`
    );
  }
  if (info.venmo_url) {
    parts.push(
      `<a href="${escapeHtml(info.venmo_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(t("footer.venmo"))}</a>`
    );
  }
  return parts.join('<span class="app-footer-sep" aria-hidden="true">·</span>');
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
          <span class="app-footer-heading">${escapeHtml(t("footer.app"))}</span>
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
    statsEl.innerHTML = renderFooterApp(data.app);
  } catch (err) {
    statsEl.innerHTML = `<span class="text-secondary">${escapeHtml(t("footer.unavailable"))}</span>`;
  }
}

function initAppFooter() {
  if (!document.body.classList.contains("app-body")) return;
  ensureAppFooter();
  refreshAppFooter();
}

whenI18nReady(initAppFooter);
