"""Fill model for the paper broker: spread-aware pricing, slippage, and commissions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MARKET = "market"
LIMIT = "limit"
STOP = "stop"
STOP_LIMIT = "stop_limit"

ORDER_TYPES = frozenset({MARKET, LIMIT, STOP, STOP_LIMIT})


@dataclass(frozen=True)
class FillModel:
    """Configurable execution assumptions. Defaults reproduce frictionless fills."""

    slippage_bps: float = 0.0
    commission_per_order: float = 0.0

    @classmethod
    def for_bot(cls, bot_id: str) -> FillModel:
        try:
            from src.settings.service import SettingsService

            app = SettingsService(bot_id).read_bot_app()
            return cls(
                slippage_bps=float(app.simulation_slippage_bps),
                commission_per_order=float(app.simulation_commission_per_order),
            )
        except Exception:
            return cls()

    def fill_price(
        self,
        side: str,
        reference_price: float,
        *,
        bid: float | None = None,
        ask: float | None = None,
    ) -> float:
        """Cross the spread when quoted, otherwise apply slippage to the reference."""
        buying = str(side).lower().startswith("buy")
        if buying and ask and ask > 0:
            return round(ask, 6)
        if not buying and bid and bid > 0:
            return round(bid, 6)
        if not self.slippage_bps:
            return round(reference_price, 6)
        drift = reference_price * (self.slippage_bps / 10_000.0)
        return round(reference_price + drift if buying else reference_price - drift, 6)

    def commission(self, asset_class: str) -> float:
        return float(self.commission_per_order)


def normalize_order_type(value: Any) -> str:
    text = str(value or MARKET).strip().lower().replace("-", "_").replace(" ", "_")
    if text in ("stoploss", "stop_loss"):
        return STOP
    if text in ("stoplimit",):
        return STOP_LIMIT
    return text if text in ORDER_TYPES else MARKET


def is_triggered(order: dict[str, Any], price: float) -> bool:
    """Whether a resting order should execute against the current price."""
    order_type = normalize_order_type(order.get("order_type"))
    buying = str(order.get("side") or "").lower().startswith("buy")
    limit_price = _to_float(order.get("limit_price"))
    stop_price = _to_float(order.get("stop_price"))

    if order_type == MARKET:
        return True
    if order_type == LIMIT:
        if limit_price is None:
            return True
        return price <= limit_price if buying else price >= limit_price
    if order_type == STOP:
        if stop_price is None:
            return True
        return price >= stop_price if buying else price <= stop_price
    if order_type == STOP_LIMIT:
        if stop_price is not None:
            stop_hit = price >= stop_price if buying else price <= stop_price
            if not stop_hit:
                return False
        if limit_price is None:
            return True
        return price <= limit_price if buying else price >= limit_price
    return True


def execution_price(order: dict[str, Any], market_price: float, model: FillModel) -> float:
    """Price a triggered order fills at, honoring the limit as a cap/floor."""
    order_type = normalize_order_type(order.get("order_type"))
    side = str(order.get("side") or "buy")
    price = model.fill_price(side, market_price)
    limit_price = _to_float(order.get("limit_price"))
    if limit_price is None or order_type not in (LIMIT, STOP_LIMIT):
        return price
    buying = side.lower().startswith("buy")
    return round(min(price, limit_price) if buying else max(price, limit_price), 6)


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
