"""Per-bot paper portfolio for simulation mode.

Thin facade over the ``src.simulation`` package. The :class:`PaperBroker` in
``engine.py`` owns all fills; everything here is snapshot shaping, seeding, and
the status-log bridge that existing callers already import.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.db.store import Store
from src.settings.service import SettingsService
from src.simulation import accounting, positions as pos
from src.simulation.engine import (
    LEDGER_KEY,
    OrderIntent,
    PaperBroker,
    empty_ledger,
    load_ledger,
    save_ledger,
)
from src.simulation.quotes import merge_quotes, resolve_price, resolve_price_and_quotes

_CASH_UNSET = object()

__all__ = [
    "LEDGER_KEY",
    "OrderIntent",
    "PaperBroker",
    "apply_simulation_snapshot_policy",
    "apply_status_log",
    "build_simulation_overview",
    "cancel_paper_orders",
    "get_ledger",
    "is_simulation_mode",
    "record_paper_order",
    "replay_bot_history",
    "reset_ledger_for_starting_cash",
    "reset_simulation",
    "save_ledger",
    "seed_ledger_from_live",
    "submit_paper_order",
    "uses_live_portfolio_in_simulation",
]


def is_simulation_mode(bot_id: str) -> bool:
    return SettingsService(bot_id).read_bot_app().simulation_mode


def uses_live_portfolio_in_simulation(bot_id: str) -> bool:
    app = SettingsService(bot_id).read_bot_app()
    return bool(app.simulation_mode and app.simulation_include_live_portfolio)


def get_ledger(bot_id: str) -> dict[str, Any]:
    return load_ledger(bot_id)


def reset_ledger_for_starting_cash(bot_id: str) -> None:
    save_ledger(bot_id, empty_ledger())


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _quote_price(quotes_payload: Any, symbol: str) -> float | None:
    from src.simulation.quotes import equity_price

    return equity_price(quotes_payload, symbol)


# Kept for callers that still want the merged-quotes tuple form.
_resolve_quote_price = resolve_price_and_quotes
_merge_quotes_payload = merge_quotes


# ---------------------------------------------------------------- snapshots


def _ledger_as_mcp_snapshot(
    ledger: dict[str, Any],
    quotes_payload: Any | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Shape the paper ledger like a Robinhood portfolio/positions payload."""
    cash = float(ledger.get("cash") or 0)
    invested = 0.0
    position_rows: list[dict[str, Any]] = []
    for position in _sorted_positions(ledger):
        asset_class = str(position.get("asset_class") or pos.EQUITY)
        symbol = str(position.get("symbol"))
        price = resolve_price(symbol, asset_class, quotes_payload)
        invested += pos.market_value(position, price)
        if asset_class != pos.EQUITY:
            continue
        position_rows.append(
            {
                "symbol": symbol,
                "quantity": float(position.get("qty") or 0),
                "average_buy_price": float(position.get("avg_cost") or 0),
                "type": "equity",
            }
        )
    portfolio = {
        "data": {
            "cash": round(cash, 4),
            "total_value": round(cash + invested, 4),
            "equity_value": round(invested, 4),
        }
    }
    return portfolio, {"data": {"positions": position_rows}}


