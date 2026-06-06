# Playbook
Autonomous trading agent. Per cycle: apply strategy + limits to the minimal Context block, trade or skip, `log_event`. No config/schedule changes. Use MCP tools only when Context is insufficient.

## Cycle
1. Strategy and limits are in the prompt.
2. Context has portfolio, holdings, quotes, 1h technicals (open/close, SMA-20), 1d technicals (open/close), last action, per-symbol counts, open orders only.
3. **robinhood-trading** for fresh data/orders if needed; **review_equity_order** before **place_equity_order**.
4. **robinhood-status** `log_event`: `{"action":"none|buy|sell","symbols":[],"reason":"...","portfolio_value":0}`

## Safety
Crypto 24/7; `market_hours_only` = US equities only. Uncertain → no action + reason.

## MCP
- **robinhood-trading** — market data and orders
- **robinhood-status** — `log_event` only

## Cursor Cloud specific instructions

Single-process Python monolith (FastAPI + Uvicorn). No npm, Docker, or separate test/lint suite in the repo.

### First-time setup (once per VM image)

Ubuntu/Debian VMs need `python3.12-venv` before `python3 -m venv .venv` works:

```bash
sudo apt-get install -y python3.12-venv
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env   # if .env missing; add CURSOR_API_KEY for agent cycles
```

### Start the dashboard

```bash
source .venv/bin/activate
python -m runner.main
```

Also available as `robinhood-bot`. Default URL: http://127.0.0.1:8765/

### Verify without external credentials

The web UI and REST API work without `CURSOR_API_KEY` or Robinhood OAuth. Quick checks:

```bash
curl -s http://127.0.0.1:8765/api/bots
curl -s http://127.0.0.1:8765/api/settings
```

Agent cycles (`run-once`, scheduler, **Run Cycle** button) require `CURSOR_API_KEY` and Robinhood connection via Configuration tab.

### Syntax sanity check

No pytest/ruff configured. Use `python -m compileall -q runner src mcp_servers` if needed.

### Runtime data

SQLite DB at `data/bot.db` (gitignored). MCP child processes (`mcp_servers/status`, `mcp_servers/trading`) are spawned automatically during agent cycles — do not start them manually.
