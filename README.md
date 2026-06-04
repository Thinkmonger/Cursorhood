# Robinhood Agentic Console

Local web console for [Robinhood Agentic Trading](https://robinhood.com/us/en/agentic-trading), powered by the Cursor agent SDK and the official Robinhood MCP server at `https://agent.robinhood.com/mcp/trading`.

Run multiple strategy bots against your **Agentic account**, with simulation mode, risk limits, scheduler controls, live activity, and optional [Massive](https://massive.com/docs/rest/quickstart) market data for technical analysis.

See [CHANGELOG.md](CHANGELOG.md) for release history.

## Features

- **Multi-bot dashboard** — create bots, start/pause/stop schedulers, and view portfolio sparklines
- **Simulation mode** — paper trades with optional live-portfolio context; reset and re-seed from configured cash
- **Per-bot configuration** — strategy markdown, risk limits, cycle interval, Cursor model, max runs
- **Run history & activity** — summaries, tool-call timeline, and WebSocket live updates
- **Statistics** — aggregate and per-bot metrics from run history
- **Connections** — Cursor API key, Massive API key, Robinhood OAuth, and MCP test (shared across bots)
- **Massive market data** — 1-hour bars and SMA-20 prefetched for each cycle (rate-limited, Yahoo fallback)
- **Power management (Windows)** — prevents system sleep while the bot runs; monitors and screensaver may still turn off
- **Risk hooks** — `.cursor/hooks/check_trade.py` enforces each bot's limits before orders execute
- **Internationalization** — UI copy in `locales/en.json` (English default)

## Requirements

- Python 3.11+
- [Cursor API key](https://cursor.com/settings)
- Robinhood **Agentic Trading** access
- Funded **Agentic account** (separate from your main portfolio)
- Optional: [Massive API key](https://massive.com/docs/rest/quickstart) for historical 1-hour bars (free tier: 5 calls/minute)

## Quick start

```powershell
git clone <your-repo-url>
cd Robinhood
python -m venv .venv
.venv\Scripts\activate
pip install -e .

copy .env.example .env
# Set CURSOR_API_KEY in .env (optional: MASSIVE_API_KEY)

python -m runner.main
```

If `pip install` fails with SSL errors on Windows:

```powershell
pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org -e .
```

Open **http://127.0.0.1:8765/** in your browser.

| URL | Purpose |
|-----|---------|
| `/` | Dashboard — bots overview |
| `/?tab=config` | Configuration — Cursor, Massive, and Robinhood connections |
| `/bots/{id}` | Bot console — portfolio, scheduler, settings |
| `/bots/{id}/agents` | Run history and live activity |
| `/statistics` | Performance statistics |

Legacy paths (`/setup`, `/settings`, `/agents`) redirect to the routes above.

## Manual cycle

```powershell
python -m runner.main run-once
```

Optional bot id:

```powershell
python -m runner.main run-once --bot my-bot
```

## Power management (Windows)

While the dashboard or a manual cycle is running, the bot calls Windows `SetThreadExecutionState` with **`ES_SYSTEM_REQUIRED`** so the PC does not sleep and scheduled cycles can fire on time.

| Behavior | While bot is running |
|----------|----------------------|
| System sleep / hibernate | **Prevented** |
| Monitor power-off | Allowed |
| Screensaver | Allowed |

Sleep prevention is enabled automatically when you start `python -m runner.main` (server or `run-once`) and restored when the process exits. No environment variable is required. On non-Windows platforms this is currently a no-op.

If you stop the bot process, normal Windows power settings apply again.

## Connections

Configure shared credentials on the dashboard **Configuration** tab (`/?tab=config`):

| Connection | Purpose |
|------------|---------|
| **Cursor API key** | Powers agent cycles via the Cursor SDK (billed as **API usage**, not IDE Auto/Composer quota) |
| **Massive API key** | 1-hour historical bars for technical rules (optional) |
| **Robinhood** | OAuth to your Agentic account for live trades |

### Robinhood authentication

1. **Recommended:** Dashboard → **Configuration** → **Connect Robinhood** (desktop OAuth).
2. **Fallback:** Connect MCP in Cursor (Settings → Tools & MCPs → `https://agent.robinhood.com/mcp/trading`), then enable **Use Cursor MCP** in the console.

Trades execute only in your **Agentic account**.

### Cursor API billing

Agent cycles use the **Cursor SDK with your API key**. Most models bill against **API usage** on your Cursor dashboard (SDK tag).

- Default model is **`composer-2.5`** (change per bot under **Configuration → Cursor model**).
- **`composer-*`** and **`auto`** are allowed but **warn** — they consume **Auto + Composer** subscription quota instead of API usage.
- The SDK runs **locally** with explicit `local` runtime, inline MCP config, and **no IDE project settings** (`setting_sources` not loaded).
- Each cycle uses `Agent.create` + `agent.send` with streaming, `run.wait()`, and proper SDK disposal on shutdown.
- Startup failures (`CursorAgentError`) are logged separately from mid-run failures (`result.status == "error"`).
- `agent_id` and `cursor_run_id` are logged and emitted to the dashboard for debugging in the Cursor console.

List models available to your key: `GET /api/settings/cursor/models` or use the model field suggestions in the bot dashboard.

### Massive market data

When `MASSIVE_API_KEY` is set, each cycle prefetches **1-hour OHLCV bars** (and SMA-20 when available) into `historical_bars_1h` for the agent prompt. Robinhood MCP does not expose historical bars, so Massive (or Yahoo as fallback) fills that gap.

**Fetch order per symbol:**

1. **Massive** — uses available quota (5 calls/minute on the free tier).
2. **Yahoo Finance** — fallback when Massive quota is exhausted or a non-auth error occurs.
3. **Wait + Massive retry** — if Yahoo also fails, the bot waits for quota and retries Massive (up to ~65s by default).

With `MASSIVE_USE_SMA_ENDPOINT=true` (default), each Massive call returns hourly bars **and** SMA-20 in one request via the [SMA indicator API](https://massive.com/docs/rest/stocks/technical-indicators/simple-moving-average) with `expand_underlying`.

You can set the key in `.env` or from the console (**Save Massive Key** / **Test Massive**).

## Environment variables

Copy `.env.example` to `.env`. Keys can also be saved from the Configuration tab (written to `.env`).

| Variable | Default | Description |
|----------|---------|-------------|
| `CURSOR_API_KEY` | — | Cursor agent API key (required) |
| `MASSIVE_API_KEY` | — | Massive REST API key (optional market data) |
| `MASSIVE_API_BASE_URL` | `https://api.massive.com` | Massive API base URL |
| `MASSIVE_RATE_LIMIT_PER_MINUTE` | `5` | Client-side rate limit |
| `MASSIVE_RETRY_WAIT_SECONDS` | `65` | Max wait when retrying Massive after Yahoo failure |
| `MASSIVE_USE_SMA_ENDPOINT` | `true` | Bars + SMA-20 in one call per symbol |
| `DASHBOARD_HOST` | `127.0.0.1` | Web console bind address |
| `DASHBOARD_PORT` | `8765` | Web console port |
| `OAUTH_CALLBACK_PORT` | `9876` | Robinhood OAuth callback port |
| `SSL_VERIFY` | `true` | Set to `false` on Windows if Robinhood MCP HTTPS fails with certificate errors |

Example `.env` snippet:

```env
CURSOR_API_KEY=your_cursor_key
MASSIVE_API_KEY=your_massive_key
SSL_VERIFY=false
```

On some Windows setups, Python cannot verify Robinhood's TLS certificate chain; `SSL_VERIFY=false` is required for MCP calls to succeed. The app logs that warning **once per process**, not on every request.

## Configuration files

Each bot has its own strategy, limits, and scheduler settings. The **default** bot (`default`) uses the top-level `config/` folder; additional bots use `config/bots/{bot_id}/`.

| Path | Purpose |
|------|---------|
| `config/strategy.md` | Default bot strategy |
| `config/limits.yaml` | Default bot risk limits |
| `config/app.yaml` | Default bot scheduler and simulation settings |
| `config/global.yaml` | Shared dashboard host/port (optional; created on save) |
| `config/bots/{id}/strategy.md` | Per-bot strategy (e.g. `trueagent`) |
| `config/bots/{id}/limits.yaml` | Per-bot risk limits |
| `config/bots/{id}/app.yaml` | Per-bot scheduler and simulation settings |
| `.env` | API keys and environment overrides (see table above) |
| `AGENTS.md` | Agent playbook (reference; cycle prompts use minimal Context JSON) |

New bots clone strategy and limits from the default bot. Edit per-bot settings from each bot's **Configuration** tab in the console.

## Internationalization

UI copy lives in `locales/en.json`. The web app loads strings via `web/static/i18n.js` (`data-i18n` attributes in HTML, `t()` in JavaScript). Add `locales/{locale}.json` and extend `initI18n()` to support additional languages.

## Project layout

```
runner/              Entry point and scheduler
src/api/             FastAPI routes and WebSocket
src/trading/         Historical bars (Massive/Yahoo), trade history, market hours
src/system/          Sleep prevention, server restart helpers
src/i18n/            Server-side locale helpers
src/simulation/      Paper trading ledger
web/                 Dashboard HTML and static assets
locales/             UI copy (English default)
config/              Default YAML strategy and limits
.cursor/hooks/       Trade risk enforcement
CHANGELOG.md         Release history
AGENTS.md            Agent playbook for automated cycles
```

## Disclosures

You are responsible for all trades. Agentic trading involves significant risk. This software is not affiliated with Robinhood. Brokerage services through Robinhood Financial LLC (member SIPC).
