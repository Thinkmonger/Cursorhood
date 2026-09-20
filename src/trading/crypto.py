"""Crypto trading support: pairs, quotes, positions, orders, preview.

Crypto trades 24/7, so crypto-enabled bots are exempt from the equity
``market_hours_only`` gate in both the risk hook and the scheduler wake logic.
"""

from __future__ import annotations

import logging
from typing import Any

from src.trading.trade_history import extract_mcp_data

logger = logging.getLogger(__name__)


async def _call(tool: str, args: dict[str, Any] | None = None) -> Any | None:
    from src.setup.mcp_client import MCPClient

    result = await MCPClient().call_tool(tool, args or {})
    if not result.get("ok"):
        logger.debug("Robinhood %s failed: %s", tool, result.get("error"))
        return None
    return extract_mcp_data(result)


async def get_currency_pairs() -> Any | None:
    return await _call("get_currency_pairs")


async def get_crypto_quotes(symbols: list[str]) -> Any | None:
    normalized = [normalize_pair(s) for s in symbols if s]
    if not normalized:
        return None
    return await _call("get_crypto_quotes", {"symbols": normalized})


async def get_crypto_positions(account_number: str | None = None) -> Any | None:
    args = {"account_number": account_number} if account_number else {}
    return await _call("get_crypto_positions", args)


async def get_crypto_orders(account_number: str | None = None) -> Any | None:
    args = {"account_number": account_number} if account_number else {}
    return await _call("get_crypto_orders", args)


async def preview_crypto_order(**args: Any) -> Any | None:
    return await _call("preview_crypto_order", args)


def normalize_pair(value: Any) -> str:
    """`btc-usd`, `BTC/USD`, and `BTC` all normalize to `BTC-USD`."""
    text = str(value or "").strip().upper().replace("/", "-").replace("_", "-")
    if not text:
        return ""
    if "-" not in text:
        return f"{text}-USD"
    return text


def base_currency(pair: Any) -> str:
    return normalize_pair(pair).split("-")[0]


def pair_allowed(pair: Any, allowed: list[str]) -> bool:
    """Empty allowlist means no crypto pair is permitted."""
    if not allowed:
        return False
    target = normalize_pair(pair)
    return any(normalize_pair(a) == target for a in allowed)


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rows(payload: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if not isinstance(payload, dict):
        return []
    for key in (*keys, "results", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [r for r in value if isinstance(r, dict)]
        if isinstance(value, dict):
            nested = _rows(value, *keys)
            if nested:
                return nested
    return []


def compact_crypto_positions(payload: Any) -> list[dict[str, Any]]:
    """Crypto holdings for the Context block."""
    out: list[dict[str, Any]] = []
    for row in _rows(payload, "positions", "crypto_positions", "holdings"):
        qty = _to_float(row.get("quantity") or row.get("qty") or row.get("quantity_available"))
        if qty is None or qty == 0:
            continue
        currency = row.get("currency")
        if isinstance(currency, dict):
            currency = currency.get("code")
        pair = normalize_pair(row.get("symbol") or row.get("currency_pair_id") or currency)
        entry: dict[str, Any] = {"pair": pair, "qty": qty}
        cost = _to_float(row.get("average_price") or row.get("cost_basis") or row.get("average_buy_price"))
        if cost is not None:
            entry["avg_cost"] = cost
        mark = _to_float(row.get("mark_price") or row.get("last_price") or row.get("price"))
        if mark is not None:
            entry["price"] = mark
            entry["value"] = round(mark * qty, 2)
        if cost is not None and mark is not None:
            entry["pnl"] = round((mark - cost) * qty, 2)
        out.append(entry)
    return out


def compact_currency_pairs(payload: Any, *, limit: int = 40) -> list[str]:
    pairs: list[str] = []
    for row in _rows(payload, "currency_pairs", "pairs"):
        symbol = row.get("symbol") or row.get("name") or row.get("id")
        pair = normalize_pair(symbol)
        if pair and pair not in pairs:
            pairs.append(pair)
        if len(pairs) >= limit:
            break
    return pairs


def crypto_order_notional(args: dict[str, Any]) -> float | None:
    """Notional for a place_crypto_order payload, from either amount or qty x price."""
    for key in ("amount_usd", "notional", "dollar_amount", "amount"):
        value = _to_float(args.get(key))
        if value is not None:
            return abs(value)
    qty = _to_float(args.get("quantity") or args.get("qty"))
    price = _to_float(args.get("price") or args.get("limit_price"))
    if qty is not None and price is not None:
        return abs(qty * price)
    return None
