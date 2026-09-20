from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from src.db.migrate import DEFAULT_BOT_ID
from src.trading.profile_gate import (
    acknowledge_profile_gate,
    clear_profile_acknowledgement,
    get_profile_gate_status,
)

router = APIRouter(prefix="/api/trading", tags=["trading"])
research_router = APIRouter(prefix="/api/research", tags=["research"])
scans_router = APIRouter(prefix="/api/scans", tags=["scans"])
watchlists_router = APIRouter(prefix="/api/watchlists", tags=["watchlists"])

_CAPABILITIES_TTL_SECONDS = 300
_capabilities_cache: tuple[float, dict[str, Any]] | None = None


@router.get("/profile-gate")
async def profile_gate_status(bot_id: str = DEFAULT_BOT_ID) -> dict[str, Any]:
    return get_profile_gate_status(bot_id=bot_id)


@router.post("/profile-gate/acknowledge")
async def profile_gate_acknowledge(bot_id: str = DEFAULT_BOT_ID) -> dict[str, Any]:
    acknowledge_profile_gate(bot_id)
    return get_profile_gate_status(bot_id=bot_id)


@router.post("/profile-gate/reset")
async def profile_gate_reset(bot_id: str = DEFAULT_BOT_ID) -> dict[str, Any]:
    clear_profile_acknowledgement(bot_id)
    return get_profile_gate_status(bot_id=bot_id)


@router.get("/capabilities")
async def trading_capabilities(refresh: bool = False) -> dict[str, Any]:
    """Live Robinhood MCP tool catalog grouped by category, cached for 5 minutes."""
    global _capabilities_cache
    now = time.monotonic()
    if not refresh and _capabilities_cache and now - _capabilities_cache[0] < _CAPABILITIES_TTL_SECONDS:
        return {**_capabilities_cache[1], "cached": True}

    from src.setup.mcp_client import MCPClient
    from src.trading.mcp_tools import group_tools_by_category

    catalog = await MCPClient().list_tools()
    if not catalog.get("ok"):
        return {"ok": False, "error": catalog.get("error", "tools/list failed")}

    names = [str(n) for n in (catalog.get("tool_names") or [])]
    grouped = group_tools_by_category(names)
    payload: dict[str, Any] = {
        "ok": True,
        "tool_count": len(names),
        "categories": grouped,
        "asset_classes": {
            "equity": bool(grouped.get("equity")),
            "option": bool(grouped.get("option")),
            "crypto": bool(grouped.get("crypto")),
            "scanner": bool(grouped.get("scanner")),
            "market_data": bool(grouped.get("market_data")),
            "watchlist": bool(grouped.get("watchlist")),
            "alert": bool(grouped.get("alert")),
        },
        "cached": False,
    }
    _capabilities_cache = (now, payload)
    return payload


@research_router.get("/search")
async def research_search(q: str, limit: int = 10) -> dict[str, Any]:
    from src.trading.research import search_symbols

    return {"ok": True, "query": q, "results": await search_symbols(q, limit=limit)}


@research_router.get("/{symbol}")
async def research_symbol(symbol: str, financials: bool = True) -> dict[str, Any]:
    from src.trading.research import research_report

    return await research_report(symbol, include_financials=financials)


class ScanCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    filters: list[dict[str, Any]] = Field(default_factory=list)


class ScanFilterUpdate(BaseModel):
    filters: list[dict[str, Any]] = Field(default_factory=list)


@scans_router.get("")
async def list_scans() -> dict[str, Any]:
    from src.trading.scanners import compact_scans, get_scans

    return {"ok": True, "scans": compact_scans(await get_scans())}


@scans_router.get("/filter-specs")
async def scan_filter_specs() -> dict[str, Any]:
    from src.trading.scanners import get_scanner_filter_specs

    return {"ok": True, "specs": await get_scanner_filter_specs()}


@scans_router.post("")
async def add_scan(body: ScanCreate) -> dict[str, Any]:
    from src.trading.scanners import create_scan

    result = await create_scan(body.name, body.filters)
    return {"ok": result is not None, "scan": result}


@scans_router.post("/{scan_id}/run")
async def execute_scan(scan_id: str, limit: int = 20) -> dict[str, Any]:
    from src.trading.scanners import run_scan, scan_symbols

    payload = await run_scan(scan_id)
    return {
        "ok": payload is not None,
        "scan_id": scan_id,
        "symbols": scan_symbols(payload, limit=limit),
    }


@scans_router.patch("/{scan_id}")
async def patch_scan(scan_id: str, body: ScanFilterUpdate) -> dict[str, Any]:
    from src.trading.scanners import update_scan_filters

    result = await update_scan_filters(scan_id, body.filters)
    return {"ok": result is not None, "scan": result}


@watchlists_router.get("")
async def list_watchlists() -> dict[str, Any]:
    from src.trading.watchlists import compact_watchlist_list, get_watchlists

    return {"ok": True, "watchlists": compact_watchlist_list(await get_watchlists())}


@watchlists_router.get("/popular")
async def popular_watchlists() -> dict[str, Any]:
    from src.trading.watchlists import compact_watchlist_list, get_popular_watchlists

    return {"ok": True, "watchlists": compact_watchlist_list(await get_popular_watchlists())}


@watchlists_router.get("/{watchlist}/items")
async def watchlist_items(watchlist: str) -> dict[str, Any]:
    from src.trading.mcp_tools import extract_symbols
    from src.trading.watchlists import get_watchlist_items

    payload = await get_watchlist_items(watchlist)
    return {"ok": payload is not None, "name": watchlist, "symbols": extract_symbols(payload)}
