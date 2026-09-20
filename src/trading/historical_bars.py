"""Historical bars via a provider chain: Robinhood, then Massive, then Yahoo.

Robinhood's agentic MCP exposes `get_equity_historicals`, so it is tried first and
the third-party providers act as fallbacks. Entries keep the `provider` field so
the UI can show where each symbol's data came from.
"""

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


def _robinhood_enabled() -> bool:
    from src.auth.tokens import TokenStore

    try:
        return bool(TokenStore().get_access_token())
    except Exception:
        return False


async def fetch_hourly_bars(
    symbols: list[str],
    *,
    skip_robinhood: bool = False,
    skip_massive: bool = False,
) -> dict[str, Any]:
    """Fetch 1-hour OHLCV bars plus SMA-20 for technical analysis."""
    unique = sorted({s.strip().upper() for s in symbols if s and str(s).strip()})
    limiter = get_massive_rate_limiter()
    if not unique:
        return {"interval": BAR_INTERVAL, "symbols": {}}

    out: dict[str, Any] = {
        "interval": BAR_INTERVAL,
        "symbols": {},
        "provider": _chain_label(),
        "rate_limit": limiter.snapshot(),
    }
    needs_massive_retry: list[str] = []

    for symbol in unique:
        entry = await _hourly_for_symbol(
            symbol,
            skip_robinhood=skip_robinhood,
            skip_massive=skip_massive,
        )
        if entry.get("error") and massive_enabled() and not skip_robinhood and not skip_massive:
            needs_massive_retry.append(symbol)
        out["symbols"][symbol] = entry

    for symbol in needs_massive_retry:
        logger.info("Waiting for Massive quota to fetch hourly bars for %s", symbol)
        entry = await fetch_hourly_bars_for_symbol(symbol, wait=True)
        if entry.get("error"):
            logger.warning("Massive retry failed for %s: %s", symbol, entry["error"])
            continue
        out["symbols"][symbol] = _with_sma(entry)

    out["rate_limit"] = limiter.snapshot()
    return out


async def fetch_daily_bars(
    symbols: list[str],
    *,
    skip_robinhood: bool = False,
    skip_massive: bool = False,
) -> dict[str, Any]:
    """Fetch daily OHLCV candles through the same provider chain."""
    unique = sorted({s.strip().upper() for s in symbols if s and str(s).strip()})
    if not unique:
        return {"interval": DAILY_INTERVAL, "symbols": {}}

    out: dict[str, Any] = {
        "interval": DAILY_INTERVAL,
        "symbols": {},
        "provider": _chain_label(),
    }
    for symbol in unique:
        out["symbols"][symbol] = await _daily_for_symbol(
            symbol,
            skip_robinhood=skip_robinhood,
            skip_massive=skip_massive,
        )
    return out


def _chain_label() -> str:
    providers = []
    if _robinhood_enabled():
        providers.append("robinhood")
    if massive_enabled():
        providers.append("massive")
    providers.append("yahoo")
    return "+".join(providers)


async def _hourly_for_symbol(
    symbol: str,
    *,
    skip_robinhood: bool = False,
    skip_massive: bool = False,
) -> dict[str, Any]:
    if _robinhood_enabled() and not skip_robinhood:
        from src.trading.rh_market_data import fetch_bars

        entry = await fetch_bars(symbol, daily=False)
        if not entry.get("error"):
            entry["bars"] = entry["bars"][-MAX_BARS:]
            return await _with_native_indicators(symbol, _with_sma(entry))
        logger.info("Robinhood bars unavailable for %s (%s)", symbol, entry["error"])

    if massive_enabled() and not skip_massive:
        entry = await fetch_hourly_bars_for_symbol(symbol, wait=False)
        if not entry.get("error"):
            return _with_sma(entry)
        if not is_rate_limit_error(entry["error"]):
            logger.info("Massive bars unavailable for %s (%s)", symbol, entry["error"])

    return await _fetch_yahoo_hourly_bars(symbol)


async def _daily_for_symbol(
    symbol: str,
    *,
    skip_robinhood: bool = False,
    skip_massive: bool = False,
) -> dict[str, Any]:
    if _robinhood_enabled() and not skip_robinhood:
        from src.trading.rh_market_data import fetch_bars

        entry = await fetch_bars(symbol, daily=True)
        if not entry.get("error"):
            entry["bars"] = entry["bars"][-MAX_DAILY_BARS:]
            return entry
        logger.info("Robinhood daily bars unavailable for %s (%s)", symbol, entry["error"])

    if massive_enabled() and not skip_massive:
        entry = await fetch_daily_bars_for_symbol(symbol, wait=False)
        if not entry.get("error"):
            return entry
        logger.info("Massive daily bars unavailable for %s (%s)", symbol, entry["error"])

    return await _fetch_yahoo_daily_bars(symbol)


def _with_sma(entry: dict[str, Any]) -> dict[str, Any]:
    """Compute SMA-20 locally when the provider does not supply indicators."""
    if entry.get("sma_20") is not None:
        return entry
    closes = [b["close"] for b in entry.get("bars") or [] if b.get("close") is not None]
    if len(closes) >= SMA_PERIOD:
        entry["sma_20"] = round(sum(closes[-SMA_PERIOD:]) / SMA_PERIOD, 4)
    return entry


async def _with_native_indicators(symbol: str, entry: dict[str, Any]) -> dict[str, Any]:
    """Prefer Robinhood's native indicators; local SMA stays as the fallback."""
    from src.trading.rh_market_data import fetch_technical_indicators

    indicators = await fetch_technical_indicators(symbol)
    if not indicators:
        return entry
    entry["indicators"] = indicators
    if indicators.get("sma_20") is not None:
        entry["sma_20"] = indicators["sma_20"]
    return entry


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
        bars = _parse_yahoo_chart(response.json())
        if not bars:
            return {"error": "No hourly bars returned", "provider": "yahoo"}
        return _with_sma(
            {
                "bars": bars[-MAX_BARS:],
                "bar_count": len(bars),
                "provider": "yahoo",
            }
        )
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
