"""Cursor account info and local agent usage for the dashboard footer."""
from __future__ import annotations

import time
from typing import Any

from src.http_client import async_client
from src.settings.service import SettingsService

_CURSOR_CACHE: dict[str, Any] = {"at": 0.0, "data": None}
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
        "id": "yahoo",
        "label": "Yahoo Finance",
        "url": "https://finance.yahoo.com",
    },
    {
        "id": "massive",
        "label": "Massive",
        "url": "https://massive.com/docs/rest/quickstart",
    },
]

_QUOTA_NOTE = (
    "Remaining Cursor plan % is not available with a user API key. "
    "Check usage at cursor.com/settings."
)
_QUOTA_URL = "https://cursor.com/settings"


def _mask_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return email
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        masked_local = local[0] + "*"
    else:
        masked_local = local[:2] + "***"
    return f"{masked_local}@{domain}"


def _intish(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _floatish(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _walk_token_fields(payload: Any) -> tuple[int, int]:
    prompt = 0
    completion = 0
    seen: list[Any] = []

    def consider(obj: Any, depth: int = 0) -> None:
        nonlocal prompt, completion
        if obj is None or depth > 5:
            return
        if isinstance(obj, dict):
            for key, value in obj.items():
                lower = str(key).lower()
                n = _intish(value)
                if n is None:
                    if isinstance(value, (dict, list)):
                        consider(value, depth + 1)
                    continue
                if any(
                    part in lower
                    for part in ("prompt", "inputtoken", "input_token")
                ):
                    prompt += n
                elif any(
                    part in lower
                    for part in ("completion", "outputtoken", "output_token")
                ):
                    completion += n
                elif lower in ("total_tokens", "totaltokens") and not prompt and not completion:
                    prompt += n
            return
        if isinstance(obj, list):
            for item in obj[:20]:
                consider(item, depth + 1)

    consider(payload)
    seen.append(payload)
    return prompt, completion


def _local_usage_stats() -> dict[str, Any]:
    from src.db.store import Store

    return Store().usage_counters()


async def _fetch_cursor_account(api_key: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": False,
        "quota_available": False,
        "quota_note": _QUOTA_NOTE,
        "quota_url": _QUOTA_URL,
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with async_client(timeout=15) as client:
            me_resp = await client.get("https://api.cursor.com/v1/me", headers=headers)
            if me_resp.status_code == 200:
                me = me_resp.json()
                out["ok"] = True
                out["api_key_name"] = me.get("apiKeyName")
                out["user_email"] = _mask_email(me.get("userEmail"))
                out["user_id"] = me.get("userId")

            agents_resp = await client.get(
                "https://api.cursor.com/v1/agents", headers=headers
            )
            items: list[Any] = []
            if agents_resp.status_code == 200:
                payload = agents_resp.json()
                items = payload.get("items") or payload.get("agents") or []
                if not isinstance(items, list):
                    items = []
                out["cloud_agents"] = len(items)
            else:
                out["cloud_agents"] = None

            # Documented per-agent usage (Cloud Agents). User keys have no remaining-plan field.
            cloud_prompt = 0
            cloud_completion = 0
            usage_ok = False
            for item in items[:5]:
                if not isinstance(item, dict):
                    continue
                agent_id = item.get("id") or item.get("agentId")
                if not agent_id:
                    continue
                usage_resp = await client.get(
                    f"https://api.cursor.com/v1/agents/{agent_id}/usage",
                    headers=headers,
                )
                if usage_resp.status_code != 200:
                    continue
                usage_ok = True
                p, c = _walk_token_fields(usage_resp.json())
                cloud_prompt += p
                cloud_completion += c

            if usage_ok:
                out["cloud_agent_tokens"] = cloud_prompt + cloud_completion
                out["cloud_agent_prompt_tokens"] = cloud_prompt
                out["cloud_agent_completion_tokens"] = cloud_completion

            # Best-effort user-key spend/usage routes. 401/404 → omit quota, never scrape cookies.
            for path in ("/v1/me/usage", "/v1/usage", "/v1/spend"):
                try:
                    resp = await client.get(
                        f"https://api.cursor.com{path}", headers=headers
                    )
                except Exception:
                    continue
                if resp.status_code != 200:
                    continue
                body = resp.json()
                remaining = None
                limit = None
                if isinstance(body, dict):
                    remaining = (
                        _floatish(body.get("remaining"))
                        or _floatish(body.get("percentRemaining"))
                        or _floatish(body.get("remaining_percent"))
                    )
                    limit = _floatish(body.get("limit") or body.get("included"))
                    used = _floatish(body.get("used") or body.get("spend"))
                    if remaining is None and limit and used is not None and limit > 0:
                        remaining = max(0.0, (1 - used / limit) * 100)
                if remaining is not None:
                    out["quota_available"] = True
                    out["quota_percent_remaining"] = round(float(remaining), 1)
                    out["quota_note"] = None
                    break

            out["billing_available"] = bool(out.get("quota_available"))
            if not out["billing_available"]:
                out["billing_note"] = _QUOTA_NOTE
    except Exception as exc:
        out["error"] = str(exc)
    return out


async def _cached_cursor_account(api_key: str | None) -> dict[str, Any]:
    now = time.time()
    cached = _CURSOR_CACHE.get("data")
    if cached is not None and (now - float(_CURSOR_CACHE["at"])) < _CACHE_TTL_SECONDS:
        return cached
    cursor: dict[str, Any] = {
        "configured": bool(api_key),
        "ok": False,
        "quota_available": False,
        "quota_note": _QUOTA_NOTE,
        "quota_url": _QUOTA_URL,
    }
    if api_key:
        cursor = {**cursor, **await _fetch_cursor_account(api_key)}
        cursor["configured"] = True
    _CURSOR_CACHE["at"] = now
    _CURSOR_CACHE["data"] = cursor
    return cursor


async def get_footer_info() -> dict[str, Any]:
    """Footer payload: tech stack, live local usage, cached Cursor account summary."""
    local = _local_usage_stats()
    settings = SettingsService("default")
    api_key = settings.get_cursor_api_key()
    cursor = await _cached_cursor_account(api_key)
    return {
        "technologies": TECHNOLOGIES,
        "local": local,
        "cursor": cursor,
        "as_of": time.time(),
    }
