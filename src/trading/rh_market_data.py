"""Robinhood MCP market-data tools normalized to the shared bar/indicator shapes.

Bars come back as ``{"time","open","high","low","close","volume"}`` so the
Massive and Yahoo providers in ``historical_bars`` remain drop-in alternatives.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from src.trading.trade_history import extract_mcp_data

logger = logging.getLogger(__name__)

_HOURLY_ARGS = {"interval": "hour", "span": "week"}
_DAILY_ARGS = {"interval": "day", "span": "month"}


async def _call(tool: str, args: dict[str, Any] | None = None) -> Any | None:
    from src.setup.mcp_client import MCPClient

    result = await MCPClient().call_tool(tool, args or {})
    if not result.get("ok"):
        logger.debug("Robinhood %s failed: %s", tool, result.get("error"))
        return None
    return extract_mcp_data(result)


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso_time(value: Any) -> str:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    return str(value or "")


def normalize_bars(raw: Any) -> list[dict[str, Any]]:
    """Coerce Robinhood historicals into the shared bar shape."""
    rows = _bar_rows(raw)
    bars: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        close = _to_float(row.get("close_price") or row.get("close"))
        if close is None:
            continue
        bars.append(
            {
                "time": _iso_time(row.get("begins_at") or row.get("timestamp") or row.get("time")),
                "open": _to_float(row.get("open_price") or row.get("open")),
                "high": _to_float(row.get("high_price") or row.get("high")),
                "low": _to_float(row.get("low_price") or row.get("low")),
                "close": close,
                "volume": _to_float(row.get("volume")),
            }
        )
    return bars


def _bar_rows(raw: Any) -> list[Any]:
    """Dig bar rows out of the nested shapes Robinhood returns."""
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, dict):
        return []
    for key in ("historicals", "bars", "data_points", "results", "data"):
        value = raw.get(key)
        if isinstance(value, list):
            # results may itself be a list of per-symbol envelopes
            if value and isinstance(value[0], dict) and isinstance(value[0].get("historicals"), list):
                return value[0]["historicals"]
            return value
        if isinstance(value, dict):
            nested = _bar_rows(value)
            if nested:
                return nested
    return []


async def fetch_bars(symbol: str, *, daily: bool = False) -> dict[str, Any]:
    """Fetch bars for one symbol from Robinhood. Returns an error entry on failure."""
    args = dict(_DAILY_ARGS if daily else _HOURLY_ARGS)
    args["symbol"] = symbol
    raw = await _call("get_equity_historicals", args)
    if raw is None:
        return {"error": "get_equity_historicals unavailable", "provider": "robinhood"}
    bars = normalize_bars(raw)
    if not bars:
        return {"error": "No bars returned", "provider": "robinhood"}
    return {"bars": bars, "bar_count": len(bars), "provider": "robinhood"}


async def fetch_technical_indicators(symbol: str) -> dict[str, Any] | None:
    """Native indicators (SMA/EMA/RSI/MACD/Bollinger) for one symbol, if available."""
    raw = await _call("get_equity_technical_indicators", {"symbol": symbol})
    if raw is None:
        return None
    compact = compact_indicators(raw)
    return compact or None


_INDICATOR_KEYS = (
    "sma_20",
    "sma_50",
    "sma_200",
    "ema_12",
    "ema_26",
    "rsi",
    "rsi_14",
    "macd",
    "macd_signal",
    "macd_histogram",
    "bollinger_upper",
    "bollinger_lower",
    "bollinger_middle",
    "atr",
    "vwap",
)


def compact_indicators(raw: Any) -> dict[str, Any]:
    """Flatten an indicators payload into a small numeric dict for the prompt."""
    source = raw
    if isinstance(source, dict):
        for key in ("indicators", "technical_indicators", "data", "results"):
            value = source.get(key)
            if isinstance(value, dict):
                source = value
                break
            if isinstance(value, list) and value and isinstance(value[0], dict):
                source = value[0]
                break
    if not isinstance(source, dict):
        return {}

    out: dict[str, Any] = {}
    for key in _INDICATOR_KEYS:
        number = _to_float(source.get(key))
        if number is not None:
            out[key] = round(number, 4)
    # Robinhood may nest moving averages under a sub-object.
    for group in ("moving_averages", "movingAverages"):
        nested = source.get(group)
        if isinstance(nested, dict):
            for key, value in nested.items():
                number = _to_float(value)
                if number is not None:
                    out[str(key)] = round(number, 4)
    return out


async def fetch_price_book(symbol: str) -> Any | None:
    return await _call("get_equity_price_book", {"symbol": symbol})


async def fetch_fundamentals(symbols: list[str]) -> Any | None:
    normalized = sorted({str(s).upper() for s in symbols if s})
    if not normalized:
        return None
    return await _call("get_equity_fundamentals", {"symbols": normalized})


async def fetch_financials(symbol: str) -> Any | None:
    return await _call("get_financials", {"symbol": symbol})


async def fetch_earnings_results(symbol: str) -> Any | None:
    return await _call("get_earnings_results", {"symbol": symbol})


async def fetch_earnings_calendar(symbols: list[str] | None = None) -> Any | None:
    args: dict[str, Any] = {}
    if symbols:
        args["symbols"] = sorted({str(s).upper() for s in symbols if s})
    return await _call("get_earnings_calendar", args)


async def fetch_indexes() -> Any | None:
    return await _call("get_indexes", {})


async def fetch_index_quotes(symbols: list[str]) -> Any | None:
    normalized = [str(s).upper() for s in symbols if s]
    if not normalized:
        return None
    return await _call("get_index_quotes", {"symbols": normalized})
