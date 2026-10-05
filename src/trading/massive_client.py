from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from src.http_client import async_client
from src.trading.massive_rate_limit import get_massive_rate_limiter

logger = logging.getLogger(__name__)

MASSIVE_BASE_URL = os.environ.get("MASSIVE_API_BASE_URL", "https://api.massive.com").rstrip("/")
BAR_LOOKBACK_DAYS = 10
MAX_BARS = 48
SMA_PERIOD = 20
DEFAULT_RETRY_WAIT_SECONDS = 65.0
DAILY_LOOKBACK_DAYS = 21
MAX_DAILY_BARS = 12


_CRYPTO_QUOTES = ("USD", "USDT", "USDC", "EUR")


def massive_ticker(symbol: str) -> str:
    """Massive crypto aggregates use ``X:BTCUSD``, not ``BTC-USD``."""
    text = (symbol or "").strip().upper()
    if "-" in text:
        base, quote = text.split("-", 1)
        if base and quote in _CRYPTO_QUOTES:
            return f"X:{base}{quote}"
    return text


def massive_api_key() -> str | None:
    """Env, OS keyring, then `.env`. POLYGON_API_KEY remains an env-only alias."""
    from src.auth.secrets import get_secret

    key = get_secret("MASSIVE_API_KEY")
    if key:
        return key.strip()
    poly = (os.environ.get("POLYGON_API_KEY") or "").strip()
    return poly or None


def massive_enabled() -> bool:
    return massive_api_key() is not None


def _retry_wait_seconds() -> float:
    raw = os.environ.get("MASSIVE_RETRY_WAIT_SECONDS", str(DEFAULT_RETRY_WAIT_SECONDS)).strip()
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_RETRY_WAIT_SECONDS
    return max(5.0, value)


def _use_sma_endpoint() -> bool:
    value = os.environ.get("MASSIVE_USE_SMA_ENDPOINT", "true").strip().lower()
    return value not in ("0", "false", "no", "off")


def is_rate_limit_error(message: str | None) -> bool:
    if not message:
        return False
    lowered = message.lower()
    return any(
        token in lowered
        for token in ("rate limit", "quota exhausted", "http 429", "429")
    )


async def _acquire_massive_slot(*, wait: bool, wait_timeout: float | None) -> bool:
    limiter = get_massive_rate_limiter()
    if wait:
        return await limiter.wait_for_slot(timeout=wait_timeout if wait_timeout is not None else _retry_wait_seconds())
    return await limiter.try_acquire()


async def _massive_get(path: str, params: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    api_key = massive_api_key()
    if not api_key:
        return None, "MASSIVE_API_KEY not configured"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "RobinhoodAgenticBot/1.0",
    }
    try:
        async with async_client(timeout=30) as client:
            response = await client.get(f"{MASSIVE_BASE_URL}{path}", headers=headers, params=params)
    except Exception as exc:
        logger.warning("Massive request failed for %s: %s", path, exc)
        return None, str(exc)

    if response.status_code == 429:
        await get_massive_rate_limiter().mark_remote_exhausted()
        return None, "Massive HTTP 429 (rate limited)"
    if response.status_code == 401:
        return None, "Massive HTTP 401 (invalid API key)"
    if response.status_code >= 400:
        return None, f"Massive HTTP {response.status_code}"

    try:
        payload = response.json()
    except ValueError as exc:
        return None, f"Invalid Massive JSON: {exc}"

    status = str(payload.get("status") or "").upper()
    if status and status not in ("OK", "DELAYED"):
        return None, f"Massive status {status}"
    return payload, None


def _entry_from_bars(bars: list[dict[str, Any]], sma_20: float | None = None) -> dict[str, Any]:
    if not bars:
        return {"error": "No hourly bars returned"}
    entry: dict[str, Any] = {
        "bars": bars[-MAX_BARS:],
        "bar_count": len(bars),
        "provider": "massive",
    }
    if sma_20 is not None:
        entry["sma_20"] = round(float(sma_20), 4)
        entry["sma_source"] = "massive"
    else:
        closes = [b["close"] for b in bars if b.get("close") is not None]
        if len(closes) >= SMA_PERIOD:
            entry["sma_20"] = round(sum(closes[-SMA_PERIOD:]) / SMA_PERIOD, 4)
    return entry


async def _fetch_massive_hourly_sma(ticker: str, start: datetime.date, end: datetime.date) -> dict[str, Any]:
    payload, error = await _massive_get(
        f"/v1/indicators/sma/{ticker}",
        {
            "timespan": "hour",
            "window": SMA_PERIOD,
            "series_type": "close",
            "adjusted": "true",
            "order": "asc",
            "limit": 5000,
            "expand_underlying": "true",
            "timestamp.gte": start.isoformat(),
            "timestamp.lte": end.isoformat(),
        },
    )
    if error:
        return {"error": error}

    results = (payload or {}).get("results") or {}
    aggregates = ((results.get("underlying") or {}).get("aggregates") or [])
    bars = _parse_massive_aggs({"results": aggregates})
    values = results.get("values") or []
    sma_20 = None
    if values:
        last = values[-1]
        if isinstance(last, dict) and last.get("value") is not None:
            sma_20 = float(last["value"])
    return _entry_from_bars(bars, sma_20=sma_20)


