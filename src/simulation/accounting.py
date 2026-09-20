"""Cash, buying power, and T+N settlement for the paper broker.

Robinhood's agentic accounts are cash accounts, so sale proceeds are not
immediately reusable. ``simulation_settlement_days`` of 0 keeps the old
instant-settlement behavior.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any


def _today(now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).date()


def settlement_days(bot_id: str) -> int:
    try:
        from src.settings.service import SettingsService

        return int(SettingsService(bot_id).read_bot_app().simulation_settlement_days)
    except Exception:
        return 0


def settle_due(ledger: dict[str, Any], now: datetime | None = None) -> float:
    """Move matured proceeds into settled cash. Returns the amount settled."""
    pending = ledger.get("pending_settlements") or []
    if not pending:
        return 0.0
    today = _today(now)
    settled = 0.0
    still_pending: list[dict[str, Any]] = []
    for entry in pending:
        try:
            settle_on = date.fromisoformat(str(entry.get("settle_on"))[:10])
        except (TypeError, ValueError):
            settle_on = today
        if settle_on <= today:
            settled += float(entry.get("amount") or 0)
        else:
            still_pending.append(entry)
    ledger["pending_settlements"] = still_pending
    if settled:
        ledger["settled_cash"] = round(float(ledger.get("settled_cash") or 0) + settled, 6)
    return round(settled, 6)


def credit_proceeds(
    ledger: dict[str, Any],
    amount: float,
    *,
    days: int = 0,
    now: datetime | None = None,
) -> None:
    """Add sale proceeds, unsettled for `days` business-ish days."""
    if amount <= 0:
        return
    ledger["cash"] = round(float(ledger.get("cash") or 0) + amount, 6)
    if days <= 0:
        ledger["settled_cash"] = round(float(ledger.get("settled_cash") or 0) + amount, 6)
        return
    settle_on = _next_business_day(_today(now), days)
    ledger.setdefault("pending_settlements", []).append(
        {"settle_on": settle_on.isoformat(), "amount": round(amount, 6)}
    )


def debit_cash(ledger: dict[str, Any], amount: float) -> None:
    if amount <= 0:
        return
    ledger["cash"] = round(float(ledger.get("cash") or 0) - amount, 6)
    ledger["settled_cash"] = round(max(0.0, float(ledger.get("settled_cash") or 0) - amount), 6)


def available_cash(ledger: dict[str, Any], now: datetime | None = None) -> float:
    """Buying power: settled cash only, after maturing anything that came due."""
    settle_due(ledger, now)
    if not ledger.get("pending_settlements") and ledger.get("settled_cash") is None:
        return float(ledger.get("cash") or 0)
    settled = ledger.get("settled_cash")
    if settled is None:
        return float(ledger.get("cash") or 0)
    return max(0.0, float(settled))


def unsettled_cash(ledger: dict[str, Any]) -> float:
    return round(sum(float(e.get("amount") or 0) for e in ledger.get("pending_settlements") or []), 2)


def _next_business_day(start: date, days: int) -> date:
    current = start
    remaining = days
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


def realized_pnl_today(ledger: dict[str, Any], now: datetime | None = None) -> float:
    """Today's realized P&L, used to enforce `max_daily_loss_usd` in paper mode."""
    today = _today(now).isoformat()
    total = 0.0
    for trade in ledger.get("trades") or []:
        ts = str(trade.get("ts") or "")
        if ts[:10] != today:
            continue
        total += float(trade.get("realized_pnl") or 0) - float(trade.get("commission") or 0)
    return round(total, 4)
