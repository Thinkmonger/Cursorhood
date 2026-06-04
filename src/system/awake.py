from __future__ import annotations

import atexit
import logging
import sys

logger = logging.getLogger(__name__)

_active = False


def prevent_sleep() -> None:
    """Inhibit system sleep (not display idle) while the bot process is running."""
    global _active
    if _active:
        return

    if sys.platform == "win32":
        import ctypes

        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        _active = True
        atexit.register(allow_sleep)
        logger.info(
            "Sleep prevention enabled — system stays awake; monitors and screensaver may still turn off"
        )
        return

    logger.debug("Sleep prevention is not configured for platform %s", sys.platform)


def allow_sleep() -> None:
    """Restore normal sleep behavior."""
    global _active
    if not _active:
        return

    if sys.platform == "win32":
        import ctypes

        ES_CONTINUOUS = 0x80000000
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        _active = False
        logger.info("Sleep prevention disabled — normal sleep behavior restored")
        try:
            atexit.unregister(allow_sleep)
        except Exception:
            pass
