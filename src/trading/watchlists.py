"""Robinhood watchlist tools with compaction for prompts and the console UI."""

from __future__ import annotations

import logging
import re
from typing import Any

from src.trading.mcp_tools import extract_symbols
from src.trading.trade_history import extract_mcp_data

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


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
    list_id = await resolve_watchlist_id(watchlist)
    if not list_id:
        logger.debug("No watchlist id for %s", watchlist)
        return None
    return await _call("get_watchlist_items", {"list_id": list_id})


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
        ident = item.get("id") or item.get("watchlist_id") or item.get("list_id")
        if ident and str(ident) != str(name):
            row["id"] = str(ident)
        symbols = extract_symbols(item.get("symbols") or item.get("items"))
        if symbols:
            row["symbols"] = symbols[:max_symbols]
            row["symbol_count"] = len(symbols)
        if row:
            rows.append(row)
    return rows


async def resolve_watchlist_id(ref: str) -> str | None:
    text = str(ref or "").strip()
    if not text:
        return None
    if _UUID_RE.match(text):
        return text
    target = text.lower()
    for payload in (await get_watchlists(), await get_popular_watchlists()):
        for row in compact_watchlist_list(payload):
            name = str(row.get("name") or "").lower()
            ident = str(row.get("id") or "")
            if name == target or ident.lower() == target:
                return ident or None
    return None


async def resolve_symbols(
    *,
    source: str,
    ref: str | None,
    static_symbols: list[str],
    limit: int = 20,
    asset_class: str = "equity",
) -> tuple[list[str], str]:
    """Resolve a bot's tradable symbols for this cycle.

    Returns the symbols plus a short description of where they came from. Any
    failure falls back to the static allowlist so a cycle never trades blind.
    """
    static = [str(s).upper() for s in static_symbols if s]
    if asset_class == "crypto":
        static = _normalize_crypto_pairs(static)
    if source == "static" or not ref:
        return static[:limit], "static"

    object_types = {"currency_pair"} if asset_class == "crypto" else {"instrument"}

    try:
        if source in ("watchlist", "popular_watchlist"):
            payload = await get_watchlist_items(ref)
            symbols = extract_symbols(payload, limit=limit, object_types=object_types)
            if not symbols:
                rows = compact_watchlist_list(
                    await (get_watchlists() if source == "watchlist" else get_popular_watchlists())
                )
                symbols = _symbols_from_named_row(rows, ref, limit)
            if symbols:
                if asset_class == "crypto":
                    symbols = _normalize_crypto_pairs(symbols)
                return symbols, f"{source}:{ref}"

        elif source == "scan":
            from src.trading.scanners import run_scan, scan_symbols

            symbols = scan_symbols(await run_scan(ref), limit=limit)
            if symbols:
                if asset_class == "crypto":
                    symbols = _normalize_crypto_pairs(symbols)
                return symbols, f"scan:{ref}"
    except Exception as exc:
        logger.warning("Symbol source %s/%s failed: %s", source, ref, exc)

    logger.info("Symbol source %s/%s resolved nothing; using static allowlist", source, ref)
    return static[:limit], "static_fallback"


def _normalize_crypto_pairs(symbols: list[str]) -> list[str]:
    from src.trading.crypto import normalize_pair

    out: list[str] = []
    for raw in symbols:
        pair = normalize_pair(raw)
        if pair and pair not in out:
            out.append(pair)
    return out


def _symbols_from_named_row(rows: list[dict[str, Any]], ref: str, limit: int) -> list[str]:
    target = ref.strip().lower()
    for row in rows:
        if str(row.get("name", "")).lower() == target or str(row.get("id", "")).lower() == target:
            return list(row.get("symbols") or [])[:limit]
    return []
