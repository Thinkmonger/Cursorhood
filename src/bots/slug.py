from __future__ import annotations

import re
import unicodedata


def slugify(name: str, *, existing: set[str] | None = None) -> str:
    text = unicodedata.normalize("NFKD", name.strip().lower())
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    base = text or "bot"
    if existing is None or base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"
