"""Robinhood MCP market-data tools normalized to the shared bar/indicator shapes.

Bars come back as ``{"time","open","high","low","close","volume"}`` so the
Massive and Yahoo providers in ``historical_bars`` remain drop-in alternatives.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from src.trading.trade_history import extract_mcp_data

logger = logging.getLogger(__name__)

# Robinhood historicals need an explicit window; these bound the request size.
_HOURLY_LOOKBACK_DAYS = 7
_DAILY_LOOKBACK_DAYS = 60


def unwrap(payload: Any) -> Any:
    """Strip Robinhood's ``{"data": ..., "guide": ...}`` response envelope."""
    if isinstance(payload, dict) and isinstance(payload.get("data"), (dict, list)):
        return payload["data"]
    return payload


def payload_error(payload: Any) -> str | None:
    """The MCP error message carried inside an otherwise-200 response, if any."""
    if isinstance(payload, dict) and payload.get("error") is not None:
        error = payload["error"]
        if isinstance(error, dict):
            return str(error.get("message") or error)
        return str(error)
    return None


def iso_start(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _call(tool: str, args: dict[str, Any] | None = None) -> Any | None:
    from src.setup.mcp_client import MCPClient

    result = await MCPClient().call_tool(tool, args or {})
    if not result.get("ok"):
        logger.debug("Robinhood %s failed: %s", tool, result.get("error"))
        return None
    data = extract_mcp_data(result)
    error = payload_error(data)
    if error:
        logger.debug("Robinhood %s rejected args %s: %s", tool, args, error)
        return None
    return unwrap(data)


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
            # `results` is a list of per-symbol envelopes, each holding the bars.
            if value and isinstance(value[0], dict):
                for nested_key in ("bars", "historicals"):
                    if isinstance(value[0].get(nested_key), list):
                        return value[0][nested_key]
            return value
        if isinstance(value, dict):
            nested = _bar_rows(value)
            if nested:
                return nested
    return []


async def fetch_bars(symbol: str, *, daily: bool = False) -> dict[str, Any]:
    """Fetch bars for one symbol from Robinhood. Returns an error entry on failure."""
    raw = await _call(
        "get_equity_historicals",
        {
            "symbols": [symbol],
            "interval": "day" if daily else "hour",
            "start_time": iso_start(_DAILY_LOOKBACK_DAYS if daily else _HOURLY_LOOKBACK_DAYS),
        },
    )
    if raw is None:
        return {"error": "get_equity_historicals unavailable", "provider": "robinhood"}
    bars = normalize_bars(raw)
    if not bars:
        return {"error": "No bars returned", "provider": "robinhood"}
    return {"bars": bars, "bar_count": len(bars), "provider": "robinhood"}


# Robinhood indicator `type` values are lower-case and fetched one per call.
INDICATOR_TYPES: tuple[str, ...] = ("sma", "ema", "rsi", "macd", "bollinger_bands", "atr", "vwap")

_DEFAULT_INDICATORS: tuple[str, ...] = ("sma", "rsi", "macd")


async def fetch_technical_indicators(
    symbol: str,
    *,
    types: tuple[str, ...] | list[str] = _DEFAULT_INDICATORS,
    interval: str = "hour",
    lookback_days: int = 30,
    period: int | None = None,
) -> dict[str, Any] | None:
    """Native indicators for one symbol. Each type is a separate Robinhood call."""
    wanted = [t for t in dict.fromkeys(types) if t in INDICATOR_TYPES]
    if not wanted:
        return None
    start_time = iso_start(lookback_days)

    async def one(kind: str) -> Any:
        args: dict[str, Any] = {
            "symbol": symbol,
            "type": kind,
            "interval": interval,
            "start_time": start_time,
        }
        if period and kind in ("sma", "ema", "rsi"):
            args["period"] = period
        return await _call("get_equity_technical_indicators", args)

    payloads = await asyncio.gather(*(one(k) for k in wanted), return_exceptions=True)
    out: dict[str, Any] = {}
    for payload in payloads:
        if isinstance(payload, BaseException) or payload is None:
            continue
        out.update(compact_indicators(payload))
    return out or None


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


_SERIES_FIELD_NAMES = {
    "value": "",
    "macd": "macd",
    "signal": "macd_signal",
    "histogram": "macd_histogram",
    "upper": "bollinger_upper",
    "middle": "bollinger_middle",
    "lower": "bollinger_lower",
}


def _latest_series_point(entry: dict[str, Any]) -> dict[str, Any]:
    """Flatten one `{type, params, series:[...]}` block to its most recent values."""
    series = entry.get("series")
    if not isinstance(series, list) or not series:
        return {}
    last = series[-1]
    if not isinstance(last, dict):
        return {}
    kind = str(entry.get("type") or "").lower()
    params = entry.get("params") if isinstance(entry.get("params"), dict) else {}
    period = params.get("period")
    out: dict[str, Any] = {}
    for field, value in last.items():
        if field == "begins_at":
            continue
        number = _to_float(value)
        if number is None:
            continue
        mapped = _SERIES_FIELD_NAMES.get(field, field)
        if mapped:
            name = mapped
        elif kind in ("sma", "ema") and period:
            name = f"{kind}_{int(period)}"
        else:
            name = kind or field
        out[name] = round(number, 4)
    return out


def compact_indicators(raw: Any) -> dict[str, Any]:
    """Flatten an indicators payload into a small numeric dict for the prompt."""
    source = unwrap(raw)

    # Current Robinhood shape: {"indicators": [{"type", "params", "series": [...]}]}
    if isinstance(source, dict) and isinstance(source.get("indicators"), list):
        out: dict[str, Any] = {}
        for entry in source["indicators"]:
            if isinstance(entry, dict):
                out.update(_latest_series_point(entry))
        if out:
            return out

    if isinstance(source, dict):
        for key in ("indicators", "technical_indicators", "results"):
            value = source.get(key)
            if isinstance(value, dict):
                source = value
                break
            if isinstance(value, list) and value and isinstance(value[0], dict):
                source = value[0]
                break
    if not isinstance(source, dict):
        return {}

    out = {}
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
    return await _call("get_equity_price_book", {"symbols": [str(symbol).upper()]})


async def fetch_fundamentals(symbols: list[str]) -> Any | None:
    normalized = sorted({str(s).upper() for s in symbols if s})
    if not normalized:
        return None
    return await _call("get_equity_fundamentals", {"symbols": normalized})


async def fetch_financials(symbol: str) -> Any | None:
    return await _call("get_financials", {"symbols": [str(symbol).upper()]})


async def fetch_earnings_results(symbol: str) -> Any | None:
    return await _call("get_earnings_results", {"symbol": symbol})


async def fetch_earnings_calendar(
    symbols: list[str] | None = None, *, days: int = 14
) -> Any | None:
    """Upcoming earnings. The tool takes a date window, so symbols filter locally."""
    payload = await _call("get_earnings_calendar", {"days": days})
    wanted = {str(s).upper() for s in (symbols or []) if s}
    if not wanted or not isinstance(payload, dict):
        return payload
    rows = payload.get("results")
    if not isinstance(rows, list):
        return payload
    return {
        **payload,
        "results": [
            r for r in rows
            if isinstance(r, dict) and str(r.get("symbol") or "").upper() in wanted
        ],
    }


async def fetch_indexes() -> Any | None:
    return await _call("get_indexes", {})


async def fetch_index_quotes(symbols: list[str]) -> Any | None:
    normalized = [str(s).upper() for s in symbols if s]
    if not normalized:
        return None
    return await _call("get_index_quotes", {"symbols": normalized})
