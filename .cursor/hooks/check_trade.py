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


def active_bot_id() -> str:
    from src.db.migrate import DEFAULT_BOT_ID
    from src.db.store import Store

    active = Store().get_active_run()
    return active.get("bot_id", DEFAULT_BOT_ID) if active else DEFAULT_BOT_ID


def active_run_id() -> int | None:
    from src.db.store import Store

    active = Store().get_active_run()
    return int(active["id"]) if active else None


def load_limits() -> dict:
    try:
        from src.settings.service import SettingsService

        return SettingsService(active_bot_id()).read_limits().model_dump()
    except Exception:
        pass
    limits_path = ROOT / "config" / "limits.yaml"
    if not limits_path.exists():
        return {}
    return yaml.safe_load(limits_path.read_text(encoding="utf-8")) or {}


def load_bot_app() -> dict:
    try:
        from src.settings.service import SettingsService

        return SettingsService(active_bot_id()).read_bot_app().model_dump()
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


def _float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def snapshot_quotes(run_id: int | None):
    if run_id is None:
        return None
    from src.db.store import Store

    for ev in Store().get_events(run_id):
        if ev.get("type") == "portfolio_snapshot":
            return (ev.get("payload") or {}).get("quotes")
    return None


def simulate_order(tool_name: str, args: dict, asset_class: str, limits: dict) -> None:
    """Route a would-be live order into the paper broker, then deny the real call."""
    if "exercise" in tool_name.lower():
        deny("Simulation mode — option exercise is not modeled by the paper broker.")
    try:
        from src.simulation.ledger import OrderIntent, cancel_paper_orders, submit_paper_order

        bot_id = active_bot_id()
        run_id = active_run_id()
        symbol = order_symbol(args, asset_class)

        if "cancel_" in tool_name.lower():
            cancelled = cancel_paper_orders(bot_id, order_id=args.get("order_id"), symbol=symbol or None)
            deny(
                f"Simulation mode — cancelled {len(cancelled)} resting paper order(s); "
                "nothing was sent to Robinhood."
            )

        intent = OrderIntent(
            symbol=symbol,
            side=str(args.get("side") or "buy").lower(),
            asset_class=asset_class,
            order_type=args.get("type") or args.get("order_type") or "market",
            qty=_optional_float(args.get("quantity") or args.get("contracts")),
            notional=_optional_float(
                args.get("notional") or args.get("amount") or args.get("amount_usd")
            ),
            limit_price=_optional_float(args.get("limit_price") or args.get("price")),
            stop_price=_optional_float(args.get("stop_price")),
            meta=option_meta(args) if asset_class == "option" else {},
            intent_key=f"hook:{run_id}:{symbol}:{args.get('side')}:{tool_name}",
            run_id=run_id,
        )
        if intent.qty is None and intent.notional is None:
            intent.notional = _float(limits.get("max_order_notional_usd"))
        result = submit_paper_order(bot_id, intent, snapshot_quotes(run_id))
        if not result.get("ok"):
            deny(f"Simulation mode — paper order rejected: {result.get('reason')}")
    except SystemExit:
        raise
    except Exception:
        pass
    deny("Simulation mode — order recorded in paper ledger only, not sent to Robinhood.")


def _optional_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def order_symbol(args: dict, asset_class: str) -> str:
    if asset_class == "crypto":
        from src.trading.crypto import normalize_pair

        return normalize_pair(
            args.get("symbol") or args.get("pair") or args.get("currency_pair") or ""
        )
    return str(
        args.get("symbol")
        or args.get("chain_symbol")
        or args.get("underlying")
        or args.get("instrument")
        or ""
    ).upper()


def option_meta(args: dict) -> dict:
    return {
        "expiry": str(args.get("expiration_date") or args.get("expiry") or "")[:10] or None,
        "strike": _optional_float(args.get("strike_price") or args.get("strike")),
        "type": str(args.get("option_type") or args.get("type") or "").lower() or None,
    }


def check_equity(args: dict, limits: dict) -> None:
    if limits.get("market_hours_only") and not is_market_hours():
        deny("Market hours only — trading blocked outside 9:30–16:00 ET weekdays.")

    symbol = order_symbol(args, "equity")
    allowed = [s.upper() for s in limits.get("allowed_symbols") or []]
    if allowed and symbol and symbol not in allowed:
        deny(f"Symbol {symbol} not in allowed list: {', '.join(allowed)}")

    notional = _float(args.get("notional") or args.get("amount") or args.get("quantity"))
    max_order = _float(limits.get("max_order_notional_usd"))
    if max_order and notional > max_order:
        deny(f"Order notional ${notional:.2f} exceeds max ${max_order:.2f}")


