from __future__ import annotations

import json
from typing import Any

from src.auth.tokens import TokenStore
from src.db.store import Store
from src.trading.trade_history import (
    agentic_account_number,
    build_trade_history,
    extract_mcp_data,
)
from src.http_client import async_client

MCP_URL = "https://agent.robinhood.com/mcp/trading"

CURSOR_MCP_MODE_KEY = "robinhood_via_cursor"


def use_cursor_mcp_mode() -> bool:
    return Store().get_state(CURSOR_MCP_MODE_KEY) == "true"


def set_cursor_mcp_mode(enabled: bool) -> None:
    Store().set_state(CURSOR_MCP_MODE_KEY, "true" if enabled else "false")


class MCPClient:
    def __init__(self, token: str | None = None) -> None:
        store = TokenStore()
        self.token = token or store.get_access_token()

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.token:
            return {"ok": False, "error": "Not authenticated. Connect Robinhood in setup."}
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        try:
            async with async_client(timeout=60) as client:
                response = await client.post(MCP_URL, json=payload, headers=headers)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if response.status_code == 401:
            return {"ok": False, "error": "Unauthorized — reconnect Robinhood."}
        if response.status_code >= 400:
            return {"ok": False, "error": response.text, "status": response.status_code}
        try:
            data = response.json()
        except json.JSONDecodeError:
            return {"ok": True, "raw": response.text}
        return {"ok": True, "result": data}

    async def test_connection(self) -> dict[str, Any]:
        if not self.token and not TokenStore().get_access_token():
            return {
                "ok": False,
                "step": "token",
                "error": (
                    "No Robinhood bearer token for the bot. "
                    "Settings → Connect Robinhood (Cursor IDE auth does not power automated runs)."
                ),
            }
        accounts = await self.call_tool("get_accounts")
        if not accounts.get("ok"):
            return {"ok": False, "step": "get_accounts", **accounts}
        portfolio = await self.call_tool("get_portfolio")
        return {
            "ok": True,
            "accounts": accounts.get("result"),
            "portfolio": portfolio.get("result"),
        }

    async def fetch_trading_snapshot(self, bot_id: str | None = None) -> dict[str, Any]:
        """Prefetch portfolio context and 1-hour bars for the agent prompt."""
        if not self.token:
            return {"ok": False, "error": "No Robinhood token"}
        from src.db.migrate import DEFAULT_BOT_ID

        bot_id = bot_id or DEFAULT_BOT_ID
        snapshot: dict[str, Any] = {"ok": True}
        accounts_data: Any = None
        account_number: str | None = None
        result = await self.call_tool("get_accounts")
        if not result.get("ok"):
            snapshot["ok"] = False
            snapshot["error"] = result.get("error", "get_accounts failed")
            return snapshot
        accounts_data = extract_mcp_data(result)
        account_number = agentic_account_number(accounts_data)
        for tool, key, args in (
            ("get_portfolio", "portfolio", {"account_number": account_number} if account_number else None),
            ("get_equity_positions", "positions", {"account_number": account_number} if account_number else None),
        ):
            if args is None:
                snapshot[key] = {"error": "No agentic account_number from get_accounts"}
                continue
            result = await self.call_tool(tool, args)
            if not result.get("ok"):
                snapshot["ok"] = False
                snapshot["error"] = result.get("error", f"{tool} failed")
                return snapshot
            parsed = extract_mcp_data(result)
            snapshot[key] = _compact_mcp_result(parsed)
        symbols: list[str] = []
        try:
            from src.settings.service import SettingsService

            symbols = list(SettingsService(bot_id).read_limits().allowed_symbols or [])
        except Exception:
            pass
        try:
            from src.simulation.ledger import get_ledger, is_simulation_mode

            if is_simulation_mode(bot_id):
                ledger = get_ledger(bot_id)
                symbols.extend((ledger.get("positions") or {}).keys())
        except Exception:
            pass
        symbols = sorted({str(s).upper() for s in symbols if s})
        if symbols:
            quotes = await self.call_tool(
                "get_equity_quotes",
                {"symbols": symbols},
            )
            if quotes.get("ok"):
                snapshot["quotes"] = _compact_mcp_result(extract_mcp_data(quotes))
            from src.trading.historical_bars import fetch_daily_bars, fetch_hourly_bars

            snapshot["historical_bars_1h"] = await fetch_hourly_bars(symbols)
            snapshot["historical_bars_1d"] = await fetch_daily_bars(symbols)

        broker_orders_payload: Any = None
        account_number = agentic_account_number(accounts_data)
        if account_number:
            orders_result = await self.call_tool(
                "get_equity_orders",
                {"account_number": account_number},
            )
            if orders_result.get("ok"):
                broker_orders_payload = extract_mcp_data(orders_result)

        snapshot["trade_history"] = build_trade_history(bot_id, broker_orders_payload)
        return snapshot


def _compact_mcp_result(raw: Any, *, max_len: int = 6000) -> Any:
    """Keep prompt context readable and bounded."""
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        text = json.dumps(raw, separators=(",", ":"))
    else:
        text = str(raw)
    if len(text) <= max_len:
        return raw if isinstance(raw, (dict, list)) else text
    return text[: max_len - 20] + "\n… (truncated)"


async def fetch_equity_quotes(symbols: list[str], token: str | None = None) -> Any | None:
    """Fetch live quotes for the given equity symbols."""
    normalized = sorted({str(s).upper() for s in symbols if s})
    if not normalized:
        return None
    client = MCPClient(token=token)
    result = await client.call_tool("get_equity_quotes", {"symbols": normalized})
    if result.get("ok"):
        return extract_mcp_data(result)
    return None


def fetch_equity_quotes_sync(symbols: list[str]) -> Any | None:
    """Blocking quote fetch for sync callers (e.g. status_log paper ledger updates)."""
    import asyncio
    import concurrent.futures

    def _run() -> Any | None:
        return asyncio.run(fetch_equity_quotes(symbols))

    try:
        asyncio.get_running_loop()
        in_loop = True
    except RuntimeError:
        in_loop = False
    if not in_loop:
        return _run()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(_run).result(timeout=30)