async def _fetch_massive_hourly_aggs(ticker: str, start: datetime.date, end: datetime.date) -> dict[str, Any]:
    payload, error = await _massive_get(
        f"/v2/aggs/ticker/{ticker}/range/1/hour/{start.isoformat()}/{end.isoformat()}",
        {"adjusted": "true", "sort": "asc", "limit": 5000},
    )
    if error:
        return {"error": error}
    bars = _parse_massive_aggs(payload or {})
    return _entry_from_bars(bars)


async def fetch_hourly_bars_for_symbol(
    symbol: str,
    *,
    wait: bool = False,
    wait_timeout: float | None = None,
) -> dict[str, Any]:
    """Fetch 1-hour OHLCV bars (+ SMA-20 when available) for one ticker via Massive."""
    if not massive_api_key():
        return {"error": "MASSIVE_API_KEY not configured"}

    timeout = wait_timeout if wait_timeout is not None else _retry_wait_seconds()
    attempts = 3 if wait else 1
    last_error = "Massive rate limit reached (calls/minute quota exhausted)"

    for attempt in range(attempts):
        if not await _acquire_massive_slot(wait=wait, wait_timeout=timeout):
            if wait:
                return {"error": "Massive rate limit wait timed out"}
            return {"error": "Massive rate limit reached (calls/minute quota exhausted)"}

        ticker = massive_ticker(symbol)
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=BAR_LOOKBACK_DAYS)
        use_sma = _use_sma_endpoint() and not ticker.startswith("X:")

        if use_sma:
            entry = await _fetch_massive_hourly_sma(ticker, start, end)
            if not entry.get("error"):
                return entry
            last_error = entry["error"]
            if not is_rate_limit_error(last_error):
                logger.info("Massive SMA endpoint failed for %s (%s); trying aggs", ticker, last_error)
                entry = await _fetch_massive_hourly_aggs(ticker, start, end)
                if not entry.get("error"):
                    return entry
                last_error = entry["error"]
        else:
            entry = await _fetch_massive_hourly_aggs(ticker, start, end)
            if not entry.get("error"):
                return entry
            last_error = entry["error"]

        if is_rate_limit_error(last_error):
            if wait and attempt + 1 < attempts:
                logger.info(
                    "Massive rate limited for %s (attempt %s/%s); waiting for quota",
                    ticker,
                    attempt + 1,
                    attempts,
                )
                continue
        return {"error": last_error}

    return {"error": last_error}


async def _fetch_massive_daily_aggs(ticker: str, start: datetime.date, end: datetime.date) -> dict[str, Any]:
    payload, error = await _massive_get(
        f"/v2/aggs/ticker/{ticker}/range/1/day/{start.isoformat()}/{end.isoformat()}",
        {"adjusted": "true", "sort": "asc", "limit": 5000},
    )
    if error:
        return {"error": error}
    bars = _parse_massive_aggs(payload or {})
    if not bars:
        return {"error": "No daily bars returned"}
    return {"bars": bars[-MAX_DAILY_BARS:], "bar_count": len(bars), "provider": "massive"}


async def fetch_daily_bars_for_symbol(
    symbol: str,
    *,
    wait: bool = False,
    wait_timeout: float | None = None,
) -> dict[str, Any]:
    """Fetch daily OHLCV candles for one ticker via Massive."""
    if not massive_api_key():
        return {"error": "MASSIVE_API_KEY not configured"}

    timeout = wait_timeout if wait_timeout is not None else _retry_wait_seconds()
    if not await _acquire_massive_slot(wait=wait, wait_timeout=timeout):
        if wait:
            return {"error": "Massive rate limit wait timed out"}
        return {"error": "Massive rate limit reached (calls/minute quota exhausted)"}

    ticker = massive_ticker(symbol)
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=DAILY_LOOKBACK_DAYS)
    entry = await _fetch_massive_daily_aggs(ticker, start, end)
    return entry


def _parse_massive_aggs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    results = payload.get("results") or []
    bars: list[dict[str, Any]] = []
    for row in results:
        if not isinstance(row, dict):
            continue
        close = _num(row.get("c"))
        if close is None:
            continue
        ts_ms = row.get("t")
        if ts_ms is None:
            continue
        try:
            ts = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OSError):
            continue
        bars.append(
            {
                "time": ts,
                "open": _num(row.get("o")),
                "high": _num(row.get("h")),
                "low": _num(row.get("l")),
                "close": close,
                "volume": _num(row.get("v")),
            }
        )
    return bars


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def test_connection(api_key: str | None = None) -> dict[str, Any]:
    """Verify Massive credentials with a single hourly-bars request (uses 1 API call)."""
    key = (api_key or massive_api_key() or "").strip()
    if not key:
        return {"ok": False, "error": "No Massive API key configured"}

    previous = os.environ.get("MASSIVE_API_KEY")
    os.environ["MASSIVE_API_KEY"] = key
    try:
        result = await fetch_hourly_bars_for_symbol("AAPL")
    finally:
        if previous is None:
            os.environ.pop("MASSIVE_API_KEY", None)
        else:
            os.environ["MASSIVE_API_KEY"] = previous

    if result.get("error"):
        return {"ok": False, "error": result["error"]}

    limiter = get_massive_rate_limiter()
    return {
        "ok": True,
        "symbol": "AAPL",
        "bar_count": result.get("bar_count"),
        "sma_20": result.get("sma_20"),
        "rate_limit": limiter.snapshot(),
    }