def check_option(args: dict, limits: dict, tool_name: str = "") -> None:
    if not limits.get("options_enabled"):
        deny("Options trading is disabled for this bot (limits.options_enabled = false).")

    if "exercise" in tool_name.lower():
        # Exercising converts a contract into 100 shares of stock; that is a much
        # larger position than the premium-based notional caps contemplate.
        deny("Exercising options is not permitted by this bot — close the contract instead.")

    if limits.get("market_hours_only") and not is_market_hours():
        deny("Market hours only — options trading blocked outside 9:30–16:00 ET weekdays.")

    symbol = order_symbol(args, "option")
    allowed = [s.upper() for s in limits.get("allowed_symbols") or []]
    if allowed and symbol and symbol not in allowed:
        deny(f"Underlying {symbol} not in allowed list: {', '.join(allowed)}")

    from src.trading.options import days_to_expiry, option_order_notional, option_side_is_sell

    if not limits.get("allow_option_selling") and option_side_is_sell(args):
        deny("Selling options is disabled for this bot (limits.allow_option_selling = false).")

    option_type = str(args.get("option_type") or args.get("type") or "").lower()
    allowed_types = [str(t).lower() for t in limits.get("allowed_option_types") or []]
    if option_type and allowed_types and option_type not in allowed_types:
        deny(f"Option type {option_type} not allowed; permitted: {', '.join(allowed_types)}")

    contracts = _float(args.get("quantity") or args.get("contracts"))
    max_contracts = _float(limits.get("max_option_contracts"))
    if max_contracts and contracts > max_contracts:
        deny(f"{contracts:g} contracts exceeds max {max_contracts:g}")

    notional = option_order_notional(args)
    max_notional = _float(limits.get("max_option_notional_usd"))
    if notional is not None and max_notional and notional > max_notional:
        deny(f"Option notional ${notional:.2f} exceeds max ${max_notional:.2f}")

    dte = days_to_expiry(args.get("expiration_date") or args.get("expiry"))
    if dte is not None:
        min_dte = int(_float(limits.get("min_days_to_expiry")))
        max_dte = int(_float(limits.get("max_days_to_expiry")))
        if min_dte and dte < min_dte:
            deny(f"Expiry is {dte} days out, below the {min_dte}-day minimum.")
        if max_dte and dte > max_dte:
            deny(f"Expiry is {dte} days out, beyond the {max_dte}-day maximum.")


def check_crypto(args: dict, limits: dict) -> None:
    """Crypto trades 24/7, so the market-hours gate deliberately does not apply."""
    if not limits.get("crypto_enabled"):
        deny("Crypto trading is disabled for this bot (limits.crypto_enabled = false).")

    from src.trading.crypto import crypto_order_notional, pair_allowed

    pair = order_symbol(args, "crypto")
    allowed = limits.get("allowed_crypto_pairs") or []
    if pair and not pair_allowed(pair, allowed):
        listed = ", ".join(allowed) if allowed else "none configured"
        deny(f"Crypto pair {pair} not in allowed pairs: {listed}")

    notional = crypto_order_notional(args)
    max_notional = _float(limits.get("max_crypto_notional_usd"))
    if notional is not None and max_notional and notional > max_notional:
        deny(f"Crypto notional ${notional:.2f} exceeds max ${max_notional:.2f}")


def check_order_spacing(limits: dict) -> None:
    try:
        from src.db.store import Store

        store = Store()
        active = store.get_active_run()
        bot_id = active.get("bot_id", "default") if active else None
        last = store.get_last_order_time(bot_id=bot_id)
        min_gap = int(_float(limits.get("min_seconds_between_orders")))
        if last and min_gap:
            last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
            delta = (datetime.now(timezone.utc) - last_dt).total_seconds()
            if delta < min_gap:
                deny(f"Min {min_gap}s between orders — wait {int(min_gap - delta)}s.")
    except SystemExit:
        raise
    except Exception:
        pass


def main() -> None:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        deny("Invalid hook input")

    tool_name = data.get("tool_name") or data.get("name") or ""
    from src.trading.mcp_tools import asset_class_for_tool, is_order_write_tool

    if not is_order_write_tool(tool_name):
        allow()

    args = data.get("tool_input") or data.get("arguments") or {}
    asset_class = asset_class_for_tool(tool_name)
    app = load_bot_app()
    limits = load_limits()

    if app.get("simulation_mode"):
        simulate_order(tool_name, args, asset_class, limits)

    if asset_class == "option":
        check_option(args, limits, tool_name)
    elif asset_class == "crypto":
        check_crypto(args, limits)
    else:
        check_equity(args, limits)

    check_order_spacing(limits)
    allow()


if __name__ == "__main__":
    main()
