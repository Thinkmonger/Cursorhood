from __future__ import annotations

import re
from typing import Any, Literal

AssetClass = Literal["equity", "option", "crypto", "none"]

# Baseline catalog from Robinhood's Agentic Trading MCP.
# https://robinhood.com/us/en/support/articles/trading-with-your-agent/
# The remote tools/list is merged at runtime so tools Robinhood ships later are
# still recognized without a code change.

ACCOUNT_TOOLS: frozenset[str] = frozenset({
    "get_accounts",
    "get_portfolio",
    "get_realized_pnl",
    "get_pnl_trade_history",
    "search",
})

WATCHLIST_TOOLS: frozenset[str] = frozenset({
    "get_watchlists",
    "get_watchlist_items",
    "get_option_watchlist",
    "get_popular_watchlists",
    "create_watchlist",
    "update_watchlist",
    "follow_watchlist",
    "unfollow_watchlist",
    "add_to_watchlist",
    "remove_from_watchlist",
    "add_option_to_watchlist",
    "remove_option_from_watchlist",
})

MARKET_DATA_TOOLS: frozenset[str] = frozenset({
    "get_equity_historicals",
    "get_equity_fundamentals",
    "get_financials",
    "get_equity_price_book",
    "get_equity_technical_indicators",
    "get_earnings_results",
    "get_earnings_calendar",
    "get_indexes",
    "get_index_quotes",
    "get_index_historicals",
    "get_equity_news",
    "get_equity_analyst_ratings",
    "get_politician_trades",
    "get_sec_filing",
    "get_sec_filing_index",
    "get_sec_filing_facts",
    "get_sec_filing_facts_catalog",
})

EQUITY_TOOLS: frozenset[str] = frozenset({
    "get_equity_positions",
    "get_equity_tax_lots",
    "get_equity_quotes",
    "get_equity_orders",
    "get_equity_tradability",
    "review_equity_order",
    "place_equity_order",
    "cancel_equity_order",
    "get_limited_margin_upgrade_info",
    "get_advanced_orders",
    "review_advanced_order",
    "place_advanced_order",
    "cancel_advanced_order",
})

OPTION_TOOLS: frozenset[str] = frozenset({
    "get_option_level_upgrade_info",
    "get_option_historicals",
    "get_option_chains",
    "get_option_instruments",
    "get_option_quotes",
    "get_option_positions",
    "get_option_orders",
    "review_option_order",
    "place_option_order",
    "cancel_option_order",
    "exercise_option",
    "cancel_option_exercise",
})

CRYPTO_TOOLS: frozenset[str] = frozenset({
    "get_currency_pairs",
    "get_crypto_quotes",
    "get_crypto_positions",
    "get_crypto_orders",
    "preview_crypto_order",
    "place_crypto_order",
    "cancel_crypto_order",
    "get_crypto_account_onboarding_info",
})

ALERT_TOOLS: frozenset[str] = frozenset({
    "get_alerts",
    "create_alert",
    "update_alert",
    "delete_alert",
    "get_alert_log",
    "mark_alerts_read",
})

SCANNER_TOOLS: frozenset[str] = frozenset({
    "get_scans",
    "get_scanner_filter_specs",
    "create_scan",
    "run_scan",
    "update_scan_filters",
    "update_scan_config",
})

TOOL_CATEGORIES: dict[str, frozenset[str]] = {
    "account": ACCOUNT_TOOLS,
    "watchlist": WATCHLIST_TOOLS,
    "market_data": MARKET_DATA_TOOLS,
    "equity": EQUITY_TOOLS,
    "option": OPTION_TOOLS,
    "crypto": CRYPTO_TOOLS,
    "scanner": SCANNER_TOOLS,
    "alert": ALERT_TOOLS,
}

# Categories shared by every bot. Asset-class tools are added per bot so a
# crypto bot never sees equity/option order schemas, and vice versa.
SHARED_CATEGORIES: frozenset[str] = frozenset({"account", "watchlist", "alert"})
CORE_CATEGORIES: frozenset[str] = SHARED_CATEGORIES | frozenset({"market_data", "equity"})

