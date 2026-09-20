"""Robinhood scanner tools: saved scans, filter specs, creation, and execution."""

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


async def get_scans() -> Any | None:
    return await _call("get_scans")


async def get_scanner_filter_specs() -> Any | None:
    return await _call("get_scanner_filter_specs")


async def create_scan(name: str, filters: list[dict[str, Any]] | None = None, **config: Any) -> Any | None:
    args: dict[str, Any] = {"name": name, **config}
    if filters:
        args["filters"] = filters
    return await _call("create_scan", args)


async def run_scan(scan_id: str) -> Any | None:
    return await _call("run_scan", {"scan_id": scan_id})


async def update_scan_filters(scan_id: str, filters: list[dict[str, Any]]) -> Any | None:
    return await _call("update_scan_filters", {"scan_id": scan_id, "filters": filters})


async def update_scan_config(scan_id: str, **config: Any) -> Any | None:
    return await _call("update_scan_config", {"scan_id": scan_id, **config})


def scan_symbols(payload: Any, *, limit: int = 20) -> list[str]:
    """Pull the result symbols out of a run_scan payload."""
    if isinstance(payload, dict):
        for key in ("results", "symbols", "instruments", "data", "matches"):
            value = payload.get(key)
            if value is not None:
                symbols = extract_symbols(value, limit=limit)
                if symbols:
                    return symbols
    return extract_symbols(payload, limit=limit)


def compact_scans(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    source = payload
    if isinstance(source, dict):
        for key in ("scans", "results", "data"):
            if isinstance(source.get(key), list):
                source = source[key]
                break
    if not isinstance(source, list):
        return rows
    for item in source:
        if not isinstance(item, dict):
            continue
        row = {
            "id": str(item.get("id") or item.get("scan_id") or ""),
            "name": str(item.get("name") or item.get("display_name") or ""),
        }
        filters = item.get("filters")
        if isinstance(filters, list):
            row["filter_count"] = len(filters)
        if row["id"] or row["name"]:
            rows.append(row)
    return rows
