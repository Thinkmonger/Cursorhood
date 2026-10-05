# Changelog

All notable changes to **Robinhood Agentic Console** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Class-specific bot templates** — equities, options, and crypto each have their own `strategy.md`, limits, and cycle interval under `config/templates/`. The create-bot dialog picks a class instead of cloning the default bot.
- **SQLite `bot_settings`** — strategy, limits, and per-bot app config persist in `data/bot.db`. Existing YAML is imported once; later edits write SQL only.
- **OS keyring for API keys** — Cursor and Massive keys saved from the UI go to the `robinhood-agentic-bot` keyring (env / `.env` remain fallbacks). Secrets stay out of SQLite.
- **Lightweight Charts on the bot console** — daily OHLC for a selected holding, plus an Open on TradingView link. Research already used the same library.
- **Full Robinhood MCP catalog** — categorized registry in `src/trading/mcp_tools.py` covering all 79 live tools across account, watchlist, market-data, equity, option, crypto, scanner, and alert categories, with `asset_class_for_tool` and remote-merge for tools Robinhood ships later. Reconciled against a live `tools/list`, which turned up 22 tools absent from the support article: advanced orders, option exercise, alerts, news, analyst ratings, politician trades, SEC filings, index historicals, margin-upgrade info, and crypto onboarding.
- **Per-bot tool filtering** — `ROBINHOOD_ENABLED_CATEGORIES` trims the proxy's `tools/list` to the bot's enabled asset classes; an equities-only bot sees 53 of 79 tools, cutting roughly 2,900 schema tokens (33%) from every cycle.
- **Capabilities API and chips** — `GET /api/trading/capabilities` returns the live catalog grouped by category (5-minute cache); the dashboard shows which asset classes the connected account can trade.
- **Robinhood-first market data** (`src/trading/rh_market_data.py`) — `get_equity_historicals` and native `get_equity_technical_indicators` lead a Robinhood → Yahoo → Massive provider chain; Massive is last-resort when a key is set. Local SMA-20 remains the fallback and strategies pull only the indicators they mention.
- **Watchlist-driven trading** (`src/trading/watchlists.py`) — all 12 watchlist tools plus `symbol_source` / `symbol_source_ref` / `symbol_source_limit`, resolved fresh each cycle and recorded in the run event.
- **Single-leg options** (`src/trading/options.py`) — chains, instruments, quotes, positions, and orders with bounded near-the-money chain compaction, `option_positions` in Context, and options risk limits (contracts, notional, DTE window, type allowlist, selling blocked by default).
- **Crypto** (`src/trading/crypto.py`) — pairs, quotes, positions, orders, and preview with a pair allowlist and notional cap; crypto-enabled bots are exempt from `market_hours_only` and keep cycling 24/7.
- **Research and scanners** — `src/trading/research.py` and `src/trading/scanners.py` behind `/api/research/*`, `/api/scans/*`, and `/api/watchlists/*`, plus a new `/research` console page with symbol search, fundamentals, financials, earnings, indicators, price book, and a scanner builder.
- **Paper broker** (`src/simulation/`) — real order lifecycle (market, limit, stop, stop-limit) with resting orders re-evaluated each cycle, multi-asset FIFO positions with realized P&L, a configurable slippage/commission fill model, T+N settlement, and one-time migration of the existing ledger JSON.
- **Bot dashboard panels** — options positions, crypto positions, paper trading (open orders with cancel, recent fills, realized P&L), and watchlists with the active symbol source highlighted.
- **Context profile** — per-bot `minimal | standard | research` bounds how much market context rides along in each prompt; default stays `minimal`.
- **Cursor model dropdown** — bot configuration lists every model from the live Cursor catalog, grouped into IDE / Cursor models vs third-party API usage.

### Fixed

- **Double paper fills** — the hook and `apply_status_log` could each record the same trade. The paper broker now owns every fill and de-duplicates on run plus event id.
- **Options and crypto booked as equities** — in simulation mode `place_option_order` and `place_crypto_order` were recorded as equity fills at an equity quote price; orders now route by asset class with the correct contract multiplier.
- **Ignored paper risk limits** — `max_open_positions` and `max_daily_loss_usd` are now enforced against the paper portfolio, not just live orders.
- **Paper cancels did nothing** — `cancel_*_order` now cancels resting paper orders instead of denying with no state change.
- **Contradictory simulation prompt** — the prompt no longer tells the agent both "no place/cancel" and "review before place"; it explains that orders are intercepted and filled on paper.
- **Unfunded paper orders silently shrank** — a buy larger than paper buying power filled at whatever the cash allowed (draining the account to zero) instead of being rejected; it now rejects and names the shortfall, like a cash account.
- **Fractional option contracts** — paper option orders sized by notional could book 1.25 contracts. Quantities are quantized per asset class, and an order that rounds to zero is rejected.
- **Cost-less positions on ledger upgrade** — a v1 position missing `avg_cost` migrated to a zero-cost lot that later read as pure profit; such positions are now dropped with a warning, and common legacy key spellings are recognized.
- **Option exercise** — `exercise_option` reaches the risk hook as an option write, and is denied in both live and simulation mode since exercising converts a contract into 100 shares well past the configured notional caps.

