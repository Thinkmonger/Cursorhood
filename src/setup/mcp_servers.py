from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_STDIO_ENV_KEYS = (
    "PATH",
    "SystemRoot",
    "ComSpec",
    "PATHEXT",
    "PYTHONPATH",
    "PYTHONHOME",
    "VIRTUAL_ENV",
    "APPDATA",
    "LOCALAPPDATA",
    "USERPROFILE",
    "HOME",
    "TEMP",
    "TMP",
    "PROGRAMFILES",
    "SSLKEYLOGFILE",
    "SSL_VERIFY",
)


def python_executable() -> str:
    venv_python = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _mcp_subprocess_env(extra: dict[str, str]) -> dict[str, str]:
    """Minimal subprocess env — avoid passing full os.environ to Cursor MCP."""
    env = {key: os.environ[key] for key in _STDIO_ENV_KEYS if key in os.environ}
    env.update(extra)
    return env


def build_agent_mcp_servers(
    token: str | None,
    *,
    bot_id: str,
    run_id: int,
) -> dict[str, Any]:
    """MCP servers for SDK agent runs. robinhood-trading requires a bot bearer token."""
    from cursor_sdk import StdioMcpServerConfig

    cycle_env = _mcp_subprocess_env(
        {
            "ROBINHOOD_BOT_ID": bot_id,
            "ROBINHOOD_RUN_ID": str(run_id),
        }
    )
    servers: dict[str, Any] = {
        "robinhood-status": StdioMcpServerConfig(
            command=python_executable(),
            args=["-m", "mcp_servers.status.server"],
            cwd=str(PROJECT_ROOT),
            env=cycle_env,
        ),
    }
    if token:
        # Local stdio proxy: Robinhood HTTP MCP returns text-only content but declares
        # outputSchema on every tool; Cursor SDK rejects those as "no structured content".
        servers["robinhood-trading"] = StdioMcpServerConfig(
            command=python_executable(),
            args=["-m", "mcp_servers.trading.server"],
            cwd=str(PROJECT_ROOT),
            env=_mcp_subprocess_env(
                {
                    "ROBINHOOD_MCP_TOKEN": token,
                    "ROBINHOOD_BOT_ID": bot_id,
                    "ROBINHOOD_RUN_ID": str(run_id),
                    "ROBINHOOD_ENABLED_CATEGORIES": ",".join(sorted(_bot_categories(bot_id))),
                }
            ),
        )
    return servers


def _bot_categories(bot_id: str) -> set[str]:
    """Tool categories this bot should see. Keeps unused schemas out of the prompt."""
    from src.trading.mcp_tools import CORE_CATEGORIES, enabled_categories

    try:
        from src.settings.service import SettingsService

        settings = SettingsService(bot_id)
        limits = settings.read_limits()
        app = settings.read_bot_app()
        cats = enabled_categories(
            options=limits.options_enabled,
            crypto=limits.crypto_enabled,
            scanners=app.scanners_enabled,
            asset_class=getattr(limits, "asset_class", None),
        )
        return cats
    except Exception:
        return set(CORE_CATEGORIES)


def runner_trading_ready(token: str | None, via_cursor: bool) -> tuple[bool, str]:
    if token:
        return True, ""
    if via_cursor:
        return False, (
            "Robinhood is connected in Cursor IDE, but automated bot runs need a one-time "
            "login here: Settings → Connect Robinhood (same account)."
        )
    return False, "Robinhood not connected. Settings → Connect Robinhood."
