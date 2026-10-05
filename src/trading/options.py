"""Single-leg options support: chains, instruments, quotes, positions, orders.

Chains are never prefetched wholesale — ``compact_chain`` bounds them to
near-the-money strikes across a few expiries so an on-demand agent call stays
within the prompt budget.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from src.trading.trade_history import extract_mcp_data

logger = logging.getLogger(__name__)

OPTION_MULTIPLIER = 100
MAX_CHAIN_EXPIRIES = 3
MAX_CHAIN_STRIKES = 8


async def _call(tool: str, args: dict[str, Any] | None = None) -> Any | None:
    from src.setup.mcp_client import MCPClient

    result = await MCPClient().call_tool(tool, args or {})
    if not result.get("ok"):
        logger.debug("Robinhood %s failed: %s", tool, result.get("error"))
        return None
    return extract_mcp_data(result)


async def get_option_level_upgrade_info() -> Any | None:
    return await _call("get_option_level_upgrade_info")


async def get_option_chains(symbol: str) -> Any | None:
    return await _call("get_option_chains", {"symbol": symbol})


async def get_option_instruments(symbol: str, **filters: Any) -> Any | None:
    return await _call("get_option_instruments", {"symbol": symbol, **filters})


async def get_option_quotes(option_ids: list[str]) -> Any | None:
    ids = [str(i) for i in option_ids if i]
    if not ids:
        return None
    return await _call("get_option_quotes", {"option_ids": ids})


async def get_option_positions(account_number: str | None = None) -> Any | None:
    args = {"account_number": account_number} if account_number else {}
    return await _call("get_option_positions", args)


async def get_option_orders(account_number: str | None = None) -> Any | None:
    args = {"account_number": account_number} if account_number else {}
    return await _call("get_option_orders", args)


async def get_option_historicals(option_id: str, **args: Any) -> Any | None:
    return await _call("get_option_historicals", {"option_id": option_id, **args})


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


def parse_expiry(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def days_to_expiry(value: Any, today: date | None = None) -> int | None:
    expiry = parse_expiry(value)
    if expiry is None:
        return None
    return (expiry - (today or date.today())).days


def compact_option_positions(payload: Any) -> list[dict[str, Any]]:
    """Option holdings for the Context block: expiry, strike, type, qty, mark, P/L."""
    out: list[dict[str, Any]] = []
    for row in _rows(payload, "positions", "option_positions"):
        qty = _to_float(row.get("quantity") or row.get("qty"))
        if qty is None or qty == 0:
            continue
        entry: dict[str, Any] = {
            "symbol": str(row.get("chain_symbol") or row.get("symbol") or "").upper(),
            "qty": qty,
        }
        expiry = row.get("expiration_date") or row.get("expiry")
        if expiry:
            entry["expiry"] = str(expiry)[:10]
            dte = days_to_expiry(expiry)
            if dte is not None:
                entry["dte"] = dte
        strike = _to_float(row.get("strike_price") or row.get("strike"))
        if strike is not None:
            entry["strike"] = strike
        option_type = row.get("option_type") or row.get("type")
        if option_type:
            entry["type"] = str(option_type).lower()
        cost = _to_float(row.get("average_price") or row.get("average_open_price"))
        if cost is not None:
            entry["avg_cost"] = cost
        mark = _to_float(row.get("mark_price") or row.get("last_price") or row.get("price"))
        if mark is not None:
            entry["mark"] = mark
        if cost is not None and mark is not None:
            entry["pnl"] = round((mark - cost) * qty * OPTION_MULTIPLIER, 2)
        if row.get("id") or row.get("option_id"):
            entry["option_id"] = str(row.get("option_id") or row.get("id"))
        out.append(entry)
    return out


def compact_chain(
    payload: Any,
    *,
    underlying_price: float | None = None,
    max_expiries: int = MAX_CHAIN_EXPIRIES,
    max_strikes: int = MAX_CHAIN_STRIKES,
) -> list[dict[str, Any]]:
    """Bound a chain to near-the-money strikes across the nearest expiries."""
    rows = _rows(payload, "chains", "instruments", "options")
    if not rows:
        return []

    by_expiry: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        expiry = str(row.get("expiration_date") or row.get("expiry") or "")[:10]
        if not expiry:
            continue
        by_expiry.setdefault(expiry, []).append(row)

    out: list[dict[str, Any]] = []
    for expiry in sorted(by_expiry)[:max_expiries]:
        contracts = by_expiry[expiry]
        if underlying_price is not None:
            contracts = sorted(
                contracts,
                key=lambda r: abs((_to_float(r.get("strike_price") or r.get("strike")) or 0) - underlying_price),
            )
        contracts = contracts[:max_strikes]
        for row in contracts:
            entry: dict[str, Any] = {"expiry": expiry}
            strike = _to_float(row.get("strike_price") or row.get("strike"))
            if strike is not None:
                entry["strike"] = strike
            option_type = row.get("option_type") or row.get("type")
            if option_type:
                entry["type"] = str(option_type).lower()
            for source, target in (
                ("bid_price", "bid"),
                ("ask_price", "ask"),
                ("mark_price", "mark"),
                ("open_interest", "oi"),
                ("volume", "volume"),
            ):
                number = _to_float(row.get(source))
                if number is not None:
                    entry[target] = number
            if row.get("id") or row.get("option_id"):
                entry["option_id"] = str(row.get("option_id") or row.get("id"))
            out.append(entry)
    return out


def first_option_leg(args: dict[str, Any]) -> dict[str, Any]:
    """Robinhood `place_option_order` puts the contract on `legs`, not top-level `symbol`."""
    legs = args.get("legs")
    if isinstance(legs, list):
        for row in legs:
            if isinstance(row, dict):
                return row
    if isinstance(legs, dict):
        return legs
    return {}


def option_id_from_args(args: dict[str, Any]) -> str:
    leg = first_option_leg(args)
    raw = (
        args.get("option_id")
        or args.get("instrument_id")
        or leg.get("option_id")
        or leg.get("instrument_id")
        or leg.get("id")
        or leg.get("option")
        or ""
    )
    text = str(raw).strip()
    if "/" in text:
        text = text.rstrip("/").split("/")[-1]
    return text


def option_symbol_from_args(args: dict[str, Any]) -> str:
    leg = first_option_leg(args)
    return str(
        args.get("symbol")
        or args.get("chain_symbol")
        or args.get("underlying")
        or args.get("instrument")
        or leg.get("symbol")
        or leg.get("chain_symbol")
        or leg.get("underlying")
        or ""
    ).upper()


def option_side_from_args(args: dict[str, Any]) -> str:
    leg = first_option_leg(args)
    return str(
        args.get("side")
        or args.get("direction")
        or leg.get("side")
        or leg.get("direction")
        or "buy"
    ).lower()


def option_meta_from_args(args: dict[str, Any]) -> dict[str, Any]:
    leg = first_option_leg(args)
    raw_type = str(args.get("option_type") or leg.get("option_type") or "").lower()
    if raw_type not in ("call", "put"):
        cand = str(leg.get("type") or "").lower()
        raw_type = cand if cand in ("call", "put") else ""
    return {
        "option_id": option_id_from_args(args) or None,
        "expiry": str(
            args.get("expiration_date")
            or args.get("expiry")
            or leg.get("expiration_date")
            or leg.get("expiry")
            or ""
        )[:10]
        or None,
        "strike": _to_float(
            args.get("strike_price") or args.get("strike") or leg.get("strike_price") or leg.get("strike")
        ),
        "type": raw_type or None,
    }


def option_order_notional(args: dict[str, Any]) -> float | None:
    """Premium x 100 x contracts for a place_option_order argument payload."""
    qty = _to_float(args.get("quantity") or args.get("contracts") or args.get("qty"))
    price = _to_float(
        args.get("price")
        or args.get("limit_price")
        or args.get("premium")
        or args.get("ask_price")
    )
    if qty is None or price is None:
        return None
    return abs(qty) * price * OPTION_MULTIPLIER


def option_side_is_sell(args: dict[str, Any]) -> bool:
    side = option_side_from_args(args)
    if side in ("sell", "sell_to_open", "sell_to_close", "short"):
        return True
    leg = first_option_leg(args)
    position_effect = str(args.get("position_effect") or leg.get("position_effect") or "").lower()
    return side == "sell" or (position_effect == "open" and side.startswith("sell"))
