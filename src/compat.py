"""Compatibility shims for older Python versions (cursor-sdk needs 3.12+ on Windows)."""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import Any, Mapping

logger = logging.getLogger(__name__)


def ensure_os_blocking_api() -> None:
    """Provide os.get_blocking/set_blocking when missing (Python < 3.12)."""
    if hasattr(os, "get_blocking") and hasattr(os, "set_blocking"):
        return

    if sys.platform == "win32":
        def get_blocking(fd: int) -> bool:
            return True

        def set_blocking(fd: int, blocking: bool) -> None:
            return None

        os.get_blocking = get_blocking  # type: ignore[attr-defined]
        os.set_blocking = set_blocking  # type: ignore[attr-defined]
        return

    import fcntl

    def get_blocking(fd: int) -> bool:
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        return not (flags & os.O_NONBLOCK)

    def set_blocking(fd: int, blocking: bool) -> None:
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        if blocking:
            flags &= ~os.O_NONBLOCK
        else:
            flags |= os.O_NONBLOCK
        fcntl.fcntl(fd, fcntl.F_SETFL, flags)

    os.get_blocking = get_blocking  # type: ignore[attr-defined]
    os.set_blocking = set_blocking  # type: ignore[attr-defined]


def _read_discovery_threaded(process: Any, timeout: float) -> Mapping[str, Any]:
    """Windows/Python 3.11-safe stderr discovery (selector+os.read fails on pipes)."""
    from cursor_sdk._bridge import READY_LINE_PREFIX, parse_discovery_line
    from cursor_sdk.errors import CursorSDKError

    if process.stderr is None:
        raise CursorSDKError("Bridge process stderr is unavailable")

    holder: dict[str, Any] = {}
    stderr_lines: list[str] = []

    def reader() -> None:
        try:
            for line in process.stderr:
                stderr_lines.append(line)
                discovery = parse_discovery_line(line)
                if discovery is not None:
                    holder["discovery"] = discovery
                    return
        except Exception as exc:
            holder["error"] = exc

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if "discovery" in holder:
            return holder["discovery"]
        if "error" in holder:
            raise holder["error"]  # type: ignore[misc]
        exit_code = process.poll()
        if exit_code is not None:
            break
        time.sleep(0.05)

    if "discovery" in holder:
        return holder["discovery"]
    if "error" in holder:
        raise holder["error"]  # type: ignore[misc]

    exit_code = process.poll()
    if exit_code is not None:
        raise CursorSDKError(
            f"Bridge exited before discovery with status {exit_code}: "
            + "".join(stderr_lines)
        )
    raise CursorSDKError("Timed out waiting for bridge discovery")


def _ssl_verify_enabled() -> bool:
    value = os.environ.get("SSL_VERIFY", "true").strip().lower()
    return value not in ("0", "false", "no", "off")


def _ssl_keylogfile_is_usable(path: str) -> bool:
    """True when Python can assign SSLKEYLOGFILE (normal file paths on any OS)."""
    import ssl

    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.keylog_filename = path
        return True
    except (PermissionError, OSError, ValueError):
        return False


def _sanitize_ssl_keylogfile_env() -> None:
    """Drop SSLKEYLOGFILE only when Python cannot open it (e.g. AV filter devices)."""
    keylog = os.environ.get("SSLKEYLOGFILE")
    if not keylog or _ssl_keylogfile_is_usable(keylog):
        return
    os.environ.pop("SSLKEYLOGFILE", None)
    logger.info(
        "Removed unusable SSLKEYLOGFILE path (breaks Python SSL for Cursor SDK): %s",
        keylog,
    )


def _patch_bridge_subprocess_env() -> None:
    """Pass SSL workaround to the Node bridge when Python HTTPS verify is disabled."""
    try:
        import cursor_sdk._bridge as bridge_mod
    except Exception:
        return
    if getattr(bridge_mod, "_rh_bridge_env_patched", False):
        return
    original = bridge_mod._bridge_subprocess_env

    def patched_env() -> Mapping[str, str]:
        env = dict(original())
        if not _ssl_verify_enabled():
            env["NODE_TLS_REJECT_UNAUTHORIZED"] = "0"
        return env

    bridge_mod._bridge_subprocess_env = patched_env  # type: ignore[attr-defined]
    bridge_mod._rh_bridge_env_patched = True


def patch_cursor_sdk_for_python311() -> None:
    """Apply cursor-sdk patches required on Python 3.11 (especially Windows)."""
    _sanitize_ssl_keylogfile_env()
    ensure_os_blocking_api()
    _patch_bridge_subprocess_env()
    if sys.version_info >= (3, 12):
        return
    try:
        import cursor_sdk._bridge as bridge_mod

        bridge_mod._read_discovery = _read_discovery_threaded  # type: ignore[attr-defined]
    except Exception:
        pass
