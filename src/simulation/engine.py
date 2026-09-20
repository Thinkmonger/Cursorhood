"""Paper broker engine: the single owner of paper order state and fills.

Callers submit *intents* (keyed by run and event id); the engine decides whether
an order rests, fills, or is rejected, and it is the only place that mutates
positions and cash. That keeps a single trade from filling twice when both the
pre-trade hook and the status-log handler see the same decision.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.db.store import Store
from src.settings.service import SettingsService
from src.simulation import accounting, positions as pos
from src.simulation.fills import (
    FillModel,
    execution_price,
    is_triggered,
    normalize_order_type,
)

logger = logging.getLogger(__name__)

LEDGER_KEY = "simulation_ledger"
SCHEMA_VERSION = 2

QUEUED = "queued"
FILLED = "filled"
PARTIALLY_FILLED = "partially_filled"
CANCELLED = "cancelled"
REJECTED = "rejected"

TERMINAL_STATES = frozenset({FILLED, CANCELLED, REJECTED})

MAX_ORDERS = 200
MAX_TRADES = 300
MAX_SERIES = 60
MAX_APPLIED_FILLS = 500


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class OrderIntent:
    """A trade the agent wants to make, before the broker decides anything."""

    symbol: str
    side: str
    asset_class: str = pos.EQUITY
    order_type: str = "market"
    qty: float | None = None
    notional: float | None = None
    limit_price: float | None = None
    stop_price: float | None = None
    reference_price: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    intent_key: str | None = None
    run_id: int | None = None
    ts: str | None = None

    def normalized(self) -> OrderIntent:
        self.symbol = str(self.symbol).upper()
        self.side = str(self.side).lower()
        self.order_type = normalize_order_type(self.order_type)
        self.ts = self.ts or _now()
        return self


@dataclass
class SubmitResult:
    ok: bool
    status: str
    order: dict[str, Any] | None = None
    reason: str | None = None
    duplicate: bool = False


def empty_ledger() -> dict[str, Any]:
    return {
        "version": SCHEMA_VERSION,
        "cash": 0.0,
        "settled_cash": 0.0,
        "pending_settlements": [],
        "positions": {},
        "orders": [],
        "trades": [],
        "series": [],
        "realized_pnl": 0.0,
        "applied_fills": [],
        "seeded": False,
    }


def load_ledger(bot_id: str) -> dict[str, Any]:
    raw = Store().get_bot_state(bot_id, LEDGER_KEY)
    if not raw:
        return empty_ledger()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return empty_ledger()
    if not isinstance(data, dict):
        return empty_ledger()
    if int(data.get("version") or 1) < SCHEMA_VERSION:
        from src.simulation.migration import upgrade_ledger

        data = upgrade_ledger(data)
        # Persist immediately, otherwise every read re-runs the upgrade.
        save_ledger(bot_id, data)
    return data


def save_ledger(bot_id: str, ledger: dict[str, Any]) -> None:
    ledger["version"] = SCHEMA_VERSION
    Store().set_bot_state(bot_id, LEDGER_KEY, json.dumps(ledger))


class PaperBroker:
    """Owns one bot's paper account."""

    def __init__(self, bot_id: str, ledger: dict[str, Any] | None = None) -> None:
        self.bot_id = bot_id
        self.ledger = ledger if ledger is not None else load_ledger(bot_id)
        self.fill_model = FillModel.for_bot(bot_id)
        self._limits: Any | None = None

    # ---- persistence -------------------------------------------------

    def save(self) -> dict[str, Any]:
        save_ledger(self.bot_id, self.ledger)
        return self.ledger

    @property
    def limits(self) -> Any:
        if self._limits is None:
            self._limits = SettingsService(self.bot_id).read_limits()
        return self._limits

    # ---- order lifecycle ---------------------------------------------

    def submit(self, intent: OrderIntent, quotes: Any | None = None) -> SubmitResult:
        """Submit an intent. Idempotent on `intent_key`."""
        intent = intent.normalized()

        if intent.intent_key and intent.intent_key in set(self.ledger.get("applied_fills") or []):
            return SubmitResult(True, "duplicate", duplicate=True)

        price = intent.reference_price or self._price_for(intent, quotes)
        if price is None or price <= 0:
            return self._reject(intent, "No paper quote available for the symbol")

        blocked = self._risk_block(intent, price)
        if blocked:
            return self._reject(intent, blocked)

        order = self._new_order(intent, price)
        if float(order["qty"]) <= pos.DUST:
            return self._reject(intent, "Order quantity rounds to zero for this asset class")
        self.ledger.setdefault("orders", []).append(order)
        self._mark_applied(intent.intent_key)

        if is_triggered(order, price):
            self._fill(order, price, intent.ts or _now())
        self._trim()
        return SubmitResult(True, order["status"], order=order)

    def evaluate_resting_orders(self, quotes: Any | None = None) -> list[dict[str, Any]]:
        """Re-check queued orders against fresh quotes. Returns the ones that filled."""
        filled: list[dict[str, Any]] = []
        for order in self.ledger.get("orders") or []:
            if order.get("status") not in (QUEUED, PARTIALLY_FILLED):
                continue
            price = self._quote_price(order.get("symbol", ""), order.get("asset_class"), quotes)
            if price is None or price <= 0:
                continue
            if not is_triggered(order, price):
                continue
            if self._fill(order, price, _now()):
                filled.append(order)
        if filled:
            self.append_series(_now(), quotes)
        return filled

    def cancel(self, *, order_id: str | None = None, symbol: str | None = None) -> list[dict[str, Any]]:
        """Cancel resting paper orders by id or symbol. Returns the cancelled ones."""
        target_symbol = str(symbol).upper() if symbol else None
        cancelled: list[dict[str, Any]] = []
        for order in self.ledger.get("orders") or []:
            if order.get("status") not in (QUEUED, PARTIALLY_FILLED):
                continue
            if order_id and order.get("id") != order_id:
                continue
            if target_symbol and str(order.get("symbol")).upper() != target_symbol:
                continue
            order["status"] = CANCELLED
            order["updated_ts"] = _now()
            cancelled.append(order)
        return cancelled

    def open_orders(self) -> list[dict[str, Any]]:
        return [
            o for o in self.ledger.get("orders") or []
            if o.get("status") in (QUEUED, PARTIALLY_FILLED)
        ]

    def has_order_for(self, run_id: int | None, symbol: str, side: str) -> bool:
        """Whether this run already produced a paper order for the symbol and side.

        The pre-trade hook submits intents from actual `place_*_order` calls; this
        stops the status-log handler from filling the same decision a second time.
        """
        if run_id is None:
            return False
        target = str(symbol).upper()
        wanted = str(side).lower()
        for order in self.ledger.get("orders") or []:
            if order.get("run_id") != run_id:
                continue
            if str(order.get("symbol")).upper() != target:
                continue
            if str(order.get("side") or "").lower().startswith(wanted):
                return True
        return False

    # ---- internals ----------------------------------------------------

    def _new_order(self, intent: OrderIntent, price: float) -> dict[str, Any]:
        qty = intent.qty
        if qty is None:
            notional = intent.notional or 0.0
            multiplier = pos.multiplier_for(intent.asset_class)
            qty = (notional / (price * multiplier)) if price > 0 else 0.0
        qty = pos.quantize(intent.asset_class, float(qty))
        return {
            "id": uuid.uuid4().hex[:12],
            "asset_class": intent.asset_class,
            "symbol": intent.symbol,
            "side": intent.side,
            "order_type": intent.order_type,
            "qty": round(float(qty), 10),
            "filled_qty": 0.0,
            "avg_fill_price": None,
            "limit_price": intent.limit_price,
            "stop_price": intent.stop_price,
            "status": QUEUED,
            "created_ts": intent.ts or _now(),
            "updated_ts": intent.ts or _now(),
            "meta": dict(intent.meta or {}),
            "intent_key": intent.intent_key,
            "run_id": intent.run_id,
        }

    def _reject(self, intent: OrderIntent, reason: str) -> SubmitResult:
        order = {
            "id": uuid.uuid4().hex[:12],
            "asset_class": intent.asset_class,
            "symbol": intent.symbol,
            "side": intent.side,
            "order_type": intent.order_type,
            "qty": intent.qty,
            "filled_qty": 0.0,
            "status": REJECTED,
            "reason": reason,
            "created_ts": intent.ts or _now(),
            "updated_ts": intent.ts or _now(),
            "intent_key": intent.intent_key,
            "run_id": intent.run_id,
        }
        self.ledger.setdefault("orders", []).append(order)
        self._mark_applied(intent.intent_key)
        self._trim()
        return SubmitResult(False, REJECTED, order=order, reason=reason)

    def _mark_applied(self, intent_key: str | None) -> None:
        if not intent_key:
            return
        applied = list(self.ledger.get("applied_fills") or [])
        if intent_key not in applied:
            applied.append(intent_key)
        self.ledger["applied_fills"] = applied[-MAX_APPLIED_FILLS:]

    def _risk_block(self, intent: OrderIntent, price: float) -> str | None:
        """Paper-side enforcement of the limits the live hook also applies."""
        limits = self.limits
        if intent.side == "buy":
            open_count = sum(1 for p in self.ledger.get("positions", {}).values() if not pos.is_flat(p))
            key = pos.position_key(intent.asset_class, intent.symbol, intent.meta)
            is_new = key not in self.ledger.get("positions", {})
            if is_new and open_count >= int(limits.max_open_positions):
                return f"Max open positions ({limits.max_open_positions}) reached in paper account"

        daily = accounting.realized_pnl_today(self.ledger)
        max_loss = float(limits.max_daily_loss_usd or 0)
        if max_loss and daily <= -max_loss:
            return f"Daily paper loss ${abs(daily):.2f} hit the ${max_loss:.2f} limit"
        return None

    def _fill(self, order: dict[str, Any], market_price: float, ts: str) -> bool:
        asset_class = str(order.get("asset_class") or pos.EQUITY)
        side = str(order.get("side") or "buy")
        price = execution_price(order, market_price, self.fill_model)
        if price <= 0:
            return False

        multiplier = pos.multiplier_for(asset_class)
        remaining = float(order.get("qty") or 0) - float(order.get("filled_qty") or 0)
        if remaining <= pos.DUST:
            order["status"] = FILLED
            return False

        commission = self.fill_model.commission(asset_class)
        key = pos.position_key(asset_class, str(order.get("symbol")), order.get("meta"))
        book: dict[str, Any] = self.ledger.setdefault("positions", {})
        realized = 0.0

        if side.startswith("buy"):
            cash_available = accounting.available_cash(self.ledger)
            cost_per_unit = price * multiplier
            qty = remaining
            cost = qty * cost_per_unit + commission
            if cost > cash_available + pos.DUST:
                # A cash account rejects an unfunded order rather than quietly
                # shrinking it, so the paper book does the same.
                order["status"] = REJECTED
                order["reason"] = (
                    f"Insufficient paper buying power: needs ${cost:,.2f}, "
                    f"available ${cash_available:,.2f}"
                )
                order["updated_ts"] = ts
                return False
            position = book.get(key) or pos.new_position(asset_class, str(order.get("symbol")), order.get("meta"))
            pos.add_lot(position, qty, price, ts)
            book[key] = position
            accounting.debit_cash(self.ledger, qty * cost_per_unit + commission)
        else:
            position = book.get(key)
            if not position or pos.is_flat(position):
                order["status"] = REJECTED
                order["reason"] = "No paper position to sell"
                order["updated_ts"] = ts
                return False
            qty = min(remaining, float(position.get("qty") or 0))
            realized = pos.reduce_lots(position, qty, price)
            proceeds = qty * price * multiplier - commission
            accounting.credit_proceeds(
                self.ledger,
                proceeds,
                days=accounting.settlement_days(self.bot_id),
            )
            self.ledger["realized_pnl"] = round(
                float(self.ledger.get("realized_pnl") or 0) + realized, 6
            )
            if pos.is_flat(position):
                book.pop(key, None)

        filled_qty = float(order.get("filled_qty") or 0) + qty
        order["filled_qty"] = round(filled_qty, 10)
        order["avg_fill_price"] = price
        order["status"] = FILLED if filled_qty >= float(order.get("qty") or 0) - pos.DUST else PARTIALLY_FILLED
        order["updated_ts"] = ts

        self.ledger.setdefault("trades", []).append(
            {
                "ts": ts,
                "action": "buy" if side.startswith("buy") else "sell",
                "asset_class": asset_class,
                "symbol": str(order.get("symbol")),
                "qty": round(qty, 10),
                "price": price,
                "notional": round(qty * price * multiplier, 4),
                "commission": round(commission, 4),
                "realized_pnl": realized,
                "order_id": order.get("id"),
            }
        )
        return True

    def _price_for(self, intent: OrderIntent, quotes: Any | None) -> float | None:
        return self._quote_price(intent.symbol, intent.asset_class, quotes)

    def _quote_price(self, symbol: str, asset_class: Any, quotes: Any | None) -> float | None:
        from src.simulation.quotes import resolve_price

        return resolve_price(symbol, str(asset_class or pos.EQUITY), quotes)

    def append_series(self, ts: str, quotes: Any | None) -> None:
        total = self.total_value(quotes)
        series = self.ledger.setdefault("series", [])
        series.append({"ts": ts, "value": round(total, 4)})
        self.ledger["series"] = series[-MAX_SERIES:]

    def total_value(self, quotes: Any | None = None) -> float:
        total = float(self.ledger.get("cash") or 0)
        for position in (self.ledger.get("positions") or {}).values():
            price = self._quote_price(
                str(position.get("symbol")), position.get("asset_class"), quotes
            )
            total += pos.market_value(position, price)
        return total

    def _trim(self) -> None:
        orders = self.ledger.get("orders") or []
        if len(orders) > MAX_ORDERS:
            resting = [o for o in orders if o.get("status") not in TERMINAL_STATES]
            terminal = [o for o in orders if o.get("status") in TERMINAL_STATES]
            self.ledger["orders"] = resting + terminal[-(MAX_ORDERS - len(resting)) :]
        trades = self.ledger.get("trades") or []
        if len(trades) > MAX_TRADES:
            self.ledger["trades"] = trades[-MAX_TRADES:]
