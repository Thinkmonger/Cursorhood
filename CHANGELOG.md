# Changelog

All notable changes to **Robinhood Agentic Console** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

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
