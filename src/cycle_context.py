"""Per-bot active cycle context for MCP tools (survives stdio env reuse)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from src.paths import PROJECT_ROOT

_CONTEXT_DIR = PROJECT_ROOT / "data" / "cycle_context"


def _context_path(bot_id: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in bot_id)
    return _CONTEXT_DIR / f"{safe}.json"


def publish_cycle_context(bot_id: str, run_id: int) -> None:
    _CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"bot_id": bot_id, "run_id": run_id}
    _context_path(bot_id).write_text(json.dumps(payload), encoding="utf-8")


def clear_cycle_context(bot_id: str) -> None:
    path = _context_path(bot_id)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def resolve_cycle_context() -> tuple[int | None, str | None, str]:
    """Return (run_id, bot_id, source)."""
    from src.db.migrate import DEFAULT_BOT_ID
    from src.db.store import Store

    env_run = os.environ.get("ROBINHOOD_RUN_ID", "").strip()
    env_bot = os.environ.get("ROBINHOOD_BOT_ID", "").strip()
    if env_run and env_bot:
        try:
            return int(env_run), env_bot, "env"
        except ValueError:
            pass

    store = Store()
    if _CONTEXT_DIR.is_dir():
        candidates: list[tuple[float, dict[str, Any]]] = []
        for path in _CONTEXT_DIR.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError):
                continue
            if not isinstance(data, dict):
                continue
            candidates.append((path.stat().st_mtime, data))
        for _, data in sorted(candidates, key=lambda row: row[0], reverse=True):
            bot_id = str(data.get("bot_id") or "").strip()
            run_id_raw = data.get("run_id")
            if not bot_id or run_id_raw is None:
                continue
            try:
                run_id = int(run_id_raw)
            except (TypeError, ValueError):
                continue
            run = store.get_run(run_id, bot_id=bot_id)
            if run and run.get("status") == "running":
                return run_id, bot_id, "file"

    if env_bot:
        active = store.get_active_run(bot_id=env_bot)
        if active:
            return int(active["id"]), active.get("bot_id", env_bot), "active_bot"

    active = store.get_active_run()
    if active:
        return int(active["id"]), active.get("bot_id", DEFAULT_BOT_ID), "active_global"

    return None, None, "none"
