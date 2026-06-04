#!/usr/bin/env python3
"""Cursor hook: enforce limits.yaml before Robinhood trade MCP calls."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def load_limits() -> dict:
    try:
        from src.db.migrate import DEFAULT_BOT_ID
        from src.db.store import Store
        from src.settings.service import SettingsService

        store = Store()
        active = store.get_active_run()
        bot_id = active.get("bot_id", DEFAULT_BOT_ID) if active else DEFAULT_BOT_ID
        return SettingsService(bot_id).read_limits().model_dump()
    except Exception:
        pass
    limits_path = ROOT / "config" / "limits.yaml"
    if not limits_path.exists():
        return {}
    return yaml.safe_load(limits_path.read_text(encoding="utf-8")) or {}


def load_bot_app() -> dict:
    try:
        from src.db.migrate import DEFAULT_BOT_ID
        from src.db.store import Store
        from src.settings.service import SettingsService

        store = Store()
        active = store.get_active_run()
        bot_id = active.get("bot_id", DEFAULT_BOT_ID) if active else DEFAULT_BOT_ID
        return SettingsService(bot_id).read_bot_app().model_dump()
    except Exception:
        return {}


def is_market_hours() -> bool:
    from src.trading.market_hours import is_market_hours as _is_market_hours

    return _is_market_hours()


def log_hook(event_type: str, payload: dict) -> None:
    try:
        from src.db.store import Store

        Store().log_hook_event(event_type, payload)
    except Exception:
        pass


def deny(message: str) -> None:
    log_hook("hook_deny", {"message": message})
    print(json.dumps({
        "permission": "deny",
        "agent_message": message,
        "user_message": message,
    }))
    sys.exit(0)


def allow() -> None:
    log_hook("hook_allow", {"message": "Trade check passed"})
    print(json.dumps({"permission": "allow"}))
    sys.exit(0)


def main() -> None:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        deny("Invalid hook input")

    tool_name = data.get("tool_name") or data.get("name") or ""
    if "place_equity_order" not in tool_name and "cancel_equity_order" not in tool_name:
        allow()

    args = data.get("tool_input") or data.get("arguments") or {}
    app = load_bot_app()
    limits = load_limits()

    if app.get("simulation_mode") and "place_equity_order" in tool_name:
        try:
            from src.db.migrate import DEFAULT_BOT_ID
            from src.db.store import Store
            from src.simulation.ledger import record_paper_order

            store = Store()
            active = store.get_active_run()
            bot_id = active.get("bot_id", DEFAULT_BOT_ID) if active else DEFAULT_BOT_ID
            symbol = (args.get("symbol") or args.get("instrument") or "").upper()
            side = str(args.get("side") or "buy").lower()
            notional = float(args.get("notional") or args.get("amount") or limits.get("max_order_notional_usd") or 0)
            snapshot = None
            if active:
                for ev in store.get_events(int(active["id"])):
                    if ev.get("type") == "portfolio_snapshot":
                        snapshot = ev.get("payload") or {}
                        break
            quotes = snapshot.get("quotes") if snapshot else None
            price = None
            if symbol:
                from src.simulation.ledger import _resolve_quote_price

                price, _quotes = _resolve_quote_price(symbol, quotes)
                quotes = _quotes
            if symbol and price:
                record_paper_order(
                    bot_id,
                    symbol=symbol,
                    side=side,
                    notional=notional,
                    price=price,
                    quotes_payload=quotes,
                )
        except Exception:
            pass
        deny("Simulation mode — order recorded in paper ledger only, not sent to Robinhood.")

    if limits.get("market_hours_only") and not is_market_hours():
        deny("Market hours only — trading blocked outside 9:30–16:00 ET weekdays.")

    symbol = (args.get("symbol") or args.get("instrument") or "").upper()
    allowed = [s.upper() for s in limits.get("allowed_symbols") or []]
    if allowed and symbol and symbol not in allowed:
        deny(f"Symbol {symbol} not in allowed list: {', '.join(allowed)}")

    notional = float(args.get("notional") or args.get("amount") or args.get("quantity") or 0)
    max_order = float(limits.get("max_order_notional_usd") or 0)
    if max_order and notional > max_order:
        deny(f"Order notional ${notional:.2f} exceeds max ${max_order:.2f}")

    try:
        from src.db.store import Store

        store = Store()
        active = store.get_active_run()
        bot_id = active.get("bot_id", "default") if active else None
        last = store.get_last_order_time(bot_id=bot_id)
        min_gap = int(limits.get("min_seconds_between_orders") or 0)
        if last and min_gap:
            last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
            delta = (datetime.now(timezone.utc) - last_dt).total_seconds()
            if delta < min_gap:
                deny(f"Min {min_gap}s between orders — wait {int(min_gap - delta)}s.")
    except Exception:
        pass

    allow()


if __name__ == "__main__":
    main()
