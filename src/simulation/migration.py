"""One-time upgrade of the v1 paper ledger JSON into the multi-asset schema."""

from __future__ import annotations

import logging
from typing import Any

from src.simulation import positions as pos

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2


def upgrade_ledger(old: dict[str, Any]) -> dict[str, Any]:
    """Convert a v1 ledger (flat equity positions, no orders) to v2 in place.

    v1 kept ``positions`` as ``{"AAPL": {"qty", "avg_cost"}}`` with no lots, no
    orders, and no realized P&L. Existing holdings become a single synthetic lot
    at their average cost, which preserves unrealized P&L exactly and makes
    future FIFO closes behave sensibly.
    """
    cash = float(old.get("cash") or 0)
    new: dict[str, Any] = {
        "version": SCHEMA_VERSION,
        "cash": round(cash, 6),
        "settled_cash": round(cash, 6),
        "pending_settlements": [],
        "positions": _upgrade_positions(old.get("positions")),
        "orders": [],
        "trades": _upgrade_trades(old.get("trades")),
        "series": list(old.get("series") or []),
        "realized_pnl": 0.0,
        "applied_fills": list(old.get("applied_fills") or []),
        "seeded": bool(old.get("seeded")),
        "migrated_from": 1,
    }
    for key in ("seeded_at", "starting_cash"):
        if old.get(key) is not None:
            new[key] = old[key]
    logger.info(
        "Upgraded paper ledger to v%s (%s positions, %s trades)",
        SCHEMA_VERSION,
        len(new["positions"]),
        len(new["trades"]),
    )
    return new


def _upgrade_positions(raw: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not isinstance(raw, dict):
        return out
    for symbol, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        qty = float(entry.get("qty") or 0)
        if qty <= 0:
            continue
        avg_cost = _first_float(entry, "avg_cost", "avg_price", "average_buy_price", "price")
        if avg_cost is None or avg_cost <= 0:
            # A zero-cost lot would read as pure profit the moment it is sold.
            logger.warning("Dropping paper position %s during upgrade: no cost basis", symbol)
            continue
        position = pos.new_position(pos.EQUITY, str(symbol))
        pos.add_lot(position, qty, avg_cost, entry.get("ts"))
        out[pos.position_key(pos.EQUITY, str(symbol))] = position
    return out


def _first_float(entry: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = entry.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _upgrade_trades(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for trade in raw:
        if not isinstance(trade, dict):
            continue
        out.append(
            {
                **trade,
                "asset_class": trade.get("asset_class") or pos.EQUITY,
                "commission": float(trade.get("commission") or 0),
                "realized_pnl": float(trade.get("realized_pnl") or 0),
            }
        )
    return out
