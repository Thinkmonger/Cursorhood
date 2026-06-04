"""UI copy and localization helpers (default locale: English)."""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from src.paths import PROJECT_ROOT

DEFAULT_LOCALE = "en"
_LOCALES_DIR = PROJECT_ROOT / "locales"


@lru_cache(maxsize=8)
def load_locale(locale: str = DEFAULT_LOCALE) -> dict[str, Any]:
    path = _LOCALES_DIR / f"{locale}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def t(key: str, locale: str = DEFAULT_LOCALE, **params: Any) -> str:
    parts = key.split(".")
    value: Any = load_locale(locale)
    for part in parts:
        if not isinstance(value, dict):
            return key
        value = value.get(part)
        if value is None:
            return key
    if not isinstance(value, str):
        return key
    if not params:
        return value
    for name, replacement in params.items():
        value = value.replace(f"{{{name}}}", str(replacement))
    return value


def app_name(locale: str = DEFAULT_LOCALE) -> str:
    return t("app.name", locale)
