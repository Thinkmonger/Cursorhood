"""Multi-asset paper positions with FIFO lot tracking and realized P&L."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

EQUITY = "equity"
OPTION = "option"
CRYPTO = "crypto"

MULTIPLIERS = {EQUITY: 1, OPTION: 100, CRYPTO: 1}

# Below this a position is treated as flat; crypto needs the tighter tolerance.
DUST = 1e-9


def multiplier_for(asset_class: str) -> int:
    return MULTIPLIERS.get(asset_class, 1)


def quantize(asset_class: str, qty: float) -> float:
    """Round a quantity to what the asset class can actually trade.

    Option contracts are indivisible; equities and crypto support fractions.
    """
    if asset_class == OPTION:
        return float(int(qty + DUST))
    return round(float(qty), 10)


def position_key(asset_class: str, symbol: str, meta: dict[str, Any] | None = None) -> str:
    """Stable identity for a position. Option contracts key on their full terms."""
    symbol = str(symbol).upper()
    if asset_class == OPTION:
        meta = meta or {}
        option_id = str(meta.get("option_id") or "").strip().upper()
        if option_id:
            return f"{OPTION}:{option_id}"
        expiry = str(meta.get("expiry") or "")[:10]
        strike = meta.get("strike")
        option_type = str(meta.get("type") or "").lower()
        if expiry and strike is not None and option_type:
            return f"{OPTION}:{symbol}|{expiry}|{strike}|{option_type}"
        return f"{OPTION}:{symbol}"
    return f"{asset_class}:{symbol}"


def new_position(asset_class: str, symbol: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    position: dict[str, Any] = {
        "asset_class": asset_class,
        "symbol": str(symbol).upper(),
        "qty": 0.0,
        "avg_cost": 0.0,
        "lots": [],
        "realized_pnl": 0.0,
        "multiplier": multiplier_for(asset_class),
    }
    if meta:
        position["meta"] = {k: v for k, v in meta.items() if v is not None}
    return position


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_lot(position: dict[str, Any], qty: float, price: float, ts: str | None = None) -> None:
    """Open or grow a long position at `price`."""
    if qty <= 0:
        return
    lots: list[dict[str, Any]] = position.setdefault("lots", [])
    lots.append({"qty": qty, "price": price, "ts": ts or _now()})
    position["qty"] = round(float(position.get("qty") or 0) + qty, 10)
    position["avg_cost"] = round(_weighted_avg(lots), 6)


def _weighted_avg(lots: list[dict[str, Any]]) -> float:
    total_qty = sum(float(lot.get("qty") or 0) for lot in lots)
    if total_qty <= 0:
        return 0.0
    cost = sum(float(lot.get("qty") or 0) * float(lot.get("price") or 0) for lot in lots)
    return cost / total_qty


def reduce_lots(position: dict[str, Any], qty: float, price: float) -> float:
    """Close `qty` units FIFO at `price`. Returns realized P&L in dollars."""
    if qty <= 0:
        return 0.0
    lots: list[dict[str, Any]] = position.setdefault("lots", [])
    multiplier = float(position.get("multiplier") or multiplier_for(position.get("asset_class", EQUITY)))
    remaining = qty
    realized = 0.0

    while remaining > DUST and lots:
        lot = lots[0]
        lot_qty = float(lot.get("qty") or 0)
        take = min(lot_qty, remaining)
        realized += (price - float(lot.get("price") or 0)) * take * multiplier
        remaining -= take
        if lot_qty - take <= DUST:
            lots.pop(0)
        else:
            lot["qty"] = lot_qty - take

    closed = qty - remaining
    position["qty"] = round(max(0.0, float(position.get("qty") or 0) - closed), 10)
    position["avg_cost"] = round(_weighted_avg(lots), 6) if lots else 0.0
    position["realized_pnl"] = round(float(position.get("realized_pnl") or 0) + realized, 6)
    return round(realized, 6)


def is_flat(position: dict[str, Any]) -> bool:
    return float(position.get("qty") or 0) <= DUST


def market_value(position: dict[str, Any], price: float | None) -> float:
    qty = float(position.get("qty") or 0)
    multiplier = float(position.get("multiplier") or 1)
    mark = price if price is not None else float(position.get("avg_cost") or 0)
    return qty * mark * multiplier


def cost_basis(position: dict[str, Any]) -> float:
    qty = float(position.get("qty") or 0)
    multiplier = float(position.get("multiplier") or 1)
    return qty * float(position.get("avg_cost") or 0) * multiplier


def display_symbol(position: dict[str, Any]) -> str:
    """Human-readable label, e.g. `AAPL 2026-10-16 150C`."""
    symbol = str(position.get("symbol") or "")
    if position.get("asset_class") != OPTION:
        return symbol
    meta = position.get("meta") or {}
    expiry = str(meta.get("expiry") or "")[:10]
    strike = meta.get("strike")
    letter = "C" if str(meta.get("type") or "").lower().startswith("c") else "P"
    strike_text = f"{float(strike):g}" if strike is not None else "?"
    return f"{symbol} {expiry} {strike_text}{letter}".strip()