def _sorted_positions(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    book = ledger.get("positions") or {}
    return [book[key] for key in sorted(book) if isinstance(book[key], dict)]


def _filter_trade_history_for_simulation(trade_history: dict[str, Any]) -> dict[str, Any]:
    from collections import defaultdict

    bot_activity = trade_history.get("bot_activity") or []
    per_symbol: dict[str, dict[str, int]] = defaultdict(
        lambda: {"buys": 0, "sells": 0, "cancels": 0}
    )
    for ev in bot_activity:
        if ev.get("type") != "status_log":
            continue
        action = str(ev.get("action", "")).lower()
        for sym in ev.get("symbols") or []:
            symbol = str(sym).upper()
            if action == "buy":
                per_symbol[symbol]["buys"] += 1
            elif action == "sell":
                per_symbol[symbol]["sells"] += 1

    return {
        **trade_history,
        "broker_orders": [],
        "per_symbol": {k: dict(v) for k, v in per_symbol.items()},
        "totals": {
            "broker_orders": 0,
            "bot_events": (trade_history.get("totals") or {}).get("bot_events", len(bot_activity)),
        },
        "simulation_note": "Paper trading — live broker orders are hidden; use bot_activity only.",
    }


def apply_simulation_snapshot_policy(bot_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Swap live portfolio/positions for the paper ledger when sim excludes live holdings."""
    if not is_simulation_mode(bot_id) or not snapshot.get("ok"):
        return snapshot

    if uses_live_portfolio_in_simulation(bot_id):
        snapshot["simulation_context"] = {"mode": "simulation", "uses_live_portfolio": True}
        return snapshot

    replay_bot_history(bot_id)
    ledger = get_ledger(bot_id)
    if not ledger.get("seeded"):
        seed_ledger_from_live(
            bot_id,
            snapshot.get("portfolio"),
            snapshot.get("positions"),
            snapshot.get("quotes"),
        )
        ledger = get_ledger(bot_id)

    quotes = snapshot.get("quotes")
    broker = PaperBroker(bot_id, ledger)
    filled = broker.evaluate_resting_orders(quotes)
    if filled:
        ledger = broker.save()

    portfolio, positions = _ledger_as_mcp_snapshot(ledger, quotes)
    out = {
        **snapshot,
        "portfolio": portfolio,
        "positions": positions,
        "simulation_context": {
            "mode": "simulation",
            "uses_live_portfolio": False,
            "paper_cash": ledger.get("cash"),
            "paper_position_count": len((positions.get("data") or {}).get("positions") or []),
            "paper_open_orders": len(broker.open_orders()),
            "paper_realized_pnl": round(float(ledger.get("realized_pnl") or 0), 2),
        },
    }
    option_rows = _positions_for_class(ledger, pos.OPTION, quotes)
    if option_rows:
        out["option_positions"] = option_rows
    crypto_rows = _positions_for_class(ledger, pos.CRYPTO, quotes)
    if crypto_rows:
        out["crypto_positions"] = crypto_rows
    if snapshot.get("trade_history"):
        out["trade_history"] = _filter_trade_history_for_simulation(snapshot["trade_history"])
    return out


def _positions_for_class(
    ledger: dict[str, Any],
    asset_class: str,
    quotes: Any | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for position in _sorted_positions(ledger):
        if str(position.get("asset_class")) != asset_class:
            continue
        symbol = str(position.get("symbol"))
        price = resolve_price(symbol, asset_class, quotes)
        meta = position.get("meta") or {}
        row: dict[str, Any] = {
            "symbol": symbol,
            "qty": float(position.get("qty") or 0),
            "avg_cost": float(position.get("avg_cost") or 0),
        }
        if asset_class == pos.CRYPTO:
            row["pair"] = symbol
        for key in ("expiry", "strike", "type"):
            if meta.get(key) is not None:
                row[key] = meta[key]
        if price is not None:
            row["mark"] = price
            row["pnl"] = round(
                pos.market_value(position, price) - pos.cost_basis(position), 2
            )
        rows.append(row)
    return rows


# ------------------------------------------------------------------ seeding


def _build_seeded_ledger(
    bot_id: str,
    portfolio_payload: Any,
    positions_payload: Any = None,
    quotes_payload: Any | None = None,
    *,
    configured_cash_override: float | None | object = _CASH_UNSET,
) -> dict[str, Any]:
    from src.stats.portfolio import _portfolio_fields, _position_rows

    app = SettingsService(bot_id).read_bot_app()
    if configured_cash_override is not _CASH_UNSET:
        configured_cash = (
            None if configured_cash_override is None else _as_float(configured_cash_override)
        )
    else:
        configured_cash = _as_float(app.simulated_cash_starting_value)

    book: dict[str, Any] = {}
    if configured_cash is not None and configured_cash > 0:
        cash = configured_cash
    else:
        portfolio = _portfolio_fields(portfolio_payload)
        cash = _as_float(portfolio.get("cash"))
        if cash is None or cash <= 0:
            cash = _as_float(portfolio.get("total_value")) or 100.0
        if uses_live_portfolio_in_simulation(bot_id):
            for row in _position_rows(positions_payload):
                qty = row["quantity"]
                avg = row.get("average_buy_price")
                if not qty or not avg:
                    continue
                position = pos.new_position(pos.EQUITY, row["symbol"])
                pos.add_lot(position, float(qty), float(avg))
                book[pos.position_key(pos.EQUITY, row["symbol"])] = position

    ts = datetime.now(timezone.utc).isoformat()
    ledger = empty_ledger()
    ledger.update(
        {
            "cash": round(cash, 4),
            "settled_cash": round(cash, 4),
            "positions": book,
            "seeded": True,
            "seeded_at": ts,
            "starting_cash": round(cash, 4),
        }
    )
    PaperBroker(bot_id, ledger).append_series(ts, quotes_payload)
    return ledger


def seed_ledger_from_live(
    bot_id: str,
    portfolio_payload: Any,
    positions_payload: Any = None,
    quotes_payload: Any | None = None,
) -> dict[str, Any]:
    ledger = get_ledger(bot_id)
    if ledger.get("seeded"):
        return ledger
    ledger = _build_seeded_ledger(bot_id, portfolio_payload, positions_payload, quotes_payload)
    save_ledger(bot_id, ledger)
    return ledger


async def reset_simulation(
    bot_id: str,
    *,
    configured_cash_override: float | None | object = _CASH_UNSET,
) -> dict[str, Any]:
    """Clear the paper ledger, wipe run history, and re-seed from config or live account."""
    if not is_simulation_mode(bot_id):
        raise ValueError("Simulation mode is not enabled for this bot")

    store = Store()
    deleted_runs = store.delete_bot_runs(bot_id)
    store.set_bot_state(bot_id, "scheduler_max_runs_reached", "false")
    reset_ledger_for_starting_cash(bot_id)

    from src.stats.portfolio import _get_account_snapshot

    _, _, portfolio_payload, positions_payload, quotes_payload = await _get_account_snapshot(bot_id)
    ledger = _build_seeded_ledger(
        bot_id,
        portfolio_payload,
        positions_payload,
        quotes_payload,
        configured_cash_override=configured_cash_override,
    )
    save_ledger(bot_id, ledger)

    return {
        "deleted_runs": deleted_runs,
        "starting_cash": ledger.get("starting_cash"),
        "position_count": len(ledger.get("positions") or {}),
    }


# -------------------------------------------------------------- order entry


def submit_paper_order(
    bot_id: str,
    intent: OrderIntent,
    quotes_payload: Any | None = None,
) -> dict[str, Any]:
    """Submit an order intent to the paper broker. The one place fills happen."""
    broker = PaperBroker(bot_id)
    result = broker.submit(intent, quotes_payload)
    if not result.duplicate:
        broker.append_series(intent.ts or datetime.now(timezone.utc).isoformat(), quotes_payload)
        broker.save()
    return {
        "ok": result.ok,
        "status": result.status,
        "reason": result.reason,
        "duplicate": result.duplicate,
        "order": result.order,
    }


def cancel_paper_orders(
    bot_id: str,
    *,
    order_id: str | None = None,
    symbol: str | None = None,
) -> list[dict[str, Any]]:
    broker = PaperBroker(bot_id)
    cancelled = broker.cancel(order_id=order_id, symbol=symbol)
    if cancelled:
        broker.save()
    return cancelled


def record_paper_order(
    bot_id: str,
    *,
    symbol: str,
    side: str,
    notional: float,
    price: float | None,
    ts: str | None = None,
    quotes_payload: Any | None = None,
    asset_class: str = pos.EQUITY,
    meta: dict[str, Any] | None = None,
    intent_key: str | None = None,
    run_id: int | None = None,
    order_type: str = "market",
    limit_price: float | None = None,
    stop_price: float | None = None,
    quantity: float | None = None,
) -> dict[str, Any]:
    """Backward-compatible market-order entry point, now routed through the engine."""
    limits = SettingsService(bot_id).read_limits()
    capped = min(max(float(notional or 0), 0), float(limits.max_order_notional_usd))
    intent = OrderIntent(
        symbol=symbol,
        side=side,
        asset_class=asset_class,
        order_type=order_type,
        qty=quantity,
        notional=capped if quantity is None else None,
        limit_price=limit_price,
        stop_price=stop_price,
        reference_price=price,
        meta=meta or {},
        intent_key=intent_key,
        run_id=run_id,
        ts=ts,
    )
    submit_paper_order(bot_id, intent, quotes_payload)
    return get_ledger(bot_id)


# --------------------------------------------------------- status-log bridge


def _snapshot_from_run(store: Store, run_id: int) -> dict[str, Any] | None:
    for ev in store.get_events(run_id):
        if ev.get("type") != "portfolio_snapshot":
            continue
        payload = ev.get("payload") or {}
        if isinstance(payload, dict) and payload.get("ok"):
            return payload
    return None


def _fill_key(event_id: int | None, symbol: str, action: str) -> str | None:
    if event_id is None:
        return None
    return f"{event_id}:{symbol.upper()}:{action.lower()}"


def apply_status_log(
    bot_id: str,
    run_id: int | None,
    data: dict[str, Any],
    ts: str | None = None,
    *,
    event_id: int | None = None,
) -> None:
    """Fill from a logged decision, unless the hook already submitted that order."""
    if not is_simulation_mode(bot_id):
        return

    action = str(data.get("action", "none")).lower()
    if action not in ("buy", "sell"):
        return
    symbols = data.get("symbols") or []
    if not symbols:
        return

    store = Store()
    snapshot = _snapshot_from_run(store, run_id) if run_id else None
    quotes = snapshot.get("quotes") if snapshot else None

    if not get_ledger(bot_id).get("seeded") and snapshot and snapshot.get("portfolio"):
        seed_ledger_from_live(
            bot_id, snapshot["portfolio"], snapshot.get("positions"), quotes
        )

    limits = SettingsService(bot_id).read_limits()
    notional = _as_float(data.get("notional") or data.get("order_notional"))
    if notional is None:
        notional = float(limits.max_order_notional_usd)
    ts = ts or datetime.now(timezone.utc).isoformat()

    for symbol in symbols:
        sym = str(symbol).upper()
        broker = PaperBroker(bot_id)
        if broker.has_order_for(run_id, sym, action):
            continue
        price, quotes = resolve_price_and_quotes(sym, quotes)
        if price is None:
            continue
        intent = OrderIntent(
            symbol=sym,
            side=action,
            asset_class=pos.EQUITY,
            notional=min(float(notional), float(limits.max_order_notional_usd)),
            reference_price=price,
            intent_key=_fill_key(event_id, sym, action),
            run_id=run_id,
            ts=ts,
        )
        submit_paper_order(bot_id, intent, quotes)


def replay_bot_history(bot_id: str) -> None:
    """Apply any status_log buy/sell decisions not yet recorded in the paper ledger."""
    if not is_simulation_mode(bot_id):
        return

    store = Store()
    for run in reversed(store.get_runs(limit=500, bot_id=bot_id)):
        run_id = int(run["id"])
        snapshot = _snapshot_from_run(store, run_id)
        if snapshot and not get_ledger(bot_id).get("seeded"):
            seed_ledger_from_live(
                bot_id,
                snapshot.get("portfolio"),
                snapshot.get("positions"),
                snapshot.get("quotes"),
            )
        for ev in store.get_events(run_id):
            if ev.get("type") != "status_log":
                continue
            apply_status_log(
                bot_id,
                run_id,
                ev.get("payload") or {},
                ts=ev.get("ts"),
                event_id=int(ev["id"]),
            )


# ------------------------------------------------------------------ reports


def build_simulation_overview(
    bot_id: str,
    quotes_payload: Any | None = None,
) -> dict[str, Any] | None:
    if not is_simulation_mode(bot_id):
        return None

    replay_bot_history(bot_id)
    ledger = get_ledger(bot_id)
    if not ledger.get("seeded"):
        return None

    equity_symbols = [
        str(p.get("symbol"))
        for p in _sorted_positions(ledger)
        if str(p.get("asset_class")) == pos.EQUITY
    ]
    missing = [s for s in equity_symbols if _quote_price(quotes_payload, s) is None]
    if missing:
        try:
            from src.setup.mcp_client import fetch_equity_quotes_sync

            quotes_payload = merge_quotes(quotes_payload, fetch_equity_quotes_sync(missing))
        except Exception:
            pass

    broker = PaperBroker(bot_id, ledger)
    holdings: list[dict[str, Any]] = []
    invested = 0.0
    total_unrealized = 0.0

    for position in _sorted_positions(ledger):
        asset_class = str(position.get("asset_class") or pos.EQUITY)
        symbol = str(position.get("symbol"))
        qty = float(position.get("qty") or 0)
        avg_cost = float(position.get("avg_cost") or 0)
        last_price = resolve_price(symbol, asset_class, quotes_payload) or avg_cost
        value = pos.market_value(position, last_price)
        basis = pos.cost_basis(position)
        unrealized = value - basis if basis else None
        if unrealized is not None:
            total_unrealized += unrealized
        invested += value
        holdings.append(
            {
                "symbol": pos.display_symbol(position),
                "asset_class": asset_class,
                "quantity": qty,
                "average_buy_price": avg_cost,
                "last_price": last_price,
                "market_value": round(value, 4),
                "cost_basis": round(basis, 4),
                "unrealized_pl": round(unrealized, 4) if unrealized is not None else None,
                "unrealized_pl_pct": round(unrealized / basis * 100, 2)
                if unrealized is not None and basis
                else None,
                "realized_pl": round(float(position.get("realized_pnl") or 0), 2),
                "day_change_pct": None,
            }
        )

    cash = float(ledger.get("cash") or 0)
    total_value = cash + invested
    series = ledger.get("series") or []
    from src.stats.portfolio import _portfolio_change

    realized = round(float(ledger.get("realized_pnl") or 0), 2)
    return {
        "ok": True,
        "source": "simulation",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "portfolio": {
            "total_value": round(total_value, 4),
            "equity_value": round(invested, 4),
            "cash": round(cash, 4),
            "unsettled_cash": accounting.unsettled_cash(ledger),
        },
        "holdings": holdings,
        "summary": {
            "position_count": len(holdings),
            "invested_value": round(invested, 2),
            "cash_pct": round(cash / total_value * 100, 1) if total_value else None,
            "invested_pct": round(invested / total_value * 100, 1) if total_value else None,
            "total_unrealized_pl": round(total_unrealized, 2) if holdings else None,
            "total_realized_pl": realized,
        },
        "paper": {
            "open_orders": broker.open_orders(),
            "recent_fills": (ledger.get("trades") or [])[-20:],
            "realized_pnl": realized,
            "realized_pnl_today": accounting.realized_pnl_today(ledger),
        },
        "performance": {
            "portfolio_series": series[-30:],
            "portfolio_change": _portfolio_change(series),
            "trades_placed": len(ledger.get("trades") or []),
        },
    }
