let currentSymbol = "";

function labelize(key) {
  return String(key)
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatCell(value) {
  if (value == null || value === "") return t("common.emDash");
  if (typeof value === "number") {
    return Math.abs(value) >= 1000
      ? Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 })
      : String(Number(value.toFixed(4)));
  }
  return String(value);
}

function renderKeyValues(target, obj) {
  const el = document.getElementById(target);
  if (!el) return;
  const entries = Object.entries(obj || {});
  if (!entries.length) {
    el.innerHTML = `<p class="text-secondary small mb-0">${escapeHtml(t("research.noData"))}</p>`;
    return;
  }
  el.innerHTML = `<dl class="summary-dl mb-0">${entries
    .map(
      ([k, v]) =>
        `<dt>${escapeHtml(labelize(k))}</dt><dd>${escapeHtml(formatCell(v))}</dd>`
    )
    .join("")}</dl>`;
}

function renderRowTable(target, rows) {
  const el = document.getElementById(target);
  if (!el) return;
  if (!rows || !rows.length) {
    el.innerHTML = `<p class="text-secondary small mb-0">${escapeHtml(t("research.noData"))}</p>`;
    return;
  }
  const columns = [...new Set(rows.flatMap((r) => Object.keys(r)))];
  el.innerHTML = `<div class="table-responsive"><table class="table table-dark table-sm mb-0">
    <thead><tr>${columns.map((c) => `<th>${escapeHtml(labelize(c))}</th>`).join("")}</tr></thead>
    <tbody>${rows
      .map(
        (r) =>
          `<tr>${columns.map((c) => `<td>${escapeHtml(formatCell(r[c]))}</td>`).join("")}</tr>`
      )
      .join("")}</tbody>
  </table></div>`;
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
      return `<div class="py-2 border-bottom border-secondary-subtle">
        <div class="small fw-semibold">${heading}</div>
        <div class="text-secondary small">${escapeHtml([n.source, n.published_at].filter(Boolean).join(" · "))}</div>
        ${n.summary ? `<div class="small mt-1">${escapeHtml(n.summary)}</div>` : ""}
      </div>`;
    })
    .join("");
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
  document.getElementById("report-card")?.classList.remove("d-none");
  document.getElementById("report-symbol").textContent = symbol;
  const status = document.getElementById("report-status");
  if (status) status.textContent = t("common.loading");

  try {
    const report = await api(`/api/research/${encodeURIComponent(symbol)}`);
    if (!report.ok) throw new Error(report.error || t("research.reportFailed"));
    renderKeyValues("report-fundamentals", report.fundamentals);
    renderKeyValues("report-indicators", report.indicators);
    renderKeyValues("report-price-book", report.price_book);
    renderKeyValues("report-ratings", report.analyst_ratings);
    renderRowTable("report-earnings", report.earnings);
    renderRowTable("report-financials", report.financials);
    renderNews("report-news", report.news);
    if (status) status.textContent = "";
  } catch (err) {
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
    const [mine, popular] = await Promise.all([
      api("/api/watchlists").catch(() => ({ watchlists: [] })),
      api("/api/watchlists/popular").catch(() => ({ watchlists: [] })),
    ]);
    const sections = [
      [t("research.myWatchlists"), mine.watchlists || []],
      [t("research.popularWatchlists"), popular.watchlists || []],
    ];
    el.innerHTML = sections
      .map(([heading, rows]) => {
        const body = rows.length
          ? rows
              .map(
                (w) => `<div class="py-1 border-bottom border-secondary-subtle">
                  <div class="small fw-semibold">${escapeHtml(w.name || w.id || "")}</div>
                  <div class="small">${(w.symbols || [])
                    .map(
                      (s) =>
                        `<button type="button" class="btn btn-link btn-sm p-0 me-2" onclick="loadReport('${escapeHtml(s)}')">${escapeHtml(s)}</button>`
                    )
                    .join("")}</div>
                </div>`
              )
              .join("")
          : `<p class="text-secondary small mb-0">${escapeHtml(t("research.noData"))}</p>`;
        return `<div class="mb-3"><div class="stat-label mb-1">${escapeHtml(heading)}</div>${body}</div>`;
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
});
