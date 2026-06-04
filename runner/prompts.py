from __future__ import annotations

import json
import re
from typing import Any

from src.db.migrate import DEFAULT_BOT_ID
from src.db.store import Store
from src.settings.service import SettingsService

TRADING_TOOLS = frozenset({
    "get_accounts",
    "get_portfolio",
    "get_equity_positions",
    "get_equity_quotes",
    "get_equity_orders",
    "get_equity_tradability",
    "review_equity_order",
    "place_equity_order",
    "cancel_equity_order",
    "search",
})

_TERMINAL_ORDER_STATES = frozenset({
    "filled",
    "cancelled",
    "canceled",
    "rejected",
    "failed",
    "voided",
    "expired",
})


def _json_compact(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def _essential_limits(limits: Any) -> dict[str, Any]:
    return {
        "allowed_symbols": limits.allowed_symbols,
        "max_open_positions": limits.max_open_positions,
        "max_order_notional_usd": limits.max_order_notional_usd,
        "max_daily_loss_usd": limits.max_daily_loss_usd,
        "market_hours_only": limits.market_hours_only,
        "min_seconds_between_orders": limits.min_seconds_between_orders,
    }


def _holdings_for_prompt(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    from src.stats.portfolio import _build_holdings, _position_rows, _quote_prices

    positions = _position_rows(snapshot.get("positions"))
    quotes = _quote_prices(snapshot.get("quotes"))
    rows: list[dict[str, Any]] = []
    for item in _build_holdings(positions, quotes):
        row: dict[str, Any] = {
            "symbol": item["symbol"],
            "qty": item["quantity"],
        }
        if item.get("average_buy_price") is not None:
            row["avg_cost"] = item["average_buy_price"]
        if item.get("last_price") is not None:
            row["price"] = item["last_price"]
        if item.get("unrealized_pl_pct") is not None:
            row["pnl_pct"] = item["unrealized_pl_pct"]
        rows.append(row)
    return rows


def _technicals_for_prompt(bars_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(bars_payload, dict):
        return {}
    out: dict[str, Any] = {}
    for symbol, entry in (bars_payload.get("symbols") or {}).items():
        if not isinstance(entry, dict):
            continue
        if entry.get("error"):
            out[str(symbol).upper()] = {"error": entry["error"]}
            continue
        bars = entry.get("bars") or []
        last_bar = bars[-1] if bars else {}
        last_1h = last_bar.get("close")
        open_1h = last_bar.get("open")
        sma_20 = entry.get("sma_20")
        row: dict[str, Any] = {}
        if open_1h is not None:
            row["open_1h"] = open_1h
        if last_1h is not None:
            row["last_1h"] = last_1h
        if open_1h is not None and last_1h is not None:
            row["down_candle_1h"] = float(last_1h) < float(open_1h)
        if sma_20 is not None:
            row["sma_20_1h"] = sma_20
        if last_1h is not None and sma_20 is not None:
            row["below_sma_20"] = float(last_1h) < float(sma_20)
        out[str(symbol).upper()] = row
    return out


def _daily_technicals_for_prompt(bars_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(bars_payload, dict):
        return {}
    out: dict[str, Any] = {}
    for symbol, entry in (bars_payload.get("symbols") or {}).items():
        if not isinstance(entry, dict):
            continue
        if entry.get("error"):
            out[str(symbol).upper()] = {"error": entry["error"]}
            continue
        bars = entry.get("bars") or []
        last_bar = bars[-1] if bars else {}
        open_1d = last_bar.get("open")
        close_1d = last_bar.get("close")
        row: dict[str, Any] = {}
        if open_1d is not None:
            row["open_1d"] = open_1d
        if close_1d is not None:
            row["close_1d"] = close_1d
        if open_1d is not None and close_1d is not None:
            row["up_candle_1d"] = float(close_1d) > float(open_1d)
        out[str(symbol).upper()] = row
    return out


def _last_bot_action(trade_history: dict[str, Any]) -> dict[str, Any] | None:
    for event in reversed(trade_history.get("bot_activity") or []):
        if event.get("type") == "status_log" or event.get("action"):
            action = str(event.get("action") or "none").lower()
            symbols = event.get("symbols") or []
            row: dict[str, Any] = {"action": action, "symbols": symbols}
            if event.get("ts"):
                row["ts"] = event["ts"]
            return row
    return None


def _open_orders_for_prompt(trade_history: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for order in trade_history.get("broker_orders") or []:
        if not isinstance(order, dict):
            continue
        state = str(order.get("state") or "").lower()
        if state in _TERMINAL_ORDER_STATES:
            continue
        row: dict[str, Any] = {
            "symbol": order.get("symbol"),
            "side": order.get("side"),
            "type": order.get("type"),
            "state": order.get("state"),
        }
        if order.get("price") is not None:
            row["price"] = order.get("price")
        if order.get("quantity") is not None:
            row["qty"] = order.get("quantity")
        if order.get("ts"):
            row["ts"] = order["ts"]
        rows.append(row)
    return rows


def _minimal_decision_context(
    snapshot: dict[str, Any],
    allowed_symbols: list[str] | None = None,
) -> dict[str, Any]:
    from src.stats.portfolio import _portfolio_fields, _quote_prices

    context: dict[str, Any] = {}

    portfolio = _portfolio_fields(snapshot.get("portfolio"))
    if any(value is not None for value in portfolio.values()):
        context["portfolio"] = {k: v for k, v in portfolio.items() if v is not None}

    holdings = _holdings_for_prompt(snapshot)
    if holdings:
        context["holdings"] = holdings

    held_symbols = {row["symbol"] for row in holdings}
    quotes = _quote_prices(snapshot.get("quotes"))
    if quotes:
        quote_out: dict[str, Any] = {}
        for symbol, payload in quotes.items():
            row: dict[str, Any] = {}
            if symbol not in held_symbols and payload.get("last_price") is not None:
                row["last_price"] = payload["last_price"]
            if payload.get("day_change_pct") is not None:
                row["day_change_pct"] = payload["day_change_pct"]
            if row:
                quote_out[symbol] = row
        if quote_out:
            context["quotes"] = quote_out

    technicals = _technicals_for_prompt(snapshot.get("historical_bars_1h"))
    if technicals:
        context["technicals_1h"] = technicals

    daily_technicals = _daily_technicals_for_prompt(snapshot.get("historical_bars_1d"))
    if daily_technicals:
        context["technicals_1d"] = daily_technicals

    relevant_symbols = {s.upper() for s in (allowed_symbols or [])} | held_symbols

    trade_history = snapshot.get("trade_history")
    if isinstance(trade_history, dict):
        activity: dict[str, Any] = {}
        per_symbol = trade_history.get("per_symbol")
        if per_symbol and relevant_symbols:
            per_symbol = {k: v for k, v in per_symbol.items() if str(k).upper() in relevant_symbols}
        if per_symbol:
            activity["per_symbol"] = per_symbol
        last = _last_bot_action(trade_history)
        if last:
            activity["last"] = last
        if activity:
            context["activity"] = activity

        open_orders = _open_orders_for_prompt(trade_history)
        if open_orders:
            context["open_orders"] = open_orders

    sim_ctx = snapshot.get("simulation_context")
    if isinstance(sim_ctx, dict) and sim_ctx.get("mode") == "simulation":
        context["simulation"] = {
            "paper": sim_ctx.get("uses_live_portfolio") is False,
        }

    return context


def build_cycle_prompt(
    bot_id: str = DEFAULT_BOT_ID,
    trading_snapshot: dict[str, Any] | None = None,
) -> str:
    settings = SettingsService(bot_id)
    strategy = settings.read_strategy().strip()
    limits = settings.read_limits()
    app = settings.read_bot_app()

    from src.trading.profile_gate import get_profile_gate_status

    gate = get_profile_gate_status(bot_id=bot_id)

    parts: list[str] = [
        f"# Cycle {bot_id}",
        "",
        "## Rules",
        (
            "Trading agent only. Use ONLY robinhood-trading and robinhood-status MCP "
            "tools — no shell/file/code tools (they hang the run). Decide from Context "
            "plus MCP results; if needed data is missing, prefer no action rather than "
            "computing it yourself. Finish fast: read Context, optionally call trading "
            "tools, then log_event once."
        ),
        "",
        "## Strategy",
        strategy,
        "",
        "## Limits",
        _json_compact(_essential_limits(limits)),
    ]

    if trading_snapshot:
        context = _minimal_decision_context(trading_snapshot, allowed_symbols=limits.allowed_symbols)
        if context:
            parts.extend(["", "## Context", _json_compact(context)])

    if app.simulation_mode:
        parts.append(
            "SIM: no place/cancel; log_event required."
            if not app.simulation_include_live_portfolio
            else "SIM: no place/cancel; log_event required; live portfolio visible."
        )

    if gate.get("active"):
        parts.append("BLOCK: investor profile incomplete — no orders; log_event none.")

    parts.append(
        "Apply strategy to Context. review_equity_order before place_equity_order. "
        "log_event once. Reply ≤2 sentences."
    )

    return "\n".join(parts)


def _run_bot_id(store: Store, run_id: int) -> str:
    run = store.get_run(run_id)
    return run.get("bot_id", DEFAULT_BOT_ID) if run else DEFAULT_BOT_ID


def _emit_tool_event(run_id: int, name: str, payload: dict[str, Any]) -> None:
    from src.api.ws import emit_agent_event

    store = Store()
    emit_agent_event(run_id, _run_bot_id(store, run_id), name, payload)


def _extract_tool_call(message: Any) -> dict[str, Any] | None:
    if isinstance(message, dict):
        tool_name = message.get("name") or message.get("tool_name")
        if tool_name:
            return {
                "name": tool_name,
                "input": message.get("input") or message.get("arguments") or {},
            }
    tool_name = getattr(message, "name", None) or getattr(message, "tool_name", None)
    if tool_name:
        return {
            "name": tool_name,
            "input": getattr(message, "input", None)
            or getattr(message, "arguments", None)
            or {},
        }
    return None


def _maybe_emit_tool_call(run_id: int, name: str, input_data: Any) -> None:
    if not name:
        return
    _emit_tool_event(run_id, "tool_call", {"name": name, "input": input_data or {}})


def parse_sdk_message(run_id: int, message: Any) -> None:
    msg_type = getattr(message, "type", None) or (
        message.get("type") if isinstance(message, dict) else None
    )
    if msg_type in ("thinking", "status", "assistant_text"):
        return

    if msg_type == "tool_call":
        tool = _extract_tool_call(message)
        if tool and tool["name"] == "log_event":
            _apply_log_event_tool(run_id, tool.get("input") or {})
            return
        if tool and tool["name"] in TRADING_TOOLS:
            _emit_tool_event(run_id, "tool_call", tool)
        return

    if msg_type == "tool_result":
        return

    if hasattr(message, "message"):
        inner = message.message
        content = getattr(inner, "content", None)
        if content:
            for block in content:
                btype = getattr(block, "type", None)
                if btype == "tool_use":
                    tool_name = getattr(block, "name", "")
                    if tool_name == "log_event":
                        _apply_log_event_tool(
                            run_id,
                            getattr(block, "input", None) or {},
                        )
                    elif tool_name in TRADING_TOOLS:
                        _maybe_emit_tool_call(
                            run_id,
                            tool_name,
                            getattr(block, "input", {}),
                        )
            return


def _parse_log_event_input(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        if "summary_json" in raw:
            try:
                parsed = json.loads(str(raw["summary_json"]))
                return parsed if isinstance(parsed, dict) else None
            except (json.JSONDecodeError, TypeError):
                return raw if raw.get("action") else None
        return raw if raw.get("action") else None
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def _apply_log_event_tool(run_id: int, tool_input: Any) -> None:
    data = _parse_log_event_input(tool_input)
    if not data:
        return
    store = Store()
    bot_id = _run_bot_id(store, run_id)
    from src.api.ws import emit_agent_event
    from src.simulation.ledger import apply_status_log, is_simulation_mode

    event_id = emit_agent_event(run_id, bot_id, "status_log", data)
    if is_simulation_mode(bot_id):
        apply_status_log(bot_id, run_id, data, event_id=event_id)


def _infer_status_log_from_summary(text: str) -> dict[str, Any] | None:
    """Best-effort parse when the agent writes prose but skips log_event."""
    if not text:
        return None

    head = text.split("\n\n---\n\n")[0].strip()
    if head.startswith("{"):
        try:
            data = json.loads(head)
            if isinstance(data, dict) and str(data.get("action", "none")).lower() in (
                "buy",
                "sell",
                "none",
            ):
                return data
        except (json.JSONDecodeError, TypeError):
            pass

    action_match = re.search(r"\*\*Action:\*\*(.+?)(?:\n\n|\Z)", text, re.I | re.S)
    action_section = action_match.group(1) if action_match else text
    section_lower = action_section.lower()

    buy_match = re.search(
        r"(?:simulated|intended(?:\s+paper)?|paper)\s+(?:\*\*)?buy(?:\*\*)?\s+([A-Z]{1,5})\b",
        action_section,
        re.I,
    )
    if not buy_match:
        buy_match = re.search(r"(?:\*\*)?buy(?:\*\*)?\s+([A-Z]{1,5})\b", action_section, re.I)

    sell_match = re.search(
        r"(?:simulated|intended(?:\s+paper)?|paper)\s+(?:\*\*)?sell(?:\*\*)?\s+([A-Z]{1,5})\b",
        action_section,
        re.I,
    )
    if not sell_match:
        sell_match = re.search(r"(?:\*\*)?sell(?:\*\*)?\s+([A-Z]{1,5})\b", action_section, re.I)

    action = "none"
    symbols: list[str] = []
    if buy_match and not sell_match:
        action = "buy"
        symbols = [buy_match.group(1).upper()]
    elif sell_match and not buy_match:
        action = "sell"
        symbols = [sell_match.group(1).upper()]
    elif re.search(r"\b(no action|held flat|action:\s*none)\b", section_lower):
        action = "none"
    else:
        return None

    notional = None
    for pattern in (
        r"~\$\s*(\d+(?:\.\d+)?)\s*,?\s*~?\s*\d+\s*%",
        r"\$\s*(\d+(?:\.\d+)?)\s*\(\s*~?\s*\d+\s*%\s*of",
        r"for\s+\*\*\$\s*(\d+(?:\.\d+)?)\*\*",
    ):
        nm = re.search(pattern, action_section, re.I)
        if nm:
            notional = float(nm.group(1))
            break

    portfolio_value = None
    pm = re.search(r"\*\*Portfolio:\*\*\s*~?\$([\d,.]+)", text, re.I)
    if pm:
        portfolio_value = float(pm.group(1).replace(",", ""))

    return {
        "action": action,
        "symbols": symbols,
        "reason": action_section.strip()[:500],
        "portfolio_value": portfolio_value,
        "notional": notional,
        "inferred_from_summary": True,
    }


def _ensure_status_log_from_summary(
    store: Store,
    run_id: int,
    bot_id: str,
    summary_text: str | None,
) -> bool:
    if _status_log_summary(store, run_id):
        return False

    from src.simulation.ledger import apply_status_log, is_simulation_mode

    if not is_simulation_mode(bot_id):
        return False

    inferred = _infer_status_log_from_summary(summary_text or "")
    if not inferred:
        return False

    action = str(inferred.get("action", "none")).lower()
    if action not in ("buy", "sell", "none"):
        return False

    from src.api.ws import emit_agent_event

    event_id = emit_agent_event(run_id, bot_id, "status_log", inferred)
    if action in ("buy", "sell"):
        apply_status_log(bot_id, run_id, inferred, event_id=event_id)
    return True


def _status_log_summary(store: Store, run_id: int) -> str | None:
    for event in reversed(store.get_events(run_id)):
        if event.get("type") != "status_log":
            continue
        payload = event.get("payload") or {}
        if isinstance(payload, dict) and payload:
            return json.dumps(payload, separators=(",", ":"))
    return None


def finalize_run_record(
    store: Store,
    run_id: int,
    result: Any,
    cursor_run_id: str | None = None,
    bot_id: str = DEFAULT_BOT_ID,
) -> None:
    status = getattr(result, "status", "finished")
    summary = getattr(result, "result", None) or getattr(result, "text", None)
    _ensure_status_log_from_summary(store, run_id, bot_id, str(summary) if summary else None)
    structured = _status_log_summary(store, run_id)
    if structured:
        summary = structured if not summary else f"{structured}\n\n---\n\n{summary}"
    error = None
    if status == "error":
        error = str(summary)
    store.finish_run(
        run_id,
        status=str(status),
        summary=str(summary) if summary else None,
        error=error,
        cursor_run_id=cursor_run_id,
    )
    from src.trading.profile_gate import note_run_finished

    note_run_finished(bot_id, str(summary) if summary else None)
