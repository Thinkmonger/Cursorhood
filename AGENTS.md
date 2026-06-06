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

Single Python app (FastAPI + static dashboard). No Node/npm, Docker, or test/lint suite in-repo.

### Run the dashboard

```bash
source .venv/bin/activate
python -m runner.main          # http://127.0.0.1:8765/
# or: robinhood-bot
```

Manual one-off agent cycle: `python -m runner.main run-once [--bot <id>]` (requires setup complete + `CURSOR_API_KEY` + Robinhood OAuth).

### Verify without external credentials

Dashboard/API smoke test works with no API keys:

- `curl -s http://127.0.0.1:8765/api/bots`
- `curl -s -X POST http://127.0.0.1:8765/api/bots -H 'Content-Type: application/json' -d '{"name":"Test Bot"}'`

Python sanity check: `python -m compileall -q runner src mcp_servers`

### Full agent cycles

Need `CURSOR_API_KEY` in `.env` (copy from `.env.example`) and Robinhood OAuth via Configuration tab (`/?tab=config`). Simulation mode is on by default (`config/app.yaml`); cycles still need Robinhood auth for market snapshots. Local MCP servers (`mcp_servers.trading`, `mcp_servers.status`) are spawned by the Cursor SDK per cycle — do not start them manually.

### Notes

- SQLite DB auto-created at `data/bot.db` on first run.
- Ubuntu/Debian VMs need `python3.12-venv` (or matching version) before `python3 -m venv .venv` works.
- Default ports: dashboard `8765`, OAuth callback `9876`.
- Use a tmux session for long-running `python -m runner.main` in Cloud Agent VMs.
