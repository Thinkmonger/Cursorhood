"""Composed per-symbol research reports built from Robinhood market-data tools.

Reports are available on demand through the console and to the agent; they are
never force-fed into a cycle prompt unless the bot's context profile asks for it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.trading.rh_market_data import (
    compact_indicators,
    fetch_earnings_results,
    fetch_financials,
    fetch_fundamentals,
    fetch_price_book,
    fetch_technical_indicators,
    payload_error,
    unwrap,
)
from src.trading.trade_history import extract_mcp_data

logger = logging.getLogger(__name__)

MAX_EARNINGS_ROWS = 4
MAX_FINANCIAL_ROWS = 4

_FUNDAMENTAL_KEYS = (
    "market_cap",
    "pe_ratio",
    "pb_ratio",
    "dividend_yield",
    "high_52_weeks",
    "low_52_weeks",
    "average_volume",
    "volume",
    "open",
    "high",
    "low",
    "market_date",
    "shares_outstanding",
    "sector",
    "industry",
    "description",
)


async def search_symbols(query: str, *, limit: int = 10) -> list[dict[str, Any]]:
    from src.setup.mcp_client import MCPClient

    result = await MCPClient().call_tool("search", {"query": query})
    if not result.get("ok"):
        return []
    return _compact_search(extract_mcp_data(result), limit=limit)


def _compact_search(payload: Any, *, limit: int) -> list[dict[str, Any]]:
    source = unwrap(payload)
    if isinstance(source, dict):
        for key in ("results", "instruments", "data", "matches"):
            if isinstance(source.get(key), list):
                source = source[key]
                break
    if not isinstance(source, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in source[:limit]:
        if isinstance(item, str):
            rows.append({"symbol": item.upper()})
            continue
        if not isinstance(item, dict):
            continue
        symbol = item.get("symbol") or item.get("ticker")
        if not symbol:
            continue
        row: dict[str, Any] = {"symbol": str(symbol).upper()}
        name = item.get("name") or item.get("simple_name") or item.get("display_name")
        if name:
            row["name"] = str(name)
        if item.get("type"):
            row["type"] = str(item["type"])
        rows.append(row)
    return rows


def _first_row(payload: Any) -> dict[str, Any]:
    payload = unwrap(payload)
    if isinstance(payload, dict):
        for key in ("fundamentals", "results", "books", "data"):
            value = payload.get(key)
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value[0]
            if isinstance(value, dict):
                return value
        return payload
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return payload[0]
    return {}


def _list_rows(payload: Any, *keys: str) -> list[dict[str, Any]]:
    payload = unwrap(payload)
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if not isinstance(payload, dict):
        return []
    for key in (*keys, "results", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [r for r in value if isinstance(r, dict)]
        # Per-symbol envelopes nest the rows one level deeper.
        if isinstance(value, dict):
            nested = _list_rows(value, *keys)
            if nested:
                return nested
    return []


def compact_fundamentals(payload: Any) -> dict[str, Any]:
    row = _first_row(payload)
    out: dict[str, Any] = {}
    for key in _FUNDAMENTAL_KEYS:
        value = row.get(key)
        if value in (None, ""):
            continue
        if key == "description":
            out[key] = str(value)[:400]
        else:
            out[key] = value
    return out


def compact_earnings(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _list_rows(payload, "earnings", "results")[-MAX_EARNINGS_ROWS:]:
        entry = {
            k: row.get(k)
            for k in ("year", "quarter", "report_date", "eps_actual", "eps_estimate", "date")
            if row.get(k) not in (None, "")
        }
        eps = row.get("eps")
        if isinstance(eps, dict):
            if eps.get("actual") is not None:
                entry.setdefault("eps_actual", eps["actual"])
            if eps.get("estimate") is not None:
                entry.setdefault("eps_estimate", eps["estimate"])
        report = row.get("report")
        if isinstance(report, dict) and report.get("date"):
            entry.setdefault("report_date", report["date"])
        if entry:
            rows.append(entry)
    return rows


_FINANCIAL_KEYS = (
    "fiscal_year",
    "fiscal_quarter",
    "period_end_date",
    "period",
    "fiscal_period",
    "revenue",
    "gross_profit",
    "net_income",
    "net_margin",
    "operating_income",
    "eps",
    "free_cash_flow",
)


def compact_financials(payload: Any) -> list[dict[str, Any]]:
    """Per-period rows. Robinhood nests them under each symbol's `financials`."""
    periods: list[dict[str, Any]] = []
    for row in _list_rows(payload, "financials", "results"):
        nested = row.get("financials")
        if isinstance(nested, list):
            periods.extend(r for r in nested if isinstance(r, dict))
        else:
            periods.append(row)

    rows: list[dict[str, Any]] = []
    for row in periods[:MAX_FINANCIAL_ROWS]:
        entry = {k: row.get(k) for k in _FINANCIAL_KEYS if row.get(k) not in (None, "")}
        if entry:
            rows.append(entry)
    return rows


def _best_level(levels: Any) -> dict[str, Any]:
    if isinstance(levels, list) and levels and isinstance(levels[0], dict):
        return levels[0]
    return {}


