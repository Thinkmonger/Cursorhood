"""Cursor model catalog — IDE/Cursor-pool models vs third-party API usage."""
from __future__ import annotations

import time
from typing import Any

from src.http_client import async_client

DEFAULT_CURSOR_MODEL = "composer-2.5"

# Back-compat alias used by settings schema and API responses.
DEFAULT_API_MODEL = DEFAULT_CURSOR_MODEL

CURSOR_MODELS_V0 = "https://api.cursor.com/v0/models"
CURSOR_MODELS_V1 = "https://api.cursor.com/v1/models"

GROUP_IDE = "ide"
GROUP_API = "api"
GROUP_LABELS = {
    GROUP_IDE: "IDE / Cursor models",
    GROUP_API: "API (third-party)",
}

# Shown when the live catalog cannot be fetched (no key or Cursor is unreachable).
_FALLBACK_MODELS: tuple[tuple[str, str], ...] = (
    ("default", "Auto"),
    ("composer-2.5", "Composer 2.5"),
    ("grok-4.6", "Cursor Grok 4.6"),
    ("grok-4.5", "Cursor Grok 4.5"),
)

_CACHE_TTL_SEC = 60.0
_CACHE: dict[str, Any] | None = None
_CACHE_AT = 0.0

_SUBSCRIPTION_MODEL_IDS = frozenset(
    {
        "auto",
        "auto-smart",
        "default",
        "composer",
        "composer-2",
        "composer-2.5",
        "composer-2-fast",
    }
)


def is_subscription_model(model: str) -> bool:
    """True when the model draws from included Cursor/IDE usage instead of third-party API rates."""
    return billing_for(model) == GROUP_IDE


def billing_for(model: str) -> str:
    normalized = model.strip().lower()
    if not normalized:
        return GROUP_IDE
    if normalized in _SUBSCRIPTION_MODEL_IDS:
        return GROUP_IDE
    if normalized.startswith("composer"):
        return GROUP_IDE
    if normalized.startswith("grok") or normalized.startswith("cursor-grok"):
        return GROUP_IDE
    return GROUP_API


def normalize_cursor_model(model: str) -> str:
    return model.strip() or DEFAULT_CURSOR_MODEL


def subscription_model_warning(model: str) -> str | None:
    """Human-readable warning when a model bills against included Cursor/IDE usage."""
    normalized = normalize_cursor_model(model)
    if billing_for(normalized) != GROUP_IDE:
        return None
    return (
        f"Model '{normalized}' uses included Cursor/IDE usage "
        "(Auto, Composer, and Cursor Grok), not third-party API rates."
    )


def humanize_model_id(model_id: str) -> str:
    special = {"gpt": "GPT", "glm": "GLM"}
    parts: list[str] = []
    for raw in model_id.replace("_", "-").split("-"):
        if not raw:
            continue
        key = raw.lower()
        if key in special:
            parts.append(special[key])
        elif key in {"xhigh", "xlarge"}:
            parts.append(raw[0].upper() + raw[1:])
        else:
            parts.append(raw[:1].upper() + raw[1:] if raw[:1].isalpha() else raw)
    return " ".join(parts) or model_id


def _label_for(model_id: str, names: dict[str, str]) -> str:
    if model_id in names:
        return names[model_id]
    best = ""
    for family_id, name in names.items():
        if model_id.startswith(family_id + "-") and len(family_id) > len(best):
            best = family_id
            suffix = humanize_model_id(model_id[len(family_id) + 1 :])
            if suffix:
                return f"{name} / {suffix}"
            return name
    return humanize_model_id(model_id)


def _ide_sort_key(entry: dict[str, str]) -> tuple[int, str]:
    model_id = entry["id"].lower()
    label = entry["label"].lower()
    if model_id in {"default", "auto", "auto-smart"}:
        rank = 0
    elif model_id.startswith("composer"):
        rank = 1
    elif "grok" in model_id:
        rank = 2
    else:
        rank = 3
    return (rank, label)


def _payload(entries: dict[str, dict[str, str]], *, ok: bool, error: str | None = None) -> dict[str, Any]:
    ide = sorted((e for e in entries.values() if e["billing"] == GROUP_IDE), key=_ide_sort_key)
    api = sorted(
        (e for e in entries.values() if e["billing"] == GROUP_API),
        key=lambda e: e["label"].lower(),
    )
    models = [e["id"] for e in ide] + [e["id"] for e in api]
    if DEFAULT_CURSOR_MODEL in models:
        default = DEFAULT_CURSOR_MODEL
    elif models:
        default = models[0]
    else:
        default = DEFAULT_CURSOR_MODEL
    result: dict[str, Any] = {
        "ok": ok,
        "default": default,
        "models": models,
        "subscription_models": [e["id"] for e in ide],
        "groups": [
            {"id": GROUP_IDE, "label": GROUP_LABELS[GROUP_IDE], "billing": GROUP_IDE, "models": ide},
            {"id": GROUP_API, "label": GROUP_LABELS[GROUP_API], "billing": GROUP_API, "models": api},
        ],
    }
    if error:
        result["error"] = error
    return result


def _fallback_entries() -> dict[str, dict[str, str]]:
    entries: dict[str, dict[str, str]] = {}
    for model_id, label in _FALLBACK_MODELS:
        entries[model_id] = {"id": model_id, "label": label, "billing": billing_for(model_id)}
    return entries


def _add(entries: dict[str, dict[str, str]], model_id: str, *, label: str | None, names: dict[str, str]) -> None:
    mid = str(model_id or "").strip()
    if not mid:
        return
    resolved = label or _label_for(mid, names)
    if mid not in entries:
        entries[mid] = {"id": mid, "label": resolved, "billing": billing_for(mid)}
        return
    # Prefer a catalog display name over a slug-derived label.
    current = entries[mid]["label"]
    if label and (current == mid or current == humanize_model_id(mid)):
        entries[mid]["label"] = label


async def fetch_account_models(api_key: str | None = None) -> dict[str, Any]:
    """List models available to the API key, grouped by IDE vs third-party API billing."""
    global _CACHE, _CACHE_AT
    if _CACHE is not None and (time.monotonic() - _CACHE_AT) < _CACHE_TTL_SEC:
        return _CACHE

    entries = _fallback_entries()
    names = {mid: label for mid, label in _FALLBACK_MODELS}
    error: str | None = None
    live = False

    if api_key:
        try:
            async with async_client(timeout=15) as client:
                headers = {"Authorization": f"Bearer {api_key}"}
                v1 = await client.get(CURSOR_MODELS_V1, headers=headers)
                if v1.status_code == 200:
                    for item in (v1.json().get("items") or []):
                        if not isinstance(item, dict):
                            continue
                        model_id = str(item.get("id") or "").strip()
                        label = str(item.get("displayName") or "").strip()
                        if not model_id:
                            continue
                        if label:
                            names[model_id] = label
                        _add(entries, model_id, label=label or None, names=names)
                    live = True
                v0 = await client.get(CURSOR_MODELS_V0, headers=headers)
                if v0.status_code == 200:
                    for item in v0.json().get("models") or []:
                        _add(entries, str(item), label=None, names=names)
                    live = True
                elif not live:
                    error = f"HTTP {v0.status_code}"
        except Exception as exc:
            error = str(exc)

    payload = _payload(
        entries,
        ok=live,
        error=error if api_key else (error or "No Cursor API key configured"),
    )
    if live:
        _CACHE = payload
        _CACHE_AT = time.monotonic()
    return payload
