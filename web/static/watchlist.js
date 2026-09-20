function watchlistIdFromPath() {
  const parts = location.pathname.split("/").filter(Boolean);
  const idx = parts.lastIndexOf("watchlists");
  return idx >= 0 && parts[idx + 1] ? decodeURIComponent(parts[idx + 1]) : "";
}

function formatCompact(value) {
  if (value == null || value === "") return t("common.emDash");
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  const abs = Math.abs(n);
  if (abs >= 1e12) return `${(n / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return String(Number(n.toFixed(4)));
}

function formatRange(low, high) {
  if (low == null && high == null) return t("common.emDash");
  return `${formatCompact(low)} – ${formatCompact(high)}`;
}

function holdingCard(row) {
  const f = row.fundamentals || {};
  const symbol = row.symbol || "";
  return `<div class="col-sm-6 col-lg-4 col-xl-3">
    <a class="symbol-fund-card" href="/research?symbol=${encodeURIComponent(symbol)}">
      <div class="fw-semibold mb-2">${escapeHtml(symbol)}</div>
      <div class="d-flex justify-content-between small mb-1">
        <span class="text-secondary">${escapeHtml(t("watchlist.marketCap"))}</span>
        <span>${escapeHtml(formatCompact(f.market_cap))}</span>
      </div>
      <div class="d-flex justify-content-between small mb-1">
        <span class="text-secondary">${escapeHtml(t("watchlist.peRatio"))}</span>
        <span>${escapeHtml(formatCompact(f.pe_ratio))}</span>
      </div>
      <div class="d-flex justify-content-between small mb-1">
        <span class="text-secondary">${escapeHtml(t("watchlist.week52"))}</span>
        <span>${escapeHtml(formatRange(f.low_52_weeks, f.high_52_weeks))}</span>
      </div>
      <div class="d-flex justify-content-between small">
        <span class="text-secondary">${escapeHtml(t("watchlist.volume"))}</span>
        <span>${escapeHtml(formatCompact(f.volume || f.average_volume))}</span>
      </div>
    </a>
  </div>`;
}

async function loadWatchlistOverview() {
  const id = watchlistIdFromPath();
  const status = document.getElementById("watchlist-status");
  const grid = document.getElementById("watchlist-grid");
  if (!id) {
    if (status) status.textContent = t("watchlist.loadFailed");
    return;
  }
  if (status) status.textContent = t("common.loading");
  try {
    const data = await api(`/api/watchlists/${encodeURIComponent(id)}/overview`);
    if (!data.ok) throw new Error(data.error || t("watchlist.loadFailed"));
    const title = document.getElementById("watchlist-title");
    const origin = document.getElementById("watchlist-origin");
    const count = document.getElementById("watchlist-count");
    const name = `${data.icon_emoji ? `${data.icon_emoji} ` : ""}${data.name || id}`;
    if (title) title.textContent = name;
    document.title = `${data.name || "Watchlist"} — Robinhood Agentic Console`;
    if (origin) {
      const yours = data.origin === "user";
      origin.textContent = yours ? t("watchlist.yours") : t("watchlist.robinhood");
      origin.className = `badge ${yours ? "text-bg-success" : "text-bg-secondary"}`;
      origin.classList.remove("d-none");
    }
    const n = data.item_count ?? (data.holdings || []).length;
    if (count) count.textContent = t("research.symbolsCount", { count: n });
    const holdings = data.holdings || [];
    if (!holdings.length) {
      grid.innerHTML = `<div class="col-12"><p class="text-secondary small mb-0">${escapeHtml(t("watchlist.empty"))}</p></div>`;
    } else {
      grid.innerHTML = holdings.map(holdingCard).join("");
    }
    if (status) status.textContent = "";
  } catch (err) {
    if (status) status.textContent = String(err.message || err);
    if (grid) grid.innerHTML = "";
  }
}

document.addEventListener("i18n:ready", () => {
  loadWatchlistOverview();
});
