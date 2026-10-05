"""Compute per-bot performance metrics from run/event history."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from src.db.store import Store


def _parse_summary(run: dict[str, Any]) -> dict[str, Any] | None:
    summary = run.get("summary")
    if not summary:
        return None
    sep = summary.find("\n\n---\n\n")
    head = summary[:sep].strip() if sep >= 0 else summary.strip()
    try:
        data = json.loads(head)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def _metrics_from_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    from src.stats.portfolio import (
        _build_holdings,
        _portfolio_fields,
        _position_rows,
        _quote_prices,
    )

    portfolio = _portfolio_fields(payload.get("portfolio"))
    position_rows = _position_rows(payload.get("positions"))
    quotes = _quote_prices(payload.get("quotes"))
    holdings = _build_holdings(position_rows, quotes)
    invested = sum(h["market_value"] or 0 for h in holdings)
    unrealized = sum(
        h["unrealized_pl"] or 0 for h in holdings if h["unrealized_pl"] is not None
    )
    total = portfolio.get("total_value")
    cash = portfolio.get("cash")
    if total is None and (invested or cash):
        total = round((invested or 0) + (cash or 0), 4)
    return {
        "value": total,
        "holdings_value": round(invested, 2) if invested else None,
        "unrealized_pl": round(unrealized, 2) if holdings else None,
        "position_count": len(holdings),
    }


def _portfolio_value_from_snapshot(payload: dict[str, Any]) -> float | None:
    metrics = _metrics_from_snapshot(payload)
    value = metrics.get("value")
    return float(value) if value is not None else None


def _today_iso_prefix() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def bot_stats(bot_id: str, *, run_limit: int = 500, light: bool = False) -> dict[str, Any]:
    store = Store()
    today = _today_iso_prefix()
    runs = store.get_runs(limit=run_limit, bot_id=bot_id)
    total = store.count_runs(bot_id)
    finished = store.count_runs(bot_id, status="finished")
    errors = store.count_runs(bot_id, status="error")
    runs_today = store.count_runs(bot_id, started_on=today)

    actions: Counter[str] = Counter()
    portfolio_series: list[dict[str, Any]] = []
    latest_portfolio: float | None = None
    trades = 0
    last_trade_at: str | None = None

    for run in reversed(runs):
        summary = _parse_summary(run)
        if summary:
            action = str(summary.get("action", "none")).lower()
            actions[action] += 1

        run_pv: float | None = None
        run_ts = run.get("started_at")
        run_metrics: dict[str, Any] | None = None
        run_place_orders = 0
        run_status_trades = 0
        run_trade_ts: str | None = None
        if summary:
            pv = summary.get("portfolio_value")
            if pv is not None:
                try:
                    run_pv = float(pv)
                except (TypeError, ValueError):
                    pass

        if not light:
            events = store.get_events(int(run["id"]))
            for ev in events:
                if ev.get("type") == "tool_call":
                    payload = ev.get("payload") or {}
                    if payload.get("name") == "place_equity_order":
                        run_place_orders += 1
                        run_trade_ts = ev.get("ts") or run_trade_ts
                elif ev.get("type") == "status_log":
                    payload = ev.get("payload") or {}
                    action = str(payload.get("action", "none")).lower()
                    if action in ("buy", "sell"):
                        actions[action] += 1
                        run_status_trades += 1
                        run_trade_ts = ev.get("ts") or run_trade_ts
                elif ev.get("type") == "portfolio_snapshot":
                    payload = ev.get("payload") or {}
                    metrics = _metrics_from_snapshot(payload)
                    if metrics.get("value") is not None:
                        run_pv = float(metrics["value"])
                        run_metrics = metrics
                        run_ts = ev.get("ts") or run_ts
        elif summary and str(summary.get("action", "none")).lower() in ("buy", "sell"):
            action = str(summary.get("action")).lower()
            actions[action] += 1
            run_status_trades = 1
            run_trade_ts = run.get("started_at")

        if run_place_orders:
            trades += run_place_orders
            last_trade_at = run_trade_ts or last_trade_at
        elif run_status_trades:
            trades += run_status_trades
            last_trade_at = run_trade_ts or last_trade_at
        elif summary and str(summary.get("action", "none")).lower() in ("buy", "sell"):
            trades += 1
            last_trade_at = run.get("started_at") or last_trade_at

        if run_pv is not None:
            latest_portfolio = run_pv
            point: dict[str, Any] = {
                "run_id": run["id"],
                "ts": run_ts,
                "value": run_pv,
            }
            if run_metrics:
                for key in ("holdings_value", "unrealized_pl", "position_count"):
                    if run_metrics.get(key) is not None:
                        point[key] = run_metrics[key]
            portfolio_series.append(point)

    success_rate = round(finished / total * 100, 1) if total else 0.0
    last_run = runs[0] if runs else None

    return {
        "total_runs": total,
        "finished_runs": finished,
        "error_runs": errors,
        "runs_today": runs_today,
        "success_rate": success_rate,
        "trades_placed": trades,
        "last_trade_at": last_trade_at,
        "latest_portfolio_value": latest_portfolio,
        "portfolio_series": portfolio_series[-30:],
        "actions": dict(actions),
        "last_run_at": last_run.get("started_at") if last_run else None,
        "last_run_status": last_run.get("status") if last_run else None,
    }


def all_stats() -> dict[str, Any]:
    store = Store()
    bots = store.list_bots()
    per_bot = {b["id"]: bot_stats(b["id"]) for b in bots}
    portfolio_values = [s["latest_portfolio_value"] for s in per_bot.values() if s["latest_portfolio_value"]]
    aggregate = {
        "total_bots": len(bots),
        "total_runs": sum(s["total_runs"] for s in per_bot.values()),
        "runs_today": sum(s["runs_today"] for s in per_bot.values()),
        "finished_runs": sum(s["finished_runs"] for s in per_bot.values()),
        "error_runs": sum(s["error_runs"] for s in per_bot.values()),
        "total_trades": sum(s["trades_placed"] for s in per_bot.values()),
        "combined_portfolio": sum(portfolio_values) if portfolio_values else 0,
        "last_run_status": dict(
            Counter(
                s["last_run_status"]
                for s in per_bot.values()
                if s.get("last_run_status")
            )
        ),
    }
    return {"aggregate": aggregate, "bots": per_bot}
