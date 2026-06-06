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

Single-process Python app (FastAPI + Uvicorn). No Docker, Node build, or separate DB server.

### Run the dashboard

```bash
source .venv/bin/activate
python -m runner.main
```

Default URL: `http://127.0.0.1:8765/` (`DASHBOARD_PORT` / `config/global.yaml`). One-shot cycle: `python -m runner.main run-once [--bot <id>]`.

### VM prerequisites

Ubuntu images may need `python3.12-venv` before the first `python3 -m venv .venv` (`sudo apt-get install -y python3.12-venv`). The update script assumes `.venv` already exists.

### Lint / tests

No project lint config or test suite. Sanity check: `python -m compileall -q runner src mcp_servers`.

### Full trading cycles

Require `CURSOR_API_KEY` in `.env` (or Configuration tab) plus Robinhood OAuth. MCP servers (`mcp_servers.trading`, `mcp_servers.status`) are spawned by the Cursor SDK during cycles — do not start them manually. Dashboard-only development works without those secrets.