BASELINE_TRADING_TOOLS: frozenset[str] = frozenset().union(*TOOL_CATEGORIES.values())

ORDER_WRITE_TOOLS: frozenset[str] = frozenset({
    "place_equity_order",
    "cancel_equity_order",
    "place_advanced_order",
    "cancel_advanced_order",
    "place_option_order",
    "cancel_option_order",
    "exercise_option",
    "cancel_option_exercise",
    "place_crypto_order",
    "cancel_crypto_order",
})

PLACE_ORDER_TOOLS: frozenset[str] = frozenset({
    "place_equity_order",
    "place_advanced_order",
    "place_option_order",
    "place_crypto_order",
    "exercise_option",
})

CANCEL_ORDER_TOOLS: frozenset[str] = frozenset({
    "cancel_equity_order",
    "cancel_advanced_order",
    "cancel_option_order",
    "cancel_option_exercise",
    "cancel_crypto_order",
})

REVIEW_TOOLS: frozenset[str] = frozenset({
    "review_equity_order",
    "review_advanced_order",
    "review_option_order",
    "preview_crypto_order",
})

# Heuristic for tools Robinhood adds before the baseline above is updated.
_TRADING_TOOL_RE = re.compile(
    r"^(get_|search$|create_|update_|delete_|follow_|unfollow_|add_|remove_|run_|mark_|exercise_"
    r"|review_.*_order$|preview_.*_order$|place_.*_order$|cancel_.*_order$|cancel_.*_exercise$)",
    re.IGNORECASE,
)

_remote_tool_names: frozenset[str] = frozenset()


def refresh_remote_tool_cache(tool_names: list[str] | None) -> None:
    global _remote_tool_names
    if not tool_names:
        return
    _remote_tool_names = frozenset(str(n).strip() for n in tool_names if n and str(n).strip())


def remote_tool_names() -> frozenset[str]:
    return _remote_tool_names


def known_trading_tools() -> frozenset[str]:
    return BASELINE_TRADING_TOOLS | _remote_tool_names


def is_trading_tool(name: str | None) -> bool:
    if not name:
        return False
    tool = str(name).strip()
    if tool in known_trading_tools():
        return True
    return bool(_TRADING_TOOL_RE.match(tool))


def category_for_tool(name: str | None) -> str | None:
    if not name:
        return None
    tool = str(name).strip()
    for category, tools in TOOL_CATEGORIES.items():
        if tool in tools:
            return category
    lower = tool.lower()
    if "option" in lower:
        return "option"
    if "crypto" in lower or "currency_pair" in lower:
        return "crypto"
    if "watchlist" in lower:
        return "watchlist"
    if "scan" in lower:
        return "scanner"
    if "alert" in lower:
        return "alert"
    return None


def asset_class_for_tool(name: str | None) -> AssetClass:
    """Asset class an order tool acts on. Read-only tools return 'none'."""
    if not name:
        return "none"
    tool = str(name).strip()
    if tool not in ORDER_WRITE_TOOLS and not _looks_like_order_tool(tool):
        return "none"
    lower = tool.lower()
    if "option" in lower:
        return "option"
    if "crypto" in lower:
        return "crypto"
    return "equity"


def _looks_like_order_tool(tool: str) -> bool:
    """Tools that move real money and must pass the pre-trade risk hook."""
    lower = tool.lower()
    if ("place_" in lower or "cancel_" in lower) and "order" in lower:
        return True
    # Exercising or cancelling an exercise is a position-changing action too.
    return "exercise" in lower


def is_order_write_tool(name: str | None) -> bool:
    if not name:
        return False
    tool = str(name).strip()
    if tool in ORDER_WRITE_TOOLS:
        return True
    return _looks_like_order_tool(tool)


def is_place_order_tool(name: str | None) -> bool:
    if not name:
        return False
    tool = str(name).strip()
    if tool in PLACE_ORDER_TOOLS:
        return True
    lower = tool.lower()
    return "place_" in lower and "order" in lower


