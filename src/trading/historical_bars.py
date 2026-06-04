from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from src.http_client import async_client
from src.trading.massive_client import (
    fetch_daily_bars_for_symbol,
    fetch_hourly_bars_for_symbol,
    is_rate_limit_error,
    massive_enabled,
)
from src.trading.massive_rate_limit import get_massive_rate_limiter

logger = logging.getLogger(__name__)

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
BAR_INTERVAL = "1h"
BAR_RANGE = "10d"
DAILY_INTERVAL = "1d"
DAILY_RANGE = "1mo"
SMA_PERIOD = 20
MAX_BARS = 48
MAX_DAILY_BARS = 12


async def fetch_hourly_bars(symbols: list[str]) -> dict[str, Any]:
    """Fetch 1-hour OHLCV bars for technical analysis (Robinhood MCP has no historical tool)."""
    unique = sorted({s.strip().upper() for s in symbols if s and str(s).strip()})
    if not unique:
        return {"interval": BAR_INTERVAL, "symbols": {}}

    limiter = get_massive_rate_limiter()
    out: dict[str, Any] = {
        "interval": BAR_INTERVAL,
        "symbols": {},
        "provider": "massive" if massive_enabled() else "yahoo",
        "rate_limit": limiter.snapshot(),
    }
    if massive_enabled():
        out["provider"] = "massive+yahoo"

    if not massive_enabled():
        for symbol in unique:
            out["symbols"][symbol] = await _fetch_yahoo_hourly_bars(symbol)
        out["rate_limit"] = limiter.snapshot()
        return out

    deferred_for_quota: list[str] = []
    retry_via_massive: list[str] = []

    for symbol in unique:
        entry = await fetch_hourly_bars_for_symbol(symbol, wait=False)
        if not entry.get("error"):
            out["symbols"][symbol] = entry
            continue

        if is_rate_limit_error(entry["error"]):
            deferred_for_quota.append(symbol)
            continue

        logger.info(
            "Massive bars unavailable for %s (%s); falling back to Yahoo",
            symbol,
            entry["error"],
        )
        yahoo_entry = await _fetch_yahoo_hourly_bars(symbol)
        if yahoo_entry.get("error"):
            retry_via_massive.append(symbol)
            out["symbols"][symbol] = yahoo_entry
        else:
            out["symbols"][symbol] = yahoo_entry

    for symbol in deferred_for_quota:
        yahoo_entry = await _fetch_yahoo_hourly_bars(symbol)
        if yahoo_entry.get("error"):
            logger.info(
                "Yahoo bars unavailable for %s (%s); will wait for Massive quota",
                symbol,
                yahoo_entry["error"],
            )
            retry_via_massive.append(symbol)
            out["symbols"][symbol] = yahoo_entry
        else:
            out["symbols"][symbol] = yahoo_entry

    pending = sorted(set(retry_via_massive))
    for symbol in pending:
        logger.info("Waiting for Massive quota to fetch full hourly bars for %s", symbol)
        entry = await fetch_hourly_bars_for_symbol(symbol, wait=True)
        if entry.get("error"):
            logger.warning("Massive retry failed for %s: %s", symbol, entry["error"])
            out["symbols"][symbol] = entry
        else:
            out["symbols"][symbol] = entry

    out["rate_limit"] = limiter.snapshot()
    return out


async def fetch_daily_bars(symbols: list[str]) -> dict[str, Any]:
    """Fetch daily OHLCV candles (Massive first, Yahoo fallback). No SMA needed."""
    unique = sorted({s.strip().upper() for s in symbols if s and str(s).strip()})
    if not unique:
        return {"interval": DAILY_INTERVAL, "symbols": {}}

    out: dict[str, Any] = {
        "interval": DAILY_INTERVAL,
        "symbols": {},
        "provider": "massive+yahoo" if massive_enabled() else "yahoo",
    }
    for symbol in unique:
        entry: dict[str, Any] | None = None
        if massive_enabled():
            entry = await fetch_daily_bars_for_symbol(symbol, wait=False)
            if entry.get("error"):
                logger.info(
                    "Massive daily bars unavailable for %s (%s); falling back to Yahoo",
                    symbol,
                    entry["error"],
                )
                entry = None
        if entry is None:
            entry = await _fetch_yahoo_daily_bars(symbol)
        out["symbols"][symbol] = entry
    return out


async def _fetch_yahoo_daily_bars(symbol: str) -> dict[str, Any]:
    headers = {"User-Agent": "RobinhoodAgenticBot/1.0"}
    try:
        async with async_client(timeout=30) as client:
            response = await client.get(
                YAHOO_CHART_URL.format(symbol=symbol),
                params={"interval": DAILY_INTERVAL, "range": DAILY_RANGE},
                headers=headers,
            )
        if response.status_code >= 400:
            return {"error": f"HTTP {response.status_code}", "provider": "yahoo"}
        bars = _parse_yahoo_chart(response.json())
        if not bars:
            return {"error": "No daily bars returned", "provider": "yahoo"}
        return {
            "bars": bars[-MAX_DAILY_BARS:],
            "bar_count": len(bars),
            "provider": "yahoo",
        }
    except Exception as exc:
        logger.warning("Failed to fetch daily bars for %s via Yahoo: %s", symbol, exc)
        return {"error": str(exc), "provider": "yahoo"}


async def _fetch_yahoo_hourly_bars(symbol: str) -> dict[str, Any]:
    headers = {"User-Agent": "RobinhoodAgenticBot/1.0"}
    try:
        async with async_client(timeout=30) as client:
            response = await client.get(
                YAHOO_CHART_URL.format(symbol=symbol),
                params={"interval": BAR_INTERVAL, "range": BAR_RANGE},
                headers=headers,
            )
        if response.status_code >= 400:
            return {"error": f"HTTP {response.status_code}", "provider": "yahoo"}
        payload = response.json()
        bars = _parse_yahoo_chart(payload)
        if not bars:
            return {"error": "No hourly bars returned", "provider": "yahoo"}
        closes = [b["close"] for b in bars if b.get("close") is not None]
        entry: dict[str, Any] = {
            "bars": bars[-MAX_BARS:],
            "bar_count": len(bars),
            "provider": "yahoo",
        }
        if len(closes) >= SMA_PERIOD:
            entry["sma_20"] = round(sum(closes[-SMA_PERIOD:]) / SMA_PERIOD, 4)
        return entry
    except Exception as exc:
        logger.warning("Failed to fetch 1h bars for %s via Yahoo: %s", symbol, exc)
        return {"error": str(exc), "provider": "yahoo"}


def _parse_yahoo_chart(payload: dict[str, Any]) -> list[dict[str, Any]]:
    chart = payload.get("chart") or {}
    results = chart.get("result") or []
    if not results:
        return []
    result = results[0]
    timestamps = result.get("timestamp") or []
    quotes = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quotes.get("open") or []
    highs = quotes.get("high") or []
    lows = quotes.get("low") or []
    closes = quotes.get("close") or []
    volumes = quotes.get("volume") or []

    bars: list[dict[str, Any]] = []
    for i, ts in enumerate(timestamps):
        close = _num_at(closes, i)
        if close is None:
            continue
        bars.append(
            {
                "time": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                "open": _num_at(opens, i),
                "high": _num_at(highs, i),
                "low": _num_at(lows, i),
                "close": close,
                "volume": _num_at(volumes, i),
            }
        )
    return bars


def _num_at(values: list[Any], index: int) -> float | None:
    if index >= len(values):
        return None
    value = values[index]
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
