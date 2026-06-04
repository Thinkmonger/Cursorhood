"""Live portfolio and holdings metrics for bot overview."""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any

from src.auth.tokens import TokenStore
from src.db.store import Store
from src.setup.mcp_client import MCPClient
from src.trading.trade_history import agentic_account_number, extract_mcp_data

ACCOUNT_CACHE_TTL_SECONDS = 45
LIVE_FETCH_TIMEOUT_SECONDS = 12


_account_cache: dict[str, Any] = {"at": 0.0, "data": None}


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _portfolio_fields(payload: Any) -> dict[str, float | None]:
    data = (payload or {}).get("data") if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        data = {}
    return {
        "total_value": _as_float(data.get("total_value")),
        "equity_value": _as_float(data.get("equity_value")),
        "cash": _as_float(data.get("cash")),
        "buying_power": _as_float(data.get("buying_power")),
    }


def _position_rows(payload: Any) -> list[dict[str, Any]]:
    data = (payload or {}).get("data") if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        return []
    positions = data.get("positions") or []
    if not isinstance(positions, list):
        return []
    rows: list[dict[str, Any]] = []
    for pos in positions:
        if not isinstance(pos, dict):
            continue
        qty = _as_float(pos.get("quantity"))
        if not qty:
            continue
        rows.append(
            {
                "symbol": str(pos.get("symbol") or "").upper(),
                "quantity": qty,
                "average_buy_price": _as_float(pos.get("average_buy_price")),
                "type": pos.get("type"),
            }
        )
    return rows


def _quote_prices(payload: Any) -> dict[str, dict[str, float | None]]:
    data = (payload or {}).get("data") if isinstance(payload, dict) else {}
    results = (data or {}).get("results") if isinstance(data, dict) else []
    if not isinstance(results, list):
        return {}
    out: dict[str, dict[str, float | None]] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        quote = item.get("quote") or {}
        symbol = str(quote.get("symbol") or "").upper()
        if not symbol:
            continue
        last = _as_float(quote.get("last_trade_price"))
        prev = _as_float(quote.get("previous_close") or quote.get("adjusted_previous_close"))
        day_change_pct = None
        if last is not None and prev not in (None, 0):
            day_change_pct = round((last - prev) / prev * 100, 2)
        out[symbol] = {
            "last_price": last,
            "previous_close": prev,
            "day_change_pct": day_change_pct,
        }
    return out


def _build_holdings(
    positions: list[dict[str, Any]],
    quotes: dict[str, dict[str, float | None]],
) -> list[dict[str, Any]]:
    holdings: list[dict[str, Any]] = []
    for pos in positions:
        symbol = pos["symbol"]
        qty = pos["quantity"]
        avg_cost = pos.get("average_buy_price")
        quote = quotes.get(symbol) or {}
        last_price = quote.get("last_price")
        cost_basis = avg_cost * qty if avg_cost is not None else None
        market_value = last_price * qty if last_price is not None else cost_basis
        unrealized_pl = None
        unrealized_pl_pct = None
        if market_value is not None and cost_basis is not None:
            unrealized_pl = round(market_value - cost_basis, 4)
            if cost_basis:
                unrealized_pl_pct = round(unrealized_pl / cost_basis * 100, 2)
        holdings.append(
            {
                "symbol": symbol,
                "quantity": qty,
                "average_buy_price": avg_cost,
                "last_price": last_price,
                "market_value": round(market_value, 4) if market_value is not None else None,
                "cost_basis": round(cost_basis, 4) if cost_basis is not None else None,
                "unrealized_pl": unrealized_pl,
                "unrealized_pl_pct": unrealized_pl_pct,
                "day_change_pct": quote.get("day_change_pct"),
            }
        )
    holdings.sort(key=lambda h: h.get("market_value") or 0, reverse=True)
    return holdings


def _portfolio_change(series: list[dict[str, Any]]) -> dict[str, float | None]:
    if len(series) < 2:
        return {"start_value": None, "change_usd": None, "change_pct": None}
    start = _as_float(series[0].get("value"))
    end = _as_float(series[-1].get("value"))
    if start is None or end is None:
        return {"start_value": start, "change_usd": None, "change_pct": None}
    change = round(end - start, 4)
    change_pct = round(change / start * 100, 2) if start else None
    return {"start_value": start, "change_usd": change, "change_pct": change_pct}


