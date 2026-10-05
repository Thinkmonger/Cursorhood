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
    # Robinhood declares outputSchema but returns text-only content. Passing
    # outputSchema through makes Cursor treat error strings as schema failures.
    return types.Tool(
        name=raw["name"],
        description=raw.get("description") or "",
        inputSchema=raw.get("inputSchema") or {"type": "object", "properties": {}},
        outputSchema=None,
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


def _extract_api_error(text: str) -> str | None:
    """Turn `API error 400: {"non_field_errors":[...]}` into a readable message."""
    if "API error" not in text:
        return None
    brace = text.find("{")
    if brace >= 0:
        try:
            body = json.loads(text[brace:])
        except json.JSONDecodeError:
            return text
        if isinstance(body, dict):
            for key in ("non_field_errors", "errors", "detail", "message"):
                value = body.get(key)
                if isinstance(value, list) and value:
                    return str(value[0])
                if isinstance(value, str) and value:
                    return value
            return json.dumps(body)
    return text


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
        extracted = _extract_api_error(parsed)
        if extracted:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=extracted)],
                isError=True,
            )
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


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _proxy_bot_id() -> str:
    from src.db.migrate import DEFAULT_BOT_ID

    return os.environ.get("ROBINHOOD_BOT_ID") or DEFAULT_BOT_ID


def _proxy_run_id() -> int | None:
    raw = os.environ.get("ROBINHOOD_RUN_ID")
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None


def _snapshot_quotes(run_id: int | None) -> Any:
    if run_id is None:
        return None
    from src.db.store import Store

    for ev in Store().get_events(run_id):
        if ev.get("type") == "portfolio_snapshot":
            return (ev.get("payload") or {}).get("quotes")
    return None


def _simulate_tool(name: str, args: dict[str, Any]) -> dict[str, Any] | types.CallToolResult | None:
    """Fill paper orders locally so simulation never hits live Robinhood."""
    from src.simulation.ledger import OrderIntent, cancel_paper_orders, is_simulation_mode, submit_paper_order
    from src.trading.mcp_tools import REVIEW_TOOLS, asset_class_for_tool, is_order_write_tool

    bot_id = _proxy_bot_id()
    try:
        if not is_simulation_mode(bot_id):
            return None
    except Exception:
        return None
    if name in REVIEW_TOOLS:
        return {
            "ok": True,
            "simulation": True,
            "message": "Paper trading — preview only; placing will fill against the paper ledger.",
            "symbol": args.get("symbol"),
            "side": args.get("side"),
            "type": args.get("type"),
            "dollar_amount": args.get("dollar_amount"),
            "quantity": args.get("quantity"),
        }
    if not is_order_write_tool(name):
        return None

    asset_class = asset_class_for_tool(name)
    if "exercise" in name.lower():
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="Simulation mode — option exercise is not modeled by the paper broker.")],
            isError=True,
        )

    if asset_class == "crypto":
        from src.trading.crypto import normalize_pair

        symbol = normalize_pair(args.get("symbol") or args.get("pair") or args.get("currency_pair") or "")
        side = str(args.get("side") or "buy").lower()
        meta: dict[str, Any] = {}
    elif asset_class == "option":
        from src.trading.options import (
            option_id_from_args,
            option_meta_from_args,
            option_side_from_args,
            option_symbol_from_args,
        )

        symbol = option_symbol_from_args(args) or option_id_from_args(args)
        side = option_side_from_args(args)
        meta = option_meta_from_args(args)
    else:
        symbol = str(
            args.get("symbol")
            or args.get("chain_symbol")
            or args.get("underlying")
            or ""
        ).upper()
        side = str(args.get("side") or "buy").lower()
        meta = {}

    run_id = _proxy_run_id()
    if "cancel_" in name.lower():
        cancelled = cancel_paper_orders(bot_id, order_id=args.get("order_id"), symbol=symbol or None)
        return {"ok": True, "simulation": True, "cancelled": len(cancelled)}

    intent = OrderIntent(
        symbol=symbol,
        side=side,
        asset_class=asset_class if asset_class != "none" else "equity",
        order_type=str(args.get("type") or args.get("order_type") or "market"),
        qty=_to_float(args.get("quantity") or args.get("contracts")),
        notional=_to_float(
            args.get("dollar_amount")
            or args.get("notional")
            or args.get("amount")
            or args.get("amount_usd")
        ),
        limit_price=_to_float(args.get("limit_price") or args.get("price")),
        stop_price=_to_float(args.get("stop_price")),
        meta=meta,
        intent_key=f"proxy:{run_id}:{symbol}:{side}:{name}",
        run_id=run_id,
    )
    if intent.qty is None and intent.notional is None:
        from src.settings.service import SettingsService

        intent.notional = float(SettingsService(bot_id).read_limits().max_order_notional_usd or 0)
    quotes = _snapshot_quotes(run_id)
    result = submit_paper_order(bot_id, intent, quotes)
    if not result.get("ok"):
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"Simulation mode — paper order rejected: {result.get('reason')}")],
            isError=True,
        )
    return {"ok": True, "simulation": True, "paper": True, **{k: result[k] for k in ("status", "order", "duplicate") if k in result}}


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
    simulated = _simulate_tool(name, args)
    if simulated is not None:
        return simulated
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
