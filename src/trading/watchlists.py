"""Robinhood watchlist tools with compaction for prompts and the console UI."""

from __future__ import annotations

import logging
from typing import Any

from src.trading.mcp_tools import extract_symbols
from src.trading.trade_history import extract_mcp_data

logger = logging.getLogger(__name__)


async def _call(tool: str, args: dict[str, Any] | None = None) -> Any | None:
    from src.setup.mcp_client import MCPClient

    result = await MCPClient().call_tool(tool, args or {})
    if not result.get("ok"):
        logger.debug("Robinhood %s failed: %s", tool, result.get("error"))
        return None
    return extract_mcp_data(result)


async def get_watchlists() -> Any | None:
    return await _call("get_watchlists")


async def get_watchlist_items(watchlist: str) -> Any | None:
    return await _call("get_watchlist_items", {"watchlist_name": watchlist})


async def get_option_watchlist() -> Any | None:
    return await _call("get_option_watchlist")


async def get_popular_watchlists() -> Any | None:
    return await _call("get_popular_watchlists")


async def create_watchlist(name: str) -> Any | None:
    return await _call("create_watchlist", {"name": name})


async def update_watchlist(watchlist: str, **fields: Any) -> Any | None:
    return await _call("update_watchlist", {"watchlist_name": watchlist, **fields})


async def follow_watchlist(watchlist: str) -> Any | None:
    return await _call("follow_watchlist", {"watchlist_name": watchlist})


async def unfollow_watchlist(watchlist: str) -> Any | None:
    return await _call("unfollow_watchlist", {"watchlist_name": watchlist})


async def add_to_watchlist(watchlist: str, symbols: list[str]) -> Any | None:
    return await _call(
        "add_to_watchlist",
        {"watchlist_name": watchlist, "symbols": [s.upper() for s in symbols]},
    )


async def remove_from_watchlist(watchlist: str, symbols: list[str]) -> Any | None:
    return await _call(
        "remove_from_watchlist",
        {"watchlist_name": watchlist, "symbols": [s.upper() for s in symbols]},
    )


async def add_option_to_watchlist(option_id: str) -> Any | None:
    return await _call("add_option_to_watchlist", {"option_id": option_id})


async def remove_option_from_watchlist(option_id: str) -> Any | None:
    return await _call("remove_option_from_watchlist", {"option_id": option_id})


def compact_watchlist_list(payload: Any, *, max_symbols: int = 30) -> list[dict[str, Any]]:
    """Normalize any watchlist collection payload into name/symbol rows."""
    data = payload
    if isinstance(data, dict):
        for key in ("watchlists", "results", "data"):
            value = data.get(key)
            if isinstance(value, list):
                data = value
                break
            if isinstance(value, dict) and isinstance(value.get("watchlists"), list):
                data = value["watchlists"]
                break
    if not isinstance(data, list):
        return []

    rows: list[dict[str, Any]] = []
    for item in data:
        if isinstance(item, str):
            rows.append({"name": item})
            continue
        if not isinstance(item, dict):
            continue
        row: dict[str, Any] = {}
        name = item.get("name") or item.get("display_name") or item.get("id")
        if name:
            row["name"] = str(name)
        if item.get("id") and item.get("id") != name:
            row["id"] = str(item["id"])
        symbols = extract_symbols(item.get("symbols") or item.get("items"))
        if symbols:
            row["symbols"] = symbols[:max_symbols]
            row["symbol_count"] = len(symbols)
        if row:
            rows.append(row)
    return rows


async def resolve_symbols(
    *,
    source: str,
    ref: str | None,
    static_symbols: list[str],
    limit: int = 20,
) -> tuple[list[str], str]:
    """Resolve a bot's tradable symbols for this cycle.

    Returns the symbols plus a short description of where they came from. Any
    failure falls back to the static allowlist so a cycle never trades blind.
    """
    static = [str(s).upper() for s in static_symbols if s]
    if source == "static" or not ref:
        return static[:limit], "static"

    try:
        if source == "watchlist":
            payload = await get_watchlist_items(ref)
            symbols = extract_symbols(payload, limit=limit)
            if not symbols:
                rows = compact_watchlist_list(await get_watchlists())
                symbols = _symbols_from_named_row(rows, ref, limit)
            if symbols:
                return symbols, f"watchlist:{ref}"

        elif source == "popular_watchlist":
            rows = compact_watchlist_list(await get_popular_watchlists())
            symbols = _symbols_from_named_row(rows, ref, limit)
            if symbols:
                return symbols, f"popular_watchlist:{ref}"

        elif source == "scan":
            from src.trading.scanners import run_scan, scan_symbols

            symbols = scan_symbols(await run_scan(ref), limit=limit)
            if symbols:
                return symbols, f"scan:{ref}"
    except Exception as exc:
        logger.warning("Symbol source %s/%s failed: %s", source, ref, exc)

    logger.info("Symbol source %s/%s resolved nothing; using static allowlist", source, ref)
    return static[:limit], "static_fallback"


def _symbols_from_named_row(rows: list[dict[str, Any]], ref: str, limit: int) -> list[str]:
    target = ref.strip().lower()
    for row in rows:
        if str(row.get("name", "")).lower() == target or str(row.get("id", "")).lower() == target:
            return list(row.get("symbols") or [])[:limit]
    return []