def _snapshot_from_event_row(row: Any) -> dict[str, Any] | None:
    try:
        payload = json.loads(row["payload_json"])
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or not payload.get("ok"):
        return None
    return {"payload": payload, "as_of": row["ts"]}


def _latest_cached_snapshot(bot_id: str) -> dict[str, Any] | None:
    store = Store()
    with store.connect() as conn:
        row = conn.execute(
            """
            SELECT e.payload_json, e.ts
            FROM agent_events e
            JOIN agent_runs r ON r.id = e.run_id
            WHERE r.bot_id = ? AND e.type = 'portfolio_snapshot'
            ORDER BY e.id DESC
            LIMIT 1
            """,
            (bot_id,),
        ).fetchone()
    if not row:
        return None
    return _snapshot_from_event_row(row)


def _snapshot_is_paper(payload: dict[str, Any]) -> bool:
    sim_ctx = payload.get("simulation_context") or {}
    return sim_ctx.get("mode") == "simulation" and sim_ctx.get("uses_live_portfolio") is False


def _latest_compatible_cached_snapshot(bot_id: str) -> dict[str, Any] | None:
    """Reuse a cached snapshot only when it matches this bot's simulation mode."""
    from src.simulation.ledger import is_simulation_mode, uses_live_portfolio_in_simulation

    wants_paper = is_simulation_mode(bot_id) and not uses_live_portfolio_in_simulation(bot_id)
    store = Store()
    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT e.payload_json, e.ts, r.bot_id
            FROM agent_events e
            JOIN agent_runs r ON r.id = e.run_id
            WHERE e.type = 'portfolio_snapshot'
            ORDER BY e.id DESC
            LIMIT 25
            """
        ).fetchall()

    for row in rows:
        snap = _snapshot_from_event_row(row)
        if not snap:
            continue
        payload = snap["payload"]
        is_paper = _snapshot_is_paper(payload)
        source_bot = row["bot_id"]
        if wants_paper:
            if source_bot == bot_id and is_paper:
                return snap
            continue
        if is_paper:
            continue
        if source_bot != bot_id:
            continue
        return snap
    return None


async def _fetch_live_overview() -> dict[str, Any] | None:
    token = TokenStore().get_access_token()
    if not token:
        return None
    client = MCPClient(token=token)
    accounts = extract_mcp_data(await client.call_tool("get_accounts"))
    account_number = agentic_account_number(accounts)
    if not account_number:
        return None
    portfolio = extract_mcp_data(
        await client.call_tool("get_portfolio", {"account_number": account_number})
    )
    positions = extract_mcp_data(
        await client.call_tool("get_equity_positions", {"account_number": account_number})
    )
    position_rows = _position_rows(positions)
    symbols = [p["symbol"] for p in position_rows]
    quotes_payload = None
    if symbols:
        quotes_payload = extract_mcp_data(
            await client.call_tool("get_equity_quotes", {"symbols": symbols})
        )
    return {
        "portfolio": portfolio,
        "positions": positions,
        "quotes": quotes_payload,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


async def _get_account_snapshot(bot_id: str) -> tuple[str, str | None, Any, Any, Any]:
    now = time.time()
    cached = _account_cache.get("data")
    cache_age = now - float(_account_cache.get("at") or 0)
    if cached and cache_age < ACCOUNT_CACHE_TTL_SECONDS:
        return "live", cached["as_of"], cached["portfolio"], cached["positions"], cached["quotes"]

    try:
        live = await asyncio.wait_for(_fetch_live_overview(), timeout=LIVE_FETCH_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        live = None

    if live:
        _account_cache["at"] = now
        _account_cache["data"] = live
        return "live", live["as_of"], live["portfolio"], live["positions"], live["quotes"]

    stale = cached if cached else None
    if stale:
        return "live", stale["as_of"], stale["portfolio"], stale["positions"], stale["quotes"]

    for _name, resolver in (
        ("bot_cached", lambda: _latest_cached_snapshot(bot_id)),
        ("compatible_cached", lambda: _latest_compatible_cached_snapshot(bot_id)),
    ):
        snap = resolver()
        if snap:
            payload = snap["payload"]
            return (
                "cached",
                snap["as_of"],
                payload.get("portfolio"),
                payload.get("positions"),
                payload.get("quotes"),
            )

    return "unavailable", None, None, None, None


def enrich_bot_card_stats(
    bot_id: str,
    stats: dict[str, Any],
    quotes_payload: Any | None = None,
) -> dict[str, Any]:
    """Use paper ledger totals/series on main dashboard cards when sim mode is on."""
    from src.simulation.ledger import build_simulation_overview, is_simulation_mode

    if not is_simulation_mode(bot_id):
        return stats

    out = dict(stats)
    sim = build_simulation_overview(bot_id, quotes_payload)
    if sim and sim.get("portfolio"):
        out["managed_portfolio_value"] = sim["portfolio"].get("total_value")
        perf = sim.get("performance") or {}
        sim_series = perf.get("portfolio_series") or []
        if sim_series:
            summary = sim.get("summary") or {}
            enriched: list[dict[str, Any]] = []
            for point in sim_series:
                row = dict(point)
                if summary.get("invested_value") is not None:
                    row.setdefault("holdings_value", summary["invested_value"])
                if summary.get("total_unrealized_pl") is not None:
                    row.setdefault("unrealized_pl", summary["total_unrealized_pl"])
                enriched.append(row)
            out["portfolio_series"] = enriched
        if perf.get("trades_placed") is not None:
            out["trades_placed"] = perf["trades_placed"]
    elif out.get("latest_portfolio_value") is not None:
        out["managed_portfolio_value"] = out["latest_portfolio_value"]
    return out

async def build_portfolio_overview(bot_id: str, stats: dict[str, Any] | None = None) -> dict[str, Any]:
    from src.stats.service import bot_stats

    if stats is None:
        stats = bot_stats(bot_id)

    source, as_of, portfolio_payload, positions_payload, quotes_payload = await _get_account_snapshot(
        bot_id
    )

    portfolio = _portfolio_fields(portfolio_payload)
    position_rows = _position_rows(positions_payload)
    quotes = _quote_prices(quotes_payload)
    holdings = _build_holdings(position_rows, quotes)

    invested = sum(h["market_value"] or 0 for h in holdings)
    total_unrealized = sum(h["unrealized_pl"] or 0 for h in holdings if h["unrealized_pl"] is not None)
    total_value = portfolio.get("total_value")
    cash = portfolio.get("cash")
    cash_pct = round(cash / total_value * 100, 1) if cash is not None and total_value else None
    invested_pct = round(invested / total_value * 100, 1) if total_value else None

    series = stats.get("portfolio_series") or []
    change = _portfolio_change(series)

    from src.simulation.ledger import build_simulation_overview, is_simulation_mode

    simulation_mode = is_simulation_mode(bot_id)
    simulation = None
    if simulation_mode:
        simulation = build_simulation_overview(bot_id, quotes_payload)

    live_summary = {
        "position_count": len(holdings),
        "invested_value": round(invested, 2),
        "cash_pct": cash_pct,
        "invested_pct": invested_pct,
        "total_unrealized_pl": round(total_unrealized, 2) if holdings else None,
    }

    return {
        "ok": source != "unavailable" or bool(simulation),
        "source": source,
        "as_of": as_of,
        "simulation_mode": simulation_mode,
        "live": {
            "portfolio": portfolio,
            "holdings": holdings,
            "summary": live_summary,
        },
        "simulation": simulation,
        "portfolio": portfolio,
        "holdings": holdings,
        "summary": live_summary,
        "performance": {
            "total_runs": stats.get("total_runs", 0),
            "success_rate": stats.get("success_rate", 0),
            "trades_placed": stats.get("trades_placed", 0),
            "runs_today": stats.get("runs_today", 0),
            "latest_portfolio_value": stats.get("latest_portfolio_value"),
            "portfolio_change": change,
            "portfolio_series": series,
            "actions": stats.get("actions") or {},
            "last_trade_at": stats.get("last_trade_at"),
            "simulation_trades": (simulation or {}).get("performance", {}).get("trades_placed"),
            "simulation_series": (simulation or {}).get("performance", {}).get("portfolio_series"),
            "simulation_change": (simulation or {}).get("performance", {}).get("portfolio_change"),
        },
    }
