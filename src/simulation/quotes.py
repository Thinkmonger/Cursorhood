"""Price lookup for the paper broker across equity, option, and crypto symbols."""

from __future__ import annotations

import logging
from typing import Any

from src.simulation.positions import CRYPTO, EQUITY, OPTION

logger = logging.getLogger(__name__)


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def equity_price(quotes_payload: Any, symbol: str) -> float | None:
    from src.stats.portfolio import _quote_prices

    row = _quote_prices(quotes_payload).get(symbol.upper()) or {}
    return _as_float(row.get("last_price"))


def merge_quotes(existing: Any | None, extra: Any | None) -> Any | None:
    """Merge a freshly fetched quote payload into the cycle's snapshot quotes."""
    if not extra:
        return existing
    if not existing:
        return extra
    if not isinstance(existing, dict) or not isinstance(extra, dict):
        return extra or existing
    existing_data = existing.get("data") if isinstance(existing.get("data"), dict) else {}
    extra_data = extra.get("data") if isinstance(extra.get("data"), dict) else {}
    merged: list[Any] = []
    seen: set[str] = set()
    for item in (extra_data.get("results") or []) + (existing_data.get("results") or []):
        if not isinstance(item, dict):
            continue
        quote = item.get("quote") or {}
        sym = str(quote.get("symbol") or "").upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        merged.append(item)
    return {"data": {**existing_data, **extra_data, "results": merged}}


def resolve_price(
    symbol: str,
    asset_class: str,
    quotes_payload: Any | None,
    meta: dict[str, Any] | None = None,
) -> float | None:
    """Best available price for a paper fill, fetching live if the snapshot lacks it."""
    ticker = str(symbol or "").upper()

    if asset_class == OPTION:
        option_id = str((meta or {}).get("option_id") or ticker).strip()
        if not option_id:
            return None
        return _fetch_sync(_fetch_option, option_id)
    if not ticker:
        return None

    if asset_class == EQUITY:
        price = equity_price(quotes_payload, ticker)
        if price is not None:
            return price
        return _fetch_sync(_fetch_equity, ticker)
    if asset_class == CRYPTO:
        price = equity_price(quotes_payload, ticker)
        if price is not None:
            return price
        price = _first_price(quotes_payload, ticker)
        if price is not None:
            return price
        return _fetch_sync(_fetch_crypto, ticker)
    return equity_price(quotes_payload, ticker)


def resolve_price_and_quotes(
    symbol: str,
    quotes_payload: Any | None,
    *,
    fetch_if_missing: bool = True,
) -> tuple[float | None, Any | None]:
    """Equity price plus the (possibly extended) quotes payload for reuse."""
    ticker = str(symbol).upper()
    price = equity_price(quotes_payload, ticker)
    if price is not None or not fetch_if_missing:
        return price, quotes_payload
    try:
        from src.setup.mcp_client import fetch_equity_quotes_sync

        fetched = fetch_equity_quotes_sync([ticker])
    except Exception:
        fetched = None
    if not fetched:
        return None, quotes_payload
    merged = merge_quotes(quotes_payload, fetched)
    return equity_price(merged, ticker), merged


async def _fetch_equity(symbol: str) -> float | None:
    from src.setup.mcp_client import fetch_equity_quotes

    payload = await fetch_equity_quotes([symbol])
    return equity_price(payload, symbol)


async def _fetch_crypto(symbol: str) -> float | None:
    from src.trading.crypto import get_crypto_quotes, normalize_pair

    payload = await get_crypto_quotes([symbol])
    return _first_price(payload, normalize_pair(symbol))


async def _fetch_option(option_id: str) -> float | None:
    from src.trading.options import get_option_quotes

    payload = await get_option_quotes([option_id])
    return _first_price(payload, option_id)


def _first_price(payload: Any, symbol: str) -> float | None:
    rows: list[Any] = []
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        for key in ("quotes", "results", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                rows = value
                break
            if isinstance(value, dict):
                rows = [value]
                break
        else:
            rows = [payload]
    for row in rows:
        if not isinstance(row, dict):
            continue
        quote = row.get("quote") if isinstance(row.get("quote"), dict) else row
        row_symbol = str(quote.get("symbol") or quote.get("id") or "").upper()
        if row_symbol and symbol and row_symbol != symbol.upper():
            continue
        for key in ("mark_price", "last_trade_price", "last_price", "price", "ask_price", "bid_price"):
            price = _as_float(quote.get(key))
            if price is not None and price > 0:
                return price
    return None


def _fetch_sync(coro_fn: Any, symbol: str) -> float | None:
    """Run an async price fetch from sync code, even inside a running loop."""
    import asyncio
    import concurrent.futures

    def _run() -> float | None:
        try:
            return asyncio.run(coro_fn(symbol))
        except Exception as exc:
            logger.debug("Paper price fetch failed for %s: %s", symbol, exc)
            return None

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _run()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        try:
            return executor.submit(_run).result(timeout=30)
        except Exception:
            return None
