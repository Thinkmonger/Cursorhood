"""Cursor model catalog — IDE/Cursor-pool models vs third-party API usage."""
from __future__ import annotations

import time
from typing import Any

DEFAULT_CURSOR_MODEL = "composer-2.5"

# Back-compat alias used by settings schema and API responses.
DEFAULT_API_MODEL = DEFAULT_CURSOR_MODEL

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
_VALID_IDS: frozenset[str] = frozenset()

# REST /v0 slugs append these to a real model id. They are variant params, not ids.
_VARIANT_SUFFIXES = (
    "extra-high",
    "xhigh",
    "thinking",
    "minimal",
    "medium",
    "high",
    "fast",
    "none",
    "max",
    "low",
)


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
    """Map a stored slug onto an id Cursor.models.list() accepts."""
    raw = str(model or "").strip()
    if not raw:
        return DEFAULT_CURSOR_MODEL
    if _VALID_IDS and raw in _VALID_IDS:
        return raw
    slug = raw[len("cursor-") :] if raw.startswith("cursor-") else raw
    changed = True
    while changed and slug:
        changed = False
        for token in _VARIANT_SUFFIXES:
            suffix = f"-{token}"
            if slug.endswith(suffix) and len(slug) > len(suffix):
                slug = slug[: -len(suffix)]
                changed = True
                break
    if _VALID_IDS and slug not in _VALID_IDS:
        matches = [
            model_id
            for model_id in _VALID_IDS
            if slug == model_id or slug.startswith(model_id + "-")
        ]
        if matches:
            return max(matches, key=len)
    return slug or DEFAULT_CURSOR_MODEL


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


async def fetch_account_models(api_key: str | None = None) -> dict[str, Any]:
    """List models Cursor.models.list() accepts, grouped by IDE vs third-party API billing."""
    global _CACHE, _CACHE_AT, _VALID_IDS
    if _CACHE is not None and (time.monotonic() - _CACHE_AT) < _CACHE_TTL_SEC:
        return _CACHE

    entries: dict[str, dict[str, str]] = {}
    error: str | None = None
    live = False

    if api_key:
        try:
            from cursor_sdk import Cursor

            for model in Cursor.models.list(api_key=api_key):
                model_id = str(model.id or "").strip()
                if not model_id:
                    continue
                label = str(model.display_name or "").strip() or humanize_model_id(model_id)
                entries[model_id] = {"id": model_id, "label": label, "billing": billing_for(model_id)}
            if entries:
                _VALID_IDS = frozenset(entries)
                live = True
        except Exception as exc:
            error = str(exc)
    else:
        error = "No Cursor API key configured"

    if not entries:
        entries = _fallback_entries()

    payload = _payload(
        entries,
        ok=live,
        error=None if live else error,
    )
    if live:
        _CACHE = payload
        _CACHE_AT = time.monotonic()
    return payload
