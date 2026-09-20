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
    source = payload
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
    if isinstance(payload, dict):
        for key in ("fundamentals", "results", "data"):
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
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if not isinstance(payload, dict):
        return []
    for key in (*keys, "results", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [r for r in value if isinstance(r, dict)]
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
            entry.setdefault("eps_actual", eps.get("actual"))
            entry.setdefault("eps_estimate", eps.get("estimate"))
        if entry:
            rows.append(entry)
    return rows


def compact_financials(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _list_rows(payload, "financials", "results")[-MAX_FINANCIAL_ROWS:]:
        entry = {
            k: row.get(k)
            for k in (
                "period",
                "fiscal_period",
                "revenue",
                "net_income",
                "gross_profit",
                "operating_income",
                "eps",
                "free_cash_flow",
            )
            if row.get(k) not in (None, "")
        }
        if entry:
            rows.append(entry)
    return rows


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
    return out


async def _call(tool: str, args: dict[str, Any]) -> Any | None:
    from src.setup.mcp_client import MCPClient

    result = await MCPClient().call_tool(tool, args)
    if not result.get("ok"):
        logger.debug("Robinhood %s failed: %s", tool, result.get("error"))
        return None
    return extract_mcp_data(result)


async def fetch_news(symbol: str) -> Any | None:
    return await _call("get_equity_news", {"symbol": symbol})


async def fetch_analyst_ratings(symbol: str) -> Any | None:
    return await _call("get_equity_analyst_ratings", {"symbol": symbol})


async def fetch_politician_trades(symbol: str | None = None) -> Any | None:
    return await _call("get_politician_trades", {"symbol": symbol} if symbol else {})


async def fetch_sec_filing_index(symbol: str) -> Any | None:
    return await _call("get_sec_filing_index", {"symbol": symbol})


async def fetch_sec_filing(filing_id: str) -> Any | None:
    return await _call("get_sec_filing", {"filing_id": filing_id})


def compact_news(payload: Any, *, limit: int = 5) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _list_rows(payload, "news", "articles", "results")[:limit]:
        entry = {
            k: row.get(k)
            for k in ("title", "source", "published_at", "summary", "url")
            if row.get(k) not in (None, "")
        }
        if entry.get("summary"):
            entry["summary"] = str(entry["summary"])[:300]
        if entry:
            rows.append(entry)
    return rows


def compact_ratings(payload: Any) -> dict[str, Any]:
    row = _first_row(payload)
    return {
        k: row.get(k)
        for k in ("num_buy_ratings", "num_hold_ratings", "num_sell_ratings", "target_price", "summary")
        if row.get(k) not in (None, "")
    }


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
    return report