def compact_price_book(payload: Any) -> dict[str, Any]:
    row = _first_row(payload)
    out: dict[str, Any] = {}
    for source, target in (
        ("bid_price", "bid"),
        ("ask_price", "ask"),
        ("bid_size", "bid_size"),
        ("ask_size", "ask_size"),
        ("last_trade_price", "last"),
    ):
        value = row.get(source)
        if value not in (None, ""):
            out[target] = value

    # Current shape is depth ladders: {"asks": [...], "bids": [...]} (empty when closed).
    for side, price_key, size_key in (("bids", "bid", "bid_size"), ("asks", "ask", "ask_size")):
        level = _best_level(row.get(side))
        price = level.get("price") or level.get("bid_price") or level.get("ask_price")
        if isinstance(price, dict):
            price = price.get("amount")
        if price not in (None, "") and price_key not in out:
            out[price_key] = price
        size = level.get("quantity") or level.get("size")
        if size not in (None, "") and size_key not in out:
            out[size_key] = size
    if row.get("updated_at") and out:
        out["updated_at"] = row["updated_at"]
    return out


async def _call(tool: str, args: dict[str, Any]) -> Any | None:
    from src.setup.mcp_client import MCPClient

    result = await MCPClient().call_tool(tool, args)
    if not result.get("ok"):
        logger.debug("Robinhood %s failed: %s", tool, result.get("error"))
        return None
    data = extract_mcp_data(result)
    error = payload_error(data)
    if error:
        logger.debug("Robinhood %s rejected args %s: %s", tool, args, error)
        return None
    return unwrap(data)


async def fetch_news(symbol: str) -> Any | None:
    return await _call("get_equity_news", {"symbol": symbol})


async def fetch_analyst_ratings(symbol: str) -> Any | None:
    return await _call("get_equity_analyst_ratings", {"symbols": [str(symbol).upper()]})


async def fetch_politician_trades(symbol: str | None = None) -> Any | None:
    return await _call("get_politician_trades", {"symbol": symbol} if symbol else {})


async def fetch_sec_filing_index(symbol: str) -> Any | None:
    return await _call("get_sec_filing_index", {"symbol": symbol})


async def fetch_sec_filing(filing_id: str) -> Any | None:
    return await _call("get_sec_filing", {"filing_id": filing_id})


_NEWS_FIELD_ALIASES = {
    "title": ("title", "headline"),
    "source": ("publisher", "source", "author"),
    "published_at": ("published_at", "updated_at", "created_at"),
    "summary": ("preview_text", "summary", "description"),
    "url": ("url", "article_url", "source_url"),
}


def compact_news(payload: Any, *, limit: int = 5) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _list_rows(payload, "news", "articles", "results")[:limit]:
        entry: dict[str, Any] = {}
        for target, sources in _NEWS_FIELD_ALIASES.items():
            for source in sources:
                value = row.get(source)
                if value not in (None, ""):
                    entry[target] = value
                    break
        if entry.get("summary"):
            entry["summary"] = str(entry["summary"])[:300]
        if entry:
            rows.append(entry)
    return rows


_RATING_KEYS = (
    "num_buy_ratings",
    "num_hold_ratings",
    "num_sell_ratings",
    "mean_price_target",
    "high_price_target",
    "low_price_target",
    "target_price",
    "summary",
)


def compact_ratings(payload: Any) -> dict[str, Any]:
    row = _first_row(payload)
    # Counts and targets live under a nested `ratings` object.
    nested = row.get("ratings")
    if isinstance(nested, dict):
        row = {**row, **nested}
    return {k: row.get(k) for k in _RATING_KEYS if row.get(k) not in (None, "")}


async def research_report(symbol: str, *, include_financials: bool = True) -> dict[str, Any]:
    """One bounded report per symbol, gathering every market-data tool in parallel."""
    ticker = str(symbol).strip().upper()
    if not ticker:
        return {"ok": False, "error": "No symbol given"}

    tasks = [
        fetch_fundamentals([ticker]),
        fetch_technical_indicators(ticker),
        fetch_earnings_results(ticker),
        fetch_price_book(ticker),
        fetch_news(ticker),
        fetch_analyst_ratings(ticker),
    ]
    if include_financials:
        tasks.append(fetch_financials(ticker))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    def _value(index: int) -> Any:
        if index >= len(results):
            return None
        value = results[index]
        if isinstance(value, BaseException):
            logger.debug("Research fetch %s failed for %s: %s", index, ticker, value)
            return None
        return value

    section_names = (
        "fundamentals",
        "indicators",
        "earnings",
        "price_book",
        "news",
        "analyst_ratings",
        "financials",
    )
    unavailable = [
        section_names[i] for i in range(len(results)) if _value(i) in (None, {}, [])
    ]

    report: dict[str, Any] = {"ok": True, "symbol": ticker}
    fundamentals = compact_fundamentals(_value(0))
    if fundamentals:
        report["fundamentals"] = fundamentals
    indicators = _value(1)
    if isinstance(indicators, dict) and indicators:
        report["indicators"] = compact_indicators(indicators) or indicators
    earnings = compact_earnings(_value(2))
    if earnings:
        report["earnings"] = earnings
    price_book = compact_price_book(_value(3))
    if price_book:
        report["price_book"] = price_book
    news = compact_news(_value(4))
    if news:
        report["news"] = news
    ratings = compact_ratings(_value(5))
    if ratings:
        report["analyst_ratings"] = ratings
    if include_financials:
        financials = compact_financials(_value(6))
        if financials:
            report["financials"] = financials

    if unavailable:
        report["unavailable"] = unavailable
    if not any(k in report for k in section_names):
        report["ok"] = False
        report["error"] = f"No research data returned for {ticker}"
    return report
