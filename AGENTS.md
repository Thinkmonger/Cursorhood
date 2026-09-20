# Playbook
Autonomous trading agent. Per cycle: apply strategy + limits to the minimal Context block, trade or skip, `log_event`. No config/schedule changes. Use MCP tools only when Context is insufficient.

## Cycle
1. Strategy and limits are in the prompt. Limits may include `options` and `crypto` blocks when those asset classes are enabled.
2. Context has portfolio, holdings, quotes, watchlists or resolved `symbol_source`, 1h technicals (open/close, SMA-20, any strategy-requested indicators), 1d technicals (open/close), last action, per-symbol counts, open orders, and `option_positions`/`crypto_positions` when enabled.
3. **robinhood-trading** for fresh data/orders/watchlists if needed. Review before placing: **review_equity_order**, **review_option_order**, **preview_crypto_order**.
4. **robinhood-status** `log_event`: `{"action":"none|buy|sell","symbols":[],"reason":"...","portfolio_value":0}`

## Safety
Crypto 24/7; `market_hours_only` gates US equities and options only. Options are long-only unless `allow_option_selling`. Uncertain → no action + reason.

## MCP
Only the categories this bot enabled are exposed.
- **robinhood-trading**
  - Account: accounts, portfolio, realized P&L, trade history, search
  - Watchlists: read, create, update, follow, add/remove symbols and options
  - Market data: historicals, fundamentals, financials, price book, technical indicators, earnings, indexes, news, analyst ratings, SEC filings
  - Equities: positions, tax lots, quotes, orders, tradability, review/place/cancel, advanced orders
  - Options: chains, instruments, quotes, positions, orders, review/place/cancel (single-leg). Never exercise — close the contract.
  - Crypto: pairs, quotes, positions, orders, preview/place/cancel
  - Scanners: saved scans, filter specs, create/run/update
  - Alerts: list, create/update/delete, log, mark read
- **robinhood-status** — `log_event` only

## Cursor Cloud specific instructions

This is a Python 3.11+ FastAPI web console (the "Robinhood Agentic Console"). There is no test suite or linter configured in the repo; "running the app" means starting the dashboard.

- **Virtualenv**: dependencies install into `.venv` (gitignored). Activate with `. .venv/bin/activate` before running commands. The update script keeps `.venv` current.
- **Run the dashboard**: `python -m runner.main` serves the web console at `http://127.0.0.1:8765/` (host/port from `config/app.yaml`/`.env`). Routes are documented in `README.md`.
- **Manual single cycle**: `python -m runner.main run-once [--bot <id>]` — requires setup complete and a valid `CURSOR_API_KEY`; it will exit early otherwise.
- **`.env`**: copy from `.env.example` if missing. The app runs fine without `CURSOR_API_KEY` (it just reports "Setup incomplete"); the key is only needed to actually execute agent trading cycles via the Cursor SDK.
- **Core functionality without external keys**: creating bots, seeding/resetting the simulation ledger (default `$500` cash), portfolio/statistics views, and the dashboard UI all work with no Robinhood/Cursor/Massive credentials. Use these for smoke testing.
- **State/DB**: a SQLite DB is created at `data/bot.db` on first run (the `data/` dir is gitignored). Delete it to reset all bots/runs.
- **Harmless startup noise**: `python -m runner.main` may auto-open a browser (`open_browser_on_start`), producing `dbus`/`GCM`/`gpu` Chrome errors in the log. These are unrelated to the server, which is healthy once you see `Uvicorn running on http://127.0.0.1:8765`.
- **`.cursor/mcp.json`** contains Windows paths and a sample token; it is only used when running real Robinhood MCP trading and is not needed to start the dashboard.
