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
from src.trading.mcp_tools import refresh_remote_tool_cache
from src.trading.watchlists import resolve_symbols

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

    async def list_tools(self) -> dict[str, Any]:
        """Fetch the remote Robinhood MCP tool catalog (tools/list)."""
        if not self.token:
            return {"ok": False, "error": "Not authenticated. Connect Robinhood in setup."}
        import re

        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
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
        raw = response.text
        match = re.search(r"data: ({.*})", raw)
        if not match:
            return {"ok": False, "error": "Could not parse tools/list response"}
        envelope = json.loads(match.group(1))
        if "error" in envelope:
            return {"ok": False, "error": envelope["error"]}
        tools = (envelope.get("result") or {}).get("tools") or []
        names = [str(t.get("name")) for t in tools if isinstance(t, dict) and t.get("name")]
        refresh_remote_tool_cache(names)
        return {"ok": True, "tools": tools, "tool_names": names}

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
        catalog = await self.list_tools()
        out: dict[str, Any] = {
            "ok": True,
            "accounts": accounts.get("result"),
            "portfolio": portfolio.get("result"),
        }
        if catalog.get("ok"):
            out["tool_count"] = len(catalog.get("tool_names") or [])
            out["tool_names"] = catalog.get("tool_names")
        return out

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
        portfolio_args = {"account_number": account_number} if account_number else None
        if portfolio_args is None:
            snapshot["portfolio"] = {"error": "No agentic account_number from get_accounts"}
        else:
            result = await self.call_tool("get_portfolio", portfolio_args)
            if not result.get("ok"):
                snapshot["ok"] = False
                snapshot["error"] = result.get("error", "get_portfolio failed")
                return snapshot
            snapshot["portfolio"] = _compact_mcp_result(extract_mcp_data(result))

        symbols: list[str] = []
        limits: Any = None
        asset_class = "equity"
        try:
            from src.settings.service import SettingsService

            limits = SettingsService(bot_id).read_limits()
            asset_class = str(getattr(limits, "asset_class", None) or "equity").strip().lower()
            if asset_class not in ("equity", "option", "crypto"):
                asset_class = "crypto" if limits.crypto_enabled else "option" if limits.options_enabled else "equity"
            static = (
                list(limits.allowed_crypto_pairs or [])
                if asset_class == "crypto"
                else list(limits.allowed_symbols or [])
            )
            symbols, origin = await resolve_symbols(
                source=limits.symbol_source,
                ref=limits.symbol_source_ref,
                static_symbols=static,
                limit=limits.symbol_source_limit,
                asset_class=asset_class,
            )
            snapshot["symbol_source"] = {"source": origin, "symbols": list(symbols)}
        except Exception:
            pass

        if asset_class != "crypto" and account_number:
            result = await self.call_tool(
                "get_equity_positions",
                {"account_number": account_number},
            )
            if not result.get("ok"):
                snapshot["ok"] = False
                snapshot["error"] = result.get("error", "get_equity_positions failed")
                return snapshot
            snapshot["positions"] = _compact_mcp_result(extract_mcp_data(result))

        try:
            from src.simulation.ledger import get_ledger, is_simulation_mode

            if is_simulation_mode(bot_id):
                ledger = get_ledger(bot_id)
                for position in (ledger.get("positions") or {}).values():
                    if not isinstance(position, dict) or not position.get("symbol"):
                        continue
                    if str(position.get("asset_class") or "equity") != asset_class:
                        continue
                    symbols.append(str(position["symbol"]))
        except Exception:
            pass
        if asset_class == "crypto":
            from src.trading.crypto import normalize_pair

            symbols = sorted({normalize_pair(s) for s in symbols if s})
        else:
            symbols = sorted({str(s).upper() for s in symbols if s})

        broker_orders_payload: Any = None
        if asset_class == "crypto":
            from src.trading.crypto import (
                compact_crypto_positions,
                crypto_quotes_envelope,
                get_crypto_orders,
                get_crypto_positions,
                get_crypto_quotes,
            )

            if symbols:
                crypto_quotes = await get_crypto_quotes(symbols)
                if crypto_quotes is not None:
                    snapshot["quotes"] = crypto_quotes_envelope(crypto_quotes)
                from src.trading.historical_bars import fetch_daily_bars, fetch_hourly_bars

                snapshot["historical_bars_1h"] = await fetch_hourly_bars(symbols, skip_robinhood=True)
                snapshot["historical_bars_1d"] = await fetch_daily_bars(symbols, skip_robinhood=True)
            positions = await get_crypto_positions(account_number)
            rows = compact_crypto_positions(positions)
            if rows:
                snapshot["crypto_positions"] = rows
            if account_number:
                broker_orders_payload = await get_crypto_orders(account_number)
        else:
            if symbols:
                quotes = await self.call_tool("get_equity_quotes", {"symbols": symbols})
                if quotes.get("ok"):
                    snapshot["quotes"] = _compact_mcp_result(extract_mcp_data(quotes))
                from src.trading.historical_bars import fetch_daily_bars, fetch_hourly_bars

                snapshot["historical_bars_1h"] = await fetch_hourly_bars(symbols)
                snapshot["historical_bars_1d"] = await fetch_daily_bars(symbols)
            if account_number:
                orders_result = await self.call_tool(
                    "get_equity_orders",
                    {"account_number": account_number},
                )
                if orders_result.get("ok"):
                    broker_orders_payload = extract_mcp_data(orders_result)
            if asset_class == "option":
                from src.trading.options import compact_option_positions, get_option_positions

                rows = compact_option_positions(await get_option_positions(account_number))
                if rows:
                    snapshot["option_positions"] = rows

        snapshot["trade_history"] = build_trade_history(bot_id, broker_orders_payload)

        watchlists_result = await self.call_tool("get_watchlists")
        if watchlists_result.get("ok"):
            watchlists_data = extract_mcp_data(watchlists_result)
            snapshot["watchlists"] = _compact_mcp_result(watchlists_data)

        if asset_class != "crypto":
            await self._add_research_context(bot_id, snapshot, symbols)
        return snapshot

    async def _add_research_context(
        self,
        bot_id: str,
        snapshot: dict[str, Any],
        symbols: list[str],
    ) -> None:
        """Fundamentals and earnings, only for bots on the `research` context profile."""
        if not symbols:
            return
        try:
            from src.settings.service import SettingsService

            if SettingsService(bot_id).read_bot_app().context_profile != "research":
                return
        except Exception:
            return

        from src.trading.research import compact_fundamentals
        from src.trading.rh_market_data import fetch_earnings_calendar, fetch_fundamentals

        fundamentals = compact_fundamentals(await fetch_fundamentals(symbols))
        if fundamentals:
            snapshot["fundamentals"] = fundamentals
        calendar = await fetch_earnings_calendar(symbols)
        if calendar:
            snapshot["earnings_calendar"] = _compact_mcp_result(calendar, max_len=2000)


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
