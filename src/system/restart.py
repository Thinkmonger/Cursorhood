"""Restart the bot server process (spawn replacement, then exit)."""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
_shutdown: Callable[[], Awaitable[None]] | None = None


def set_shutdown_callback(callback: Callable[[], Awaitable[None]]) -> None:
    global _shutdown
    _shutdown = callback


def spawn_replacement_server() -> None:
    cmd = [sys.executable, "-m", "runner.main"]
    kwargs: dict = {"cwd": str(ROOT), "close_fds": True}
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    subprocess.Popen(cmd, **kwargs)
    logger.info("Spawned replacement server: %s", " ".join(cmd))


async def schedule_restart(delay_sec: float = 1.0) -> None:
    await asyncio.sleep(delay_sec)
    spawn_replacement_server()
    if _shutdown is not None:
        await _shutdown()
    else:
        os._exit(0)
