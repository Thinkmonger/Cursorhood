from __future__ import annotations

import json
import re
from typing import Any

from src.db.migrate import DEFAULT_BOT_ID
from src.db.store import Store
from src.settings.service import SettingsService
from src.trading.mcp_tools import compact_watchlists_for_prompt, is_trading_tool

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


def _essential_limits(limits: Any, resolved_symbols: list[str] | None = None) -> dict[str, Any]:
    asset_class = str(getattr(limits, "asset_class", None) or "equity")
    out: dict[str, Any] = {
        "asset_class": asset_class,
        "allowed_symbols": resolved_symbols or limits.allowed_symbols,
        "max_open_positions": limits.max_open_positions,
        "max_order_notional_usd": limits.max_order_notional_usd,
        "max_daily_loss_usd": limits.max_daily_loss_usd,
        "market_hours_only": limits.market_hours_only,
        "min_seconds_between_orders": limits.min_seconds_between_orders,
    }
    if asset_class == "option":
        out["options"] = {
            "max_contracts": limits.max_option_contracts,
            "max_notional_usd": limits.max_option_notional_usd,
            "days_to_expiry": [limits.min_days_to_expiry, limits.max_days_to_expiry],
            "allowed_types": limits.allowed_option_types,
            "allow_selling": limits.allow_option_selling,
        }
    if asset_class == "crypto":
        out["crypto"] = {
            "allowed_pairs": limits.allowed_crypto_pairs,
            "max_notional_usd": limits.max_crypto_notional_usd,
            "note": "Crypto trades 24/7 and is exempt from market_hours_only. Empty allowed_pairs means the symbol source is the universe.",
        }
    return out


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


_INDICATOR_ALIASES: dict[str, tuple[str, ...]] = {
    "rsi": ("rsi", "rsi_14"),
    "macd": ("macd", "macd_signal", "macd_histogram"),
    "bollinger": ("bollinger_upper", "bollinger_middle", "bollinger_lower"),
    "atr": ("atr",),
    "vwap": ("vwap",),
    "ema": ("ema_12", "ema_26"),
    "sma_50": ("sma_50",),
    "sma_200": ("sma_200",),
}


def requested_indicators(strategy: str) -> set[str]:
    """Indicator keys the strategy text mentions, so Context carries only those."""
    text = (strategy or "").lower()
    keys: set[str] = set()
    for alias, indicator_keys in _INDICATOR_ALIASES.items():
        if alias in text:
            keys.update(indicator_keys)
    return keys


def _technicals_for_prompt(
    bars_payload: dict[str, Any] | None,
    wanted: set[str] | None = None,
) -> dict[str, Any]:
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
        indicators = entry.get("indicators")
        if wanted and isinstance(indicators, dict):
            extra = {k: v for k, v in indicators.items() if k in wanted}
            if extra:
                row.update(extra)
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
    indicators: set[str] | None = None,
    profile: str = "minimal",
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

    technicals = _technicals_for_prompt(snapshot.get("historical_bars_1h"), indicators)
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

    symbol_source = snapshot.get("symbol_source")
    if isinstance(symbol_source, dict) and not str(symbol_source.get("source", "")).startswith("static"):
        context["symbol_source"] = symbol_source

    # Watchlists only matter to the agent when they aren't already the symbol source.
    if profile != "minimal" or not context.get("symbol_source"):
        watchlists = compact_watchlists_for_prompt(snapshot.get("watchlists"))
        if watchlists:
            context["watchlists"] = watchlists

    if snapshot.get("option_positions"):
        context["option_positions"] = snapshot["option_positions"]
    if snapshot.get("crypto_positions"):
        context["crypto_positions"] = snapshot["crypto_positions"]

    if profile == "research":
        fundamentals = snapshot.get("fundamentals")
        if fundamentals:
            context["fundamentals"] = fundamentals
        earnings = snapshot.get("earnings_calendar")
        if earnings:
            context["earnings_calendar"] = earnings

    sim_ctx = snapshot.get("simulation_context")
    if isinstance(sim_ctx, dict) and sim_ctx.get("mode") == "simulation":
        simulation: dict[str, Any] = {"paper": sim_ctx.get("uses_live_portfolio") is False}
        for key, target in (
            ("paper_open_orders", "open_paper_orders"),
            ("paper_realized_pnl", "realized_pnl"),
        ):
            if sim_ctx.get(key):
                simulation[target] = sim_ctx[key]
        context["simulation"] = simulation

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
    resolved_symbols = ((trading_snapshot or {}).get("symbol_source") or {}).get("symbols")

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
        _json_compact(_essential_limits(limits, resolved_symbols)),
    ]

    if trading_snapshot:
        context = _minimal_decision_context(
            trading_snapshot,
            allowed_symbols=resolved_symbols or limits.allowed_symbols,
            indicators=requested_indicators(strategy),
            profile=app.context_profile,
        )
        if context:
            parts.extend(["", "## Context", _json_compact(context)])

    if app.simulation_mode:
        # Orders are intercepted and booked as paper fills, so the agent should
        # trade normally rather than being told both "no orders" and "review first".
        note = (
            "SIM: paper trading. Place orders as usual — they are intercepted and "
            "filled against the paper ledger, never sent to Robinhood. log_event required."
        )
        if app.simulation_include_live_portfolio:
            note += " Live portfolio visible."
        parts.append(note)

    if gate.get("active"):
        parts.append("BLOCK: investor profile incomplete — no orders; log_event none.")

    review = {
        "option": "review_option_order",
        "crypto": "preview_crypto_order",
    }.get(str(getattr(limits, "asset_class", "")), "review_equity_order")
    parts.append(
        f"Apply strategy to Context. Review before placing ({review}). "
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
        if tool and is_trading_tool(tool["name"]):
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
                    elif is_trading_tool(tool_name):
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
    status = str(getattr(result, "status", "finished") or "finished")
    raw_result = getattr(result, "result", None) or getattr(result, "text", None)
    sdk_text = str(raw_result).strip() if raw_result else ""
    _ensure_status_log_from_summary(store, run_id, bot_id, sdk_text or None)
    structured = _status_log_summary(store, run_id)
    if structured and sdk_text:
        summary: str | None = f"{structured}\n\n---\n\n{sdk_text}"
    else:
        summary = structured or sdk_text or None
    error = None
    # Cursor WaitLiveRun maps an empty/unspecified payload to status=error
    # with no result text. If log_event already stored a cycle summary, this
    # is a completed cycle — not a failed one.
    if status == "error" and structured and not sdk_text:
        status = "finished"
    elif status == "error":
        error = sdk_text or str(summary) or "Cursor run error"
    store.finish_run(
        run_id,
        status=str(status),
        summary=str(summary) if summary else None,
        error=error,
        cursor_run_id=cursor_run_id,
    )
    from src.trading.profile_gate import note_run_finished

    note_run_finished(bot_id, str(summary) if summary else None)