### Changed

- **README / GitHub about** — explain Cursor multi-agent model choice (Composer for fast cycles, API models for heavier options/research).
- **Market-data chain** — bars resolve Robinhood → Yahoo → Massive (last resort, including a keyring-saved Massive key). Crypto cycle prefetch no longer skips Massive.
- **Bot Manage layout** — header actions (Run Cycle / Start / Pause), Overview holds the portfolio picture, Configuration is a two-column settings grid with one shared Save.
- **Differentiated default playbooks** — equity (session hours, Daily movers, SMA/RSI dip), option (long-only, DTE 21–45, Index options), and crypto (24/7, wider stops) no longer share one generic strategy.
- **Exclusive asset class** — each bot trades equities, options, or crypto (not a mix). Watchlist items resolve by `list_id`, crypto bots read `currency_pair` entries (normalized to `BTC-USD`), an empty crypto pair list no longer blocks every pair, and mismatched order tools are denied before a paper fill.
- **Cursor SDK integration** — dedicated `runner/cursor_agent.py` following Cursor production guidance: explicit local runtime and `api_key`, no ambient IDE settings, agent/run ID logging, startup vs mid-run error distinction, SDK bridge cleanup on shutdown.
- **Prompt token optimization** — cycle prompts send minimal decision Context only (portfolio, holdings, quotes, 1h signals, last action, open orders); no order history dumps or raw bar arrays.
- **Sleep prevention (Windows)** — keeps the system awake for scheduled bot cycles but no longer blocks monitor sleep or the screensaver (`ES_DISPLAY_REQUIRED` removed).
- **Documentation** — README Cursor API billing section, power-management notes, corrected `run-once --bot` flag, project layout, and cross-references.

### Fixed

- **Cursor SDK SSL on Windows with AVG** — drop `SSLKEYLOGFILE` only when Python cannot open the path (e.g. AV filter devices); normal file paths are left untouched.

## [0.2.0] - 2026-05-28

### Added

- **Massive market data** — 1-hour OHLCV bars and SMA-20 for agent cycles via [Massive](https://massive.com/docs/rest/quickstart) (formerly Finnhub/Polygon).
- **Massive Configuration UI** — API key field, save/validate, and test connection on the dashboard **Configuration** tab (`/?tab=config`).
- **Rate-limited Massive client** — rolling 5 calls/minute limiter with wait-and-retry when quota is exhausted.
- **Three-phase bar fetch** — Massive first, Yahoo Finance fallback, then wait and retry Massive if Yahoo fails.
- **SMA endpoint optimization** — optional single-call fetch (`MASSIVE_USE_SMA_ENDPOINT=true`) returns hourly bars and SMA-20 together.
- **Official changelog** — this file.

### Changed

- **Branding** — UI and docs use **Robinhood Agentic Console**; copy centralized in `locales/en.json`.
- **Configuration connections** — status chips for Cursor, Massive, and Robinhood; removed masked API key badge from the UI.
- **Footer stack** — Massive added to the tech stack with a link to REST docs.
- **SSL warnings** — `SSL_VERIFY=false` logs once per process instead of on every HTTP client creation.
- **README** — expanded environment variables, market data behavior, and project layout.

### Fixed

- **Windows SSL** — Robinhood MCP requires `SSL_VERIFY=false` on some Windows/Python setups where the local CA store cannot verify `agent.robinhood.com` (documented; not a server-side issue).

## [0.1.0] - 2026-05-27

### Added

- **Multi-bot dashboard** — create, start, pause, stop, and remove bots; portfolio sparklines per bot.
- **Simulation mode** — paper trading ledger with optional live-portfolio context and reset.
- **Per-bot configuration** — strategy markdown, risk limits, scheduler interval, Cursor model, max runs.
- **Run history & activity** — summaries, tool-call timeline, WebSocket live updates.
- **Statistics page** — aggregate and per-bot metrics from run history.
- **Connections** — Cursor API key, Robinhood OAuth, MCP test, Cursor MCP fallback mode.
- **Risk hooks** — `.cursor/hooks/check_trade.py` enforces per-bot limits before orders execute.
- **Internationalization scaffold** — `locales/en.json`, `web/static/i18n.js`, server-side i18n helpers.
- **Trade history prefetch** — broker orders and bot activity included in agent snapshots.
- **Investor profile gate** — banner and acknowledge flow when Robinhood blocks orders.

<!-- Release links: update when tagging in git -->
<!-- [Unreleased]: compare/v0.2.0...HEAD -->
<!-- [0.2.0]: compare/v0.1.0...v0.2.0 -->
<!-- [0.1.0]: releases/tag/v0.1.0 -->
