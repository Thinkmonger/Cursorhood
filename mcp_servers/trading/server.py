from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.setup.mcp_client import MCPClient
from src.trading.mcp_tools import compact_watchlists_for_prompt, tool_allowed_for_categories
from src.trading.trade_history import extract_mcp_data


def _token() -> str | None:
    return os.environ.get("ROBINHOOD_MCP_TOKEN") or None


def _enabled_categories() -> set[str] | None:
    """Categories this bot may use, or None to expose everything."""
    raw = (os.environ.get("ROBINHOOD_ENABLED_CATEGORIES") or "").strip()
    if not raw:
        return None
    return {part.strip() for part in raw.split(",") if part.strip()}


def _tool_from_remote(raw: dict[str, Any]) -> types.Tool:
    return types.Tool(
        name=raw["name"],
        description=raw.get("description") or "",
        inputSchema=raw.get("inputSchema") or {"type": "object", "properties": {}},
        outputSchema=raw.get("outputSchema"),
    )


async def _remote_tools(client: MCPClient) -> list[types.Tool]:
    catalog = await client.list_tools()
    if not catalog.get("ok"):
        return []
    tools = catalog.get("tools") or []
    categories = _enabled_categories()
    out: list[types.Tool] = []
    for tool in tools:
        if not isinstance(tool, dict) or not tool.get("name"):
            continue
        if categories is not None and not tool_allowed_for_categories(str(tool["name"]), categories):
            continue
        out.append(_tool_from_remote(tool))
    return out


def _normalize_tool_result(parsed: Any) -> dict[str, Any] | types.CallToolResult:
    if parsed is None:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="Empty response from Robinhood MCP")],
            isError=True,
        )
    if isinstance(parsed, dict) and "error" in parsed:
        err = parsed["error"]
        if isinstance(err, dict):
            text = err.get("message") or json.dumps(err)
        else:
            text = str(err)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)],
            isError=True,
        )
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, str):
        try:
            return json.loads(parsed)
        except json.JSONDecodeError:
            return {"data": parsed}
    return {"data": parsed}


_TERMINAL_ORDER_STATES = frozenset(
    {"filled", "cancelled", "canceled", "rejected", "failed", "voided", "expired"}
)
_MAX_TOOL_RESULT_LEN = 6000
_MAX_RECENT_TERMINAL_ORDERS = 5


def _filter_orders_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Keep open orders plus the most recent few terminal ones to bound history size."""
    data = result.get("data")
    if not isinstance(data, dict):
        return result
    orders = data.get("orders")
    if not isinstance(orders, list):
        return result

    def _ts(order: dict[str, Any]) -> str:
        return str(order.get("last_transaction_at") or order.get("created_at") or "")

    open_orders = [
        o for o in orders
        if isinstance(o, dict) and str(o.get("state") or "").lower() not in _TERMINAL_ORDER_STATES
    ]
    terminal = sorted(
        (
            o for o in orders
            if isinstance(o, dict) and str(o.get("state") or "").lower() in _TERMINAL_ORDER_STATES
        ),
        key=_ts,
    )
    kept = open_orders + terminal[-_MAX_RECENT_TERMINAL_ORDERS:]
    if len(kept) == len(orders):
        return result
    return {**result, "data": {**data, "orders": kept}}


def _compact_tool_result(name: str, result: dict[str, Any]) -> dict[str, Any]:
    """Bound the size of runtime MCP tool results sent back to the agent (token control)."""
    if name in ("get_equity_orders", "get_option_orders", "get_crypto_orders"):
        result = _filter_orders_payload(result)
    elif name == "get_watchlists":
        compact = compact_watchlists_for_prompt(result)
        if compact is not None:
            result = {"data": {"watchlists": compact}}
    try:
        text = json.dumps(result, separators=(",", ":"))
    except (TypeError, ValueError):
        return result
    if len(text) <= _MAX_TOOL_RESULT_LEN:
        return result
    return {"truncated": True, "data": text[: _MAX_TOOL_RESULT_LEN - 20] + "… (truncated)"}


server = Server("robinhood-trading")
_tool_cache: list[types.Tool] | None = None


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    global _tool_cache
    token = _token()
    if not token:
        return []
    client = MCPClient(token=token)
    _tool_cache = await _remote_tools(client)
    return _tool_cache


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    token = _token()
    if not token:
        raise ValueError("ROBINHOOD_MCP_TOKEN not set for robinhood-trading proxy")
    args = arguments or {}
    client = MCPClient(token=token)
    result = await client.call_tool(name, args)
    parsed = extract_mcp_data(result)
    normalized = _normalize_tool_result(parsed)
    if isinstance(normalized, types.CallToolResult):
        return normalized
    return _compact_tool_result(name, normalized)


async def _main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio

    asyncio.run(_main())
