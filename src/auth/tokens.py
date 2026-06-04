from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import keyring

from src.paths import DATA_DIR, ensure_data_dir

SERVICE_NAME = "robinhood-agentic-bot"
ACCOUNT_NAME = "robinhood-mcp-tokens"
FILE_FALLBACK = DATA_DIR / "tokens.json"


@dataclass
class TokenData:
    access_token: str
    refresh_token: str | None = None
    expires_at: str | None = None
    token_type: str = "Bearer"

    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        try:
            exp = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            return datetime.now(timezone.utc) >= exp
        except ValueError:
            return False


class TokenStore:
    def save(self, tokens: TokenData) -> None:
        payload = json.dumps(asdict(tokens))
        try:
            keyring.set_password(SERVICE_NAME, ACCOUNT_NAME, payload)
        except Exception:
            ensure_data_dir()
            FILE_FALLBACK.write_text(payload, encoding="utf-8")

    def load(self) -> TokenData | None:
        raw = self._read_raw()
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return TokenData(**data)
        except (json.JSONDecodeError, TypeError):
            return None

    def clear(self) -> None:
        try:
            keyring.delete_password(SERVICE_NAME, ACCOUNT_NAME)
        except Exception:
            pass
        if FILE_FALLBACK.exists():
            FILE_FALLBACK.unlink()

    def get_access_token(self) -> str | None:
        tokens = self.load()
        if not tokens:
            return None
        if tokens.is_expired():
            return None
        token = (tokens.access_token or "").strip()
        if not token:
            self.clear()
            return None
        return token

    def is_connected(self) -> bool:
        return self.get_access_token() is not None

    def _read_raw(self) -> str | None:
        try:
            value = keyring.get_password(SERVICE_NAME, ACCOUNT_NAME)
            if value:
                return value
        except Exception:
            pass
        if FILE_FALLBACK.exists():
            return FILE_FALLBACK.read_text(encoding="utf-8")
        return None
