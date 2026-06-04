"""Cursor account info and local agent usage for the dashboard footer."""
from __future__ import annotations

import time
from typing import Any

from src.http_client import async_client
from src.settings.service import SettingsService
from src.stats.service import all_stats

_CACHE: dict[str, Any] = {"at": 0.0, "data": None}
_CACHE_TTL_SECONDS = 90

TECHNOLOGIES: list[dict[str, str]] = [
    {"id": "python", "label": "Python", "url": "https://www.python.org/"},
    {"id": "fastapi", "label": "FastAPI", "url": "https://fastapi.tiangolo.com/"},
    {"id": "uvicorn", "label": "Uvicorn", "url": "https://www.uvicorn.org/"},
    {"id": "cursor", "label": "Cursor SDK", "url": "https://cursor.com/docs"},
    {"id": "mcp", "label": "MCP", "url": "https://modelcontextprotocol.io/"},
    {"id": "bootstrap", "label": "Bootstrap 5", "url": "https://getbootstrap.com/"},
    {"id": "chartjs", "label": "Chart.js", "url": "https://www.chartjs.org/"},
    {"id": "sqlite", "label": "SQLite", "url": "https://www.sqlite.org/"},
    {
        "id": "robinhood",
        "label": "Robinhood Agentic",
        "url": "https://robinhood.com/us/en/agentic-trading",
    },
    {
        "id": "massive",
        "label": "Massive",
        "url": "https://massive.com/docs/rest/quickstart",
    },
]


def _mask_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return email
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        masked_local = local[0] + "*"
    else:
        masked_local = local[:2] + "***"
    return f"{masked_local}@{domain}"


def _local_usage_stats() -> dict[str, Any]:
    from src.db.store import Store

    aggregate = all_stats()["aggregate"]
    store = Store()
    tool_calls = 0
    cursor_linked_runs = 0
    with store.connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM agent_events WHERE type = 'tool_call'"
        ).fetchone()
        tool_calls = int(row["n"]) if row else 0
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM agent_runs WHERE cursor_run_id IS NOT NULL AND cursor_run_id != ''"
        ).fetchone()
        cursor_linked_runs = int(row["n"]) if row else 0

    return {
        "total_runs": aggregate.get("total_runs", 0),
        "runs_today": aggregate.get("runs_today", 0),
        "total_trades": aggregate.get("total_trades", 0),
        "total_bots": aggregate.get("total_bots", 0),
        "tool_calls": tool_calls,
        "cursor_linked_runs": cursor_linked_runs,
    }


async def _fetch_cursor_account(api_key: str) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": False}
    try:
        async with async_client(timeout=15) as client:
            me_resp = await client.get(
                "https://api.cursor.com/v1/me",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if me_resp.status_code == 200:
                me = me_resp.json()
                out["ok"] = True
                out["api_key_name"] = me.get("apiKeyName")
                out["user_email"] = _mask_email(me.get("userEmail"))
                out["user_id"] = me.get("userId")

            agents_resp = await client.get(
                "https://api.cursor.com/v1/agents",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if agents_resp.status_code == 200:
                items = agents_resp.json().get("items") or []
                out["cloud_agents"] = len(items)
            else:
                out["cloud_agents"] = None

            out["billing_available"] = False
            out["billing_note"] = (
                "Detailed Cursor spend requires an Admin API key (Enterprise team)."
            )
    except Exception as exc:
        out["error"] = str(exc)
    return out


async def get_footer_info() -> dict[str, Any]:
    """Footer payload: tech stack, local usage, and Cursor account summary."""
    now = time.time()
    if _CACHE["data"] is not None and (now - float(_CACHE["at"])) < _CACHE_TTL_SECONDS:
        return _CACHE["data"]

    local = _local_usage_stats()
    settings = SettingsService("default")
    api_key = settings.get_cursor_api_key()
    cursor: dict[str, Any] = {
        "configured": bool(api_key),
        "ok": False,
    }
    if api_key:
        cursor = {**cursor, **await _fetch_cursor_account(api_key)}

    payload = {
        "technologies": TECHNOLOGIES,
        "local": local,
        "cursor": cursor,
        "as_of": time.time(),
    }
    _CACHE["at"] = now
    _CACHE["data"] = payload
    return payload
