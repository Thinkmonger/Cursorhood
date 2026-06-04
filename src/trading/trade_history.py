from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from src.db.store import Store


def extract_mcp_data(call_result: dict[str, Any]) -> Any:
    """Parse JSON or SSE MCP tool responses into structured data."""
    if not call_result.get("ok"):
        return None
    direct = call_result.get("result")
    if direct is not None and not isinstance(direct, str):
        return direct
    raw = call_result.get("raw") or (direct if isinstance(direct, str) else "")
    if not raw:
        return None
    match = re.search(r"data: ({.*})", raw)
    if not match:
        return raw
    envelope = json.loads(match.group(1))
    if "error" in envelope:
        return {"error": envelope["error"]}
    content = (envelope.get("result") or {}).get("content") or []
    if content and content[0].get("type") == "text":
        text = content[0].get("text", "")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return envelope.get("result")


def agentic_account_number(accounts_payload: Any) -> str | None:
    if not isinstance(accounts_payload, dict):
        return None
    data = accounts_payload.get("data") or accounts_payload
    accounts = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(accounts, list):
        return None
    for account in accounts:
        if account.get("agentic_allowed"):
            number = account.get("account_number")
            if number:
                return str(number)
    for account in accounts:
        if account.get("is_default"):
            number = account.get("account_number")
            if number:
                return str(number)
    if accounts:
        number = accounts[0].get("account_number")
        return str(number) if number else None
    return None


def compact_broker_order(order: dict[str, Any]) -> dict[str, Any]:
    amount = order.get("dollar_based_amount") or {}
    return {
        "ts": order.get("last_transaction_at") or order.get("created_at"),
        "symbol": order.get("symbol"),
        "side": order.get("side"),
        "type": order.get("type"),
        "state": order.get("state"),
        "quantity": order.get("cumulative_quantity") or order.get("quantity"),
        "notional_usd": amount.get("amount"),
        "average_price": order.get("average_price"),
        "price": order.get("price"),
        "order_id": order.get("id"),
        "placed_agent": order.get("placed_agent"),
    }


def _note_symbol_activity(
    per_symbol: dict[str, dict[str, int]],
    symbol: str | None,
    side: str | None,
) -> None:
    if not symbol:
        return
    sym = symbol.upper()
    bucket = per_symbol[sym]
    side_l = (side or "").lower()
    if side_l == "buy":
        bucket["buys"] += 1
    elif side_l == "sell":
        bucket["sells"] += 1


def build_local_bot_history(bot_id: str) -> dict[str, Any]:
    store = Store()
    events: list[dict[str, Any]] = []

    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT e.ts, e.type, e.payload_json, r.id AS run_id, r.started_at
            FROM agent_events e
            JOIN agent_runs r ON r.id = e.run_id
            WHERE r.bot_id = ?
              AND e.type IN ('tool_call', 'status_log')
            ORDER BY e.id ASC
            """,
            (bot_id,),
        ).fetchall()

    for row in rows:
        payload = json.loads(row["payload_json"])
        entry: dict[str, Any] = {
            "ts": row["ts"],
            "run_id": row["run_id"],
            "type": row["type"],
        }
        if row["type"] == "status_log":
            entry.update(
                {
                    "action": payload.get("action"),
                    "symbols": payload.get("symbols") or [],
                    "reason": payload.get("reason"),
                    "portfolio_value": payload.get("portfolio_value"),
                }
            )
        elif row["type"] == "tool_call":
            entry["tool"] = payload.get("name")
            entry["input"] = payload.get("input") or {}
        events.append(entry)

    return {"events": events, "event_count": len(events)}


def build_trade_history(
    bot_id: str,
    broker_orders_payload: Any | None = None,
) -> dict[str, Any]:
    local = build_local_bot_history(bot_id)
    per_symbol: dict[str, dict[str, int]] = defaultdict(
        lambda: {"buys": 0, "sells": 0, "cancels": 0}
    )

    broker_orders: list[dict[str, Any]] = []
    if isinstance(broker_orders_payload, dict):
        orders = ((broker_orders_payload.get("data") or {}).get("orders")) or []
        if isinstance(orders, list):
            broker_orders = [compact_broker_order(o) for o in orders if isinstance(o, dict)]
            broker_orders.sort(key=lambda o: o.get("ts") or "")
            for order in broker_orders:
                side = order.get("side")
                symbol = order.get("symbol")
                state = str(order.get("state") or "").lower()
                if state == "cancelled":
                    if symbol:
                        per_symbol[symbol.upper()]["cancels"] += 1
                else:
                    _note_symbol_activity(per_symbol, symbol, side)

    return {
        "bot_id": bot_id,
        "broker_orders": broker_orders,
        "bot_activity": local["events"],
        "per_symbol": {k: dict(v) for k, v in per_symbol.items()},
        "totals": {
            "broker_orders": len(broker_orders),
            "bot_events": local["event_count"],
        },
    }
