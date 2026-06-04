from __future__ import annotations

import time
from typing import Any

from src.auth.tokens import TokenStore
from src.db.store import Store
from src.http_client import async_client
from src.settings.service import SettingsService
from src.setup.mcp_client import MCPClient, set_cursor_mcp_mode, use_cursor_mcp_mode
from src.setup.oauth import OAuthFlow

_CURSOR_KEY_CACHE: dict[str, Any] | None = None
_CURSOR_KEY_CACHE_AT: float = 0.0
_CURSOR_KEY_TTL_SEC = 300.0


class SetupState:
    def __init__(self) -> None:
        self.store = Store()
        self.settings = SettingsService()
        self.oauth = OAuthFlow()
        self.tokens = TokenStore()

    def status(self) -> dict[str, Any]:
        oauth = self.oauth.get_status()
        cursor_key = self.settings.get_cursor_api_key()
        via_cursor = use_cursor_mcp_mode()
        has_token = self.tokens.is_connected()
        return {
            "setup_complete": self.store.is_setup_complete(),
            "cursor_api_key_set": bool(cursor_key),
            "cursor_api_key_valid": None,
            "robinhood_via_cursor": via_cursor,
            "robinhood_has_token": has_token,
            **oauth,
            "robinhood_connected": has_token,
        }

    async def validate_cursor_key(self, api_key: str | None = None) -> dict[str, Any]:
        global _CURSOR_KEY_CACHE, _CURSOR_KEY_CACHE_AT
        key = api_key or self.settings.get_cursor_api_key()
        if not key:
            return {"valid": False, "error": "No API key configured"}
        if (
            not api_key
            and _CURSOR_KEY_CACHE is not None
            and (time.monotonic() - _CURSOR_KEY_CACHE_AT) < _CURSOR_KEY_TTL_SEC
        ):
            return _CURSOR_KEY_CACHE
        try:
            async with async_client(timeout=15) as client:
                response = await client.get(
                    "https://api.cursor.com/v0/models",
                    headers={"Authorization": f"Bearer {key}"},
                )
                if response.status_code == 200:
                    result = {"valid": True}
                else:
                    result = {"valid": False, "error": f"HTTP {response.status_code}"}
        except Exception as exc:
            err = str(exc)
            if "CERTIFICATE_VERIFY_FAILED" in err or "certificate verify failed" in err.lower():
                result = {
                    "valid": True,
                    "warning": "Could not verify SSL locally, but your key may still work. "
                    "Set SSL_VERIFY=false in .env if needed.",
                }
            else:
                result = {"valid": False, "error": err}
        if not api_key:
            _CURSOR_KEY_CACHE = result
            _CURSOR_KEY_CACHE_AT = time.monotonic()
        return result

    async def test_mcp(self) -> dict[str, Any]:
        client = MCPClient()
        result = await client.test_connection()
        if not result.get("ok"):
            err = str(result.get("error", ""))
            if "CERTIFICATE_VERIFY_FAILED" in err or "certificate verify failed" in err.lower():
                return {
                    "ok": False,
                    "step": "ssl",
                    "error": err,
                    "hint": "Add SSL_VERIFY=false to .env, then retry.",
                }
        return result

    async def test_massive(self, api_key: str | None = None) -> dict[str, Any]:
        from src.trading.massive_client import test_connection

        key = api_key or self.settings.get_massive_api_key()
        result = await test_connection(key)
        if not result.get("ok"):
            err = str(result.get("error", ""))
            if "CERTIFICATE_VERIFY_FAILED" in err or "certificate verify failed" in err.lower():
                return {
                    "ok": False,
                    "step": "ssl",
                    "error": err,
                    "hint": "Add SSL_VERIFY=false to .env, then retry.",
                }
        return result

    def enable_cursor_mcp_mode(self) -> None:
        set_cursor_mcp_mode(True)