def is_cancel_order_tool(name: str | None) -> bool:
    if not name:
        return False
    tool = str(name).strip()
    if tool in CANCEL_ORDER_TOOLS:
        return True
    lower = tool.lower()
    return "cancel_" in lower and "order" in lower


def enabled_categories(
    *,
    options: bool = False,
    crypto: bool = False,
    scanners: bool = True,
    asset_class: str | None = None,
) -> set[str]:
    """Tool categories a bot should see. One asset class only."""
    if asset_class not in ("equity", "option", "crypto"):
        if crypto:
            asset_class = "crypto"
        elif options:
            asset_class = "option"
        else:
            asset_class = "equity"
    categories = set(SHARED_CATEGORIES)
    if scanners:
        categories.add("scanner")
    if asset_class == "crypto":
        categories.add("crypto")
    elif asset_class == "option":
        categories.update({"option", "equity", "market_data"})
    else:
        categories.update({"equity", "market_data"})
    return categories


def tool_allowed_for_categories(name: str, categories: set[str] | frozenset[str]) -> bool:
    """Filter predicate for the stdio proxy's tools/list.

    Unknown tools are allowed so newly shipped Robinhood tools are never hidden.
    """
    category = category_for_tool(name)
    if category is None:
        return True
    return category in categories


def group_tools_by_category(tool_names: list[str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {key: [] for key in TOOL_CATEGORIES}
    grouped["other"] = []
    for name in tool_names:
        category = category_for_tool(name) or "other"
        grouped.setdefault(category, []).append(str(name))
    return {key: sorted(value) for key, value in grouped.items() if value}


def compact_watchlists_for_prompt(payload: Any) -> list[dict[str, Any]] | None:
    """Compact watchlists for the agent Context block."""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return None
    lists = data.get("watchlists")
    if not isinstance(lists, list):
        return None

    out: list[dict[str, Any]] = []
    for item in lists:
        if not isinstance(item, dict):
            continue
        row: dict[str, Any] = {}
        name = item.get("name") or item.get("display_name") or item.get("id")
        if name:
            row["name"] = str(name)
        symbols = extract_symbols(item.get("symbols") or item.get("items"))
        if symbols:
            row["symbols"] = symbols[:30]
        if row:
            out.append(row)
    return out or None


def extract_symbols(
    raw: Any,
    limit: int | None = None,
    *,
    object_types: set[str] | frozenset[str] | None = None,
) -> list[str]:
    """Pull ticker symbols out of the varied shapes Robinhood returns."""
    out: list[str] = []
    if isinstance(raw, dict) and isinstance(raw.get("data"), (dict, list)):
        raw = raw["data"]
    if isinstance(raw, dict):
        for key in ("symbols", "items", "results", "instruments"):
            if isinstance(raw.get(key), list):
                raw = raw[key]
                break
        else:
            raw = []
    if not isinstance(raw, list):
        return out
    wanted = {str(t).lower() for t in object_types} if object_types else None
    for entry in raw:
        symbol: str | None = None
        if isinstance(entry, str):
            symbol = entry
        elif isinstance(entry, dict):
            otype = str(
                entry.get("object_type") or entry.get("objectType") or entry.get("type") or ""
            ).lower()
            if wanted and otype and otype not in wanted:
                continue
            value = (
                entry.get("symbol")
                or entry.get("ticker")
                or entry.get("instrument_symbol")
                or entry.get("display_symbol")
                or entry.get("pair")
                or entry.get("currency_pair")
                or entry.get("code")
            )
            if not value and isinstance(entry.get("currency"), dict):
                value = entry["currency"].get("code") or entry["currency"].get("symbol")
            if value:
                symbol = str(value)
        if not symbol:
            continue
        cleaned = symbol.strip().upper()
        if cleaned and cleaned not in out:
            out.append(cleaned)
        if limit and len(out) >= limit:
            break
    return out
