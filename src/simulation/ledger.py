"""Per-bot paper portfolio for simulation mode."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from src.db.store import Store, bot_state_key
from src.settings.service import SettingsService

LEDGER_KEY = "simulation_ledger"
_CASH_UNSET = object()


def is_simulation_mode(bot_id: str) -> bool:
    return SettingsService(bot_id).read_bot_app().simulation_mode


def uses_live_portfolio_in_simulation(bot_id: str) -> bool:
    app = SettingsService(bot_id).read_bot_app()
    return bool(app.simulation_mode and app.simulation_include_live_portfolio)


def _ledger_as_mcp_snapshot(
    ledger: dict[str, Any],
    quotes_payload: Any | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    cash = float(ledger.get("cash") or 0)
    invested = 0.0
    position_rows: list[dict[str, Any]] = []
    for symbol, pos in sorted((ledger.get("positions") or {}).items()):
        qty = float(pos.get("qty") or 0)
        avg = float(pos.get("avg_cost") or 0)
        price = _quote_price(quotes_payload, symbol) or avg
        market_value = qty * price if price else qty * avg
        invested += market_value
        position_rows.append(
            {
                "symbol": symbol,
                "quantity": qty,
                "average_buy_price": avg,
                "type": "equity",
            }
        )
    total_value = cash + invested
    portfolio = {
        "data": {
            "cash": round(cash, 4),
            "total_value": round(total_value, 4),
            "equity_value": round(invested, 4),
        }
    }
    positions = {"data": {"positions": position_rows}}
    return portfolio, positions


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
    """Swap live portfolio/positions for paper ledger when sim excludes live holdings."""
    if not is_simulation_mode(bot_id) or not snapshot.get("ok"):
        return snapshot

    include_live = uses_live_portfolio_in_simulation(bot_id)
    if include_live:
        snapshot["simulation_context"] = {
            "mode": "simulation",
            "uses_live_portfolio": True,
        }
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

    portfolio, positions = _ledger_as_mcp_snapshot(ledger, snapshot.get("quotes"))
    sim_position_count = len((positions.get("data") or {}).get("positions") or [])

    out = {
        **snapshot,
        "portfolio": portfolio,
        "positions": positions,
        "simulation_context": {
            "mode": "simulation",
            "uses_live_portfolio": False,
            "paper_cash": ledger.get("cash"),
            "paper_position_count": sim_position_count,
        },
    }
    if snapshot.get("trade_history"):
        out["trade_history"] = _filter_trade_history_for_simulation(snapshot["trade_history"])

    return out


def _empty_ledger() -> dict[str, Any]:
    return {
        "cash": 0.0,
        "positions": {},
        "trades": [],
        "series": [],
        "seeded": False,
    }


def get_ledger(bot_id: str) -> dict[str, Any]:
    store = Store()
    raw = store.get_bot_state(bot_id, LEDGER_KEY)
    if not raw:
        return _empty_ledger()
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else _empty_ledger()
    except (json.JSONDecodeError, TypeError):
        return _empty_ledger()


def save_ledger(bot_id: str, ledger: dict[str, Any]) -> None:
    Store().set_bot_state(bot_id, LEDGER_KEY, json.dumps(ledger))


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _quote_price(quotes_payload: Any, symbol: str) -> float | None:
    from src.stats.portfolio import _quote_prices

    prices = _quote_prices(quotes_payload)
    row = prices.get(symbol.upper()) or {}
    return _as_float(row.get("last_price"))


def _merge_quotes_payload(existing: Any | None, extra: Any | None) -> Any | None:
    if not extra:
        return existing
    if not existing:
        return extra
    if not isinstance(existing, dict) or not isinstance(extra, dict):
        return extra or existing
    existing_data = existing.get("data") if isinstance(existing.get("data"), dict) else {}
    extra_data = extra.get("data") if isinstance(extra.get("data"), dict) else {}
    merged_results: list[Any] = []
    seen: set[str] = set()
    for item in (extra_data.get("results") or []) + (existing_data.get("results") or []):
        if not isinstance(item, dict):
            continue
        quote = item.get("quote") or {}
        sym = str(quote.get("symbol") or "").upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        merged_results.append(item)
    return {"data": {**existing_data, **extra_data, "results": merged_results}}


def _resolve_quote_price(
    symbol: str,
    quotes_payload: Any | None,
    *,
    fetch_if_missing: bool = True,
) -> tuple[float | None, Any | None]:
    price = _quote_price(quotes_payload, symbol)
    if price is not None or not fetch_if_missing:
        return price, quotes_payload
    fetched = None
    try:
        from src.setup.mcp_client import fetch_equity_quotes_sync

        fetched = fetch_equity_quotes_sync([symbol])
    except Exception:
        fetched = None
    if not fetched:
        return None, quotes_payload
    merged = _merge_quotes_payload(quotes_payload, fetched)
    return _quote_price(merged, symbol), merged


def reset_ledger_for_starting_cash(bot_id: str) -> None:
    save_ledger(bot_id, _empty_ledger())


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
    saved_cash = _as_float(app.simulated_cash_starting_value)
    include_live_positions = uses_live_portfolio_in_simulation(bot_id)
    if configured_cash_override is not _CASH_UNSET:
        configured_cash = (
            None if configured_cash_override is None else _as_float(configured_cash_override)
        )
    else:
        configured_cash = saved_cash

    if configured_cash is not None and configured_cash > 0:
        cash = configured_cash
        positions: dict[str, Any] = {}
    else:
        portfolio = _portfolio_fields(portfolio_payload)
        cash = _as_float(portfolio.get("cash"))
        live_total = _as_float(portfolio.get("total_value"))
        if cash is None or cash <= 0:
            cash = live_total or 100.0

        positions = {}
        if include_live_positions:
            for row in _position_rows(positions_payload):
                symbol = row["symbol"]
                qty = row["quantity"]
                avg = row.get("average_buy_price")
                if qty and avg:
                    positions[symbol] = {"qty": qty, "avg_cost": avg}

    ts = datetime.now(timezone.utc).isoformat()
    ledger = {
        "cash": round(cash, 4),
        "positions": positions,
        "trades": [],
        "series": [],
        "seeded": True,
        "seeded_at": ts,
        "starting_cash": round(cash, 4),
    }
    _append_series_point(ledger, ts, quotes_payload)
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
    """Clear paper ledger, wipe run history, and re-seed from config or live account."""
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


def _append_series_point(
    ledger: dict[str, Any],
    ts: str,
    quotes_payload: Any | None,
) -> None:
    total = _ledger_total_value(ledger, quotes_payload)
    ledger.setdefault("series", []).append({"ts": ts, "value": round(total, 4)})
    ledger["series"] = ledger["series"][-60:]


def _ledger_total_value(ledger: dict[str, Any], quotes_payload: Any | None) -> float:
    cash = float(ledger.get("cash") or 0)
    total = cash
    for symbol, pos in (ledger.get("positions") or {}).items():
        qty = float(pos.get("qty") or 0)
        avg = float(pos.get("avg_cost") or 0)
        price = _quote_price(quotes_payload, symbol) or avg
        total += qty * price
    return total


def record_paper_order(
    bot_id: str,
    *,
    symbol: str,
    side: str,
    notional: float,
    price: float | None,
    ts: str | None = None,
    quotes_payload: Any | None = None,
) -> dict[str, Any]:
    symbol = symbol.upper()
    side = side.lower()
    ts = ts or datetime.now(timezone.utc).isoformat()
    if not price or price <= 0:
        return get_ledger(bot_id)

    ledger = get_ledger(bot_id)
    limits = SettingsService(bot_id).read_limits()
    max_order = float(limits.max_order_notional_usd)
    notional = min(max(notional, 0), max_order)

    positions = ledger.setdefault("positions", {})
    cash = float(ledger.get("cash") or 0)

    if side == "buy":
        spend = min(notional, cash)
        if spend <= 0:
            return ledger
        qty = spend / price
        pos = positions.get(symbol, {"qty": 0.0, "avg_cost": price})
        old_qty = float(pos.get("qty") or 0)
        old_avg = float(pos.get("avg_cost") or price)
        new_qty = old_qty + qty
        new_avg = ((old_qty * old_avg) + (qty * price)) / new_qty if new_qty else price
        positions[symbol] = {"qty": new_qty, "avg_cost": round(new_avg, 6)}
        cash -= spend
        ledger["trades"].append(
            {
                "ts": ts,
                "action": "buy",
                "symbol": symbol,
                "qty": round(qty, 6),
                "price": price,
                "notional": round(spend, 4),
            }
        )
    elif side == "sell":
        pos = positions.get(symbol)
        if not pos:
            return ledger
        qty = float(pos.get("qty") or 0)
        if qty <= 0:
            return ledger
        sell_qty = qty if notional <= 0 else min(qty, notional / price)
        proceeds = sell_qty * price
        cash += proceeds
        remaining = qty - sell_qty
        if remaining <= 1e-8:
            positions.pop(symbol, None)
        else:
            positions[symbol] = {"qty": remaining, "avg_cost": float(pos.get("avg_cost") or price)}
        ledger["trades"].append(
            {
                "ts": ts,
                "action": "sell",
                "symbol": symbol,
                "qty": round(sell_qty, 6),
                "price": price,
                "notional": round(proceeds, 4),
            }
        )

    ledger["cash"] = round(cash, 4)
    ledger["trades"] = ledger["trades"][-200:]
    _append_series_point(ledger, ts, quotes_payload)
    save_ledger(bot_id, ledger)
    return ledger


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


def _bootstrap_applied_fills(bot_id: str) -> None:
    """Match existing paper trades to status_log events so replay stays idempotent."""
    ledger = get_ledger(bot_id)
    if ledger.get("applied_fills") is not None:
        return

    trades = ledger.get("trades") or []
    if not trades:
        ledger["applied_fills"] = []
        save_ledger(bot_id, ledger)
        return

    applied: set[str] = set()
    used_trade_idx: set[int] = set()
    store = Store()
    runs = store.get_runs(limit=500, bot_id=bot_id)
    for run in reversed(runs):
        for ev in store.get_events(int(run["id"])):
            if ev.get("type") != "status_log":
                continue
            payload = ev.get("payload") or {}
            action = str(payload.get("action", "none")).lower()
            if action not in ("buy", "sell"):
                continue
            for sym in payload.get("symbols") or []:
                sym_u = str(sym).upper()
                key = _fill_key(int(ev["id"]), sym_u, action)
                if not key:
                    continue
                for i, trade in enumerate(trades):
                    if i in used_trade_idx:
                        continue
                    if trade.get("symbol") == sym_u and trade.get("action") == action:
                        applied.add(key)
                        used_trade_idx.add(i)
                        break

    ledger["applied_fills"] = sorted(applied)
    save_ledger(bot_id, ledger)


def apply_status_log(
    bot_id: str,
    run_id: int | None,
    data: dict[str, Any],
    ts: str | None = None,
    *,
    event_id: int | None = None,
) -> None:
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
    portfolio = snapshot.get("portfolio") if snapshot else None

    ledger = get_ledger(bot_id)
    if not ledger.get("seeded") and portfolio:
        seed_ledger_from_live(bot_id, portfolio, snapshot.get("positions"), quotes)
        ledger = get_ledger(bot_id)

    _bootstrap_applied_fills(bot_id)
    applied = set(get_ledger(bot_id).get("applied_fills") or [])

    limits = SettingsService(bot_id).read_limits()
    notional = _as_float(data.get("notional") or data.get("order_notional"))
    if notional is None:
        notional = float(limits.max_order_notional_usd)
    ts = ts or datetime.now(timezone.utc).isoformat()

    missing_quotes = [
        str(symbol).upper()
        for symbol in symbols
        if _quote_price(quotes, str(symbol).upper()) is None
    ]
    if missing_quotes:
        try:
            from src.setup.mcp_client import fetch_equity_quotes_sync

            fetched = fetch_equity_quotes_sync(missing_quotes)
            quotes = _merge_quotes_payload(quotes, fetched)
        except Exception:
            pass

    for symbol in symbols:
        sym = str(symbol).upper()
        fill_key = _fill_key(event_id, sym, action)
        if fill_key and fill_key in applied:
            continue
        price, quotes = _resolve_quote_price(sym, quotes)
        if price is None:
            continue
        record_paper_order(
            bot_id,
            symbol=sym,
            side=action,
            notional=float(notional or limits.max_order_notional_usd),
            price=price,
            ts=ts,
            quotes_payload=quotes,
        )
        if fill_key:
            applied.add(fill_key)
            ledger = get_ledger(bot_id)
            ledger["applied_fills"] = sorted(applied)[-500:]
            save_ledger(bot_id, ledger)


def replay_bot_history(bot_id: str) -> None:
    """Apply any status_log buy/sell fills not yet recorded in the paper ledger."""
    if not is_simulation_mode(bot_id):
        return

    _bootstrap_applied_fills(bot_id)
    store = Store()
    runs = store.get_runs(limit=500, bot_id=bot_id)
    for run in reversed(runs):
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
            if ev.get("type") == "status_log":
                payload = ev.get("payload") or {}
                apply_status_log(
                    bot_id,
                    run_id,
                    payload,
                    ts=ev.get("ts"),
                    event_id=int(ev["id"]),
                )


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

    paper_symbols = list((ledger.get("positions") or {}).keys())
    if paper_symbols:
        missing = [s for s in paper_symbols if _quote_price(quotes_payload, s) is None]
        if missing:
            try:
                from src.setup.mcp_client import fetch_equity_quotes_sync

                fetched = fetch_equity_quotes_sync(missing)
                quotes_payload = _merge_quotes_payload(quotes_payload, fetched)
            except Exception:
                pass

    holdings: list[dict[str, Any]] = []
    invested = 0.0
    total_unrealized = 0.0

    for symbol, pos in sorted((ledger.get("positions") or {}).items()):
        qty = float(pos.get("qty") or 0)
        avg_cost = float(pos.get("avg_cost") or 0)
        last_price = _quote_price(quotes_payload, symbol) or avg_cost
        market_value = qty * last_price if last_price else qty * avg_cost
        cost_basis = qty * avg_cost
        unrealized = market_value - cost_basis if cost_basis else None
        if unrealized is not None:
            total_unrealized += unrealized
        invested += market_value
        holdings.append(
            {
                "symbol": symbol,
                "quantity": qty,
                "average_buy_price": avg_cost,
                "last_price": last_price,
                "market_value": round(market_value, 4),
                "cost_basis": round(cost_basis, 4),
                "unrealized_pl": round(unrealized, 4) if unrealized is not None else None,
                "unrealized_pl_pct": round(unrealized / cost_basis * 100, 2)
                if unrealized is not None and cost_basis
                else None,
                "day_change_pct": None,
            }
        )

    cash = float(ledger.get("cash") or 0)
    total_value = cash + invested
    cash_pct = round(cash / total_value * 100, 1) if total_value else None
    invested_pct = round(invested / total_value * 100, 1) if total_value else None

    series = ledger.get("series") or []
    from src.stats.portfolio import _portfolio_change

    return {
        "ok": True,
        "source": "simulation",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "portfolio": {
            "total_value": round(total_value, 4),
            "equity_value": round(invested, 4),
            "cash": round(cash, 4),
        },
        "holdings": holdings,
        "summary": {
            "position_count": len(holdings),
            "invested_value": round(invested, 2),
            "cash_pct": cash_pct,
            "invested_pct": invested_pct,
            "total_unrealized_pl": round(total_unrealized, 2) if holdings else None,
        },
        "performance": {
            "portfolio_series": series[-30:],
            "portfolio_change": _portfolio_change(series),
            "trades_placed": len(ledger.get("trades") or []),
        },
    }
