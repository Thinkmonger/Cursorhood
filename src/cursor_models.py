"""Cursor model helpers — bill agent cycles against API usage, not IDE Composer/Auto quota."""
from __future__ import annotations

from typing import Any

from src.http_client import async_client

DEFAULT_CURSOR_MODEL = "composer-2.5"

# Back-compat alias used by settings schema and API responses.
DEFAULT_API_MODEL = DEFAULT_CURSOR_MODEL

_SUBSCRIPTION_MODEL_IDS = frozenset(
    {
        "auto",
        "composer",
        "composer-2",
        "composer-2.5",
        "composer-2-fast",
    }
)


def is_subscription_model(model: str) -> bool:
    """True when the model bills against Cursor Auto/Composer quota instead of API usage."""
    normalized = model.strip().lower()
    if not normalized:
        return True
    if normalized in _SUBSCRIPTION_MODEL_IDS:
        return True
    return normalized.startswith("composer-")


def normalize_cursor_model(model: str) -> str:
    return model.strip() or DEFAULT_CURSOR_MODEL


def subscription_model_warning(model: str) -> str | None:
    """Human-readable warning when a model bills against Auto/Composer quota."""
    normalized = normalize_cursor_model(model)
    if not is_subscription_model(normalized):
        return None
    return (
        f"Model '{normalized}' bills against Cursor Auto/Composer quota, not API usage."
    )


async def fetch_account_models(api_key: str) -> dict[str, Any]:
    """List models available to the API key; subscription-only models are flagged separately."""
    try:
        async with async_client(timeout=15) as client:
            response = await client.get(
                "https://api.cursor.com/v0/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if response.status_code != 200:
                return {
                    "ok": False,
                    "error": f"HTTP {response.status_code}",
                    "default": DEFAULT_CURSOR_MODEL,
                    "models": [],
                }
            raw = response.json().get("models") or []
            models = [str(item) for item in raw]
            if DEFAULT_CURSOR_MODEL in models:
                default = DEFAULT_CURSOR_MODEL
            elif models:
                default = models[0]
            else:
                default = DEFAULT_CURSOR_MODEL
            return {
                "ok": True,
                "default": default,
                "models": models,
                "subscription_models": [m for m in models if is_subscription_model(m)],
            }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "default": DEFAULT_CURSOR_MODEL,
            "models": [],
        }
