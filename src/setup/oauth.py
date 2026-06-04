from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import threading
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from src.http_client import async_client

from src.auth.tokens import TokenData, TokenStore
from src.db.store import Store
from src.i18n import app_name
from src.paths import get_env

MCP_URL = "https://agent.robinhood.com/mcp/trading"
RESOURCE_METADATA_URL = "https://agent.robinhood.com/.well-known/oauth-protected-resource"
AUTH_SERVER_URL = "https://agent.robinhood.com/.well-known/oauth-authorization-server"
CLIENT_STATE_KEY = "robinhood_oauth_client"
OAUTH_STATE: dict[str, Any] = {}


@dataclass
class OAuthStartResult:
    status: str
    message: str
    auth_url: str | None = None


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


class OAuthFlow:
    def __init__(self) -> None:
        self.callback_port = int(get_env("OAUTH_CALLBACK_PORT", "9876") or "9876")
        self.redirect_uri = f"http://127.0.0.1:{self.callback_port}/callback"
        self.token_store = TokenStore()
        self.store = Store()

    async def discover_metadata(self) -> dict[str, Any]:
        async with async_client(timeout=30, follow_redirects=True) as client:
            resource_resp = await client.get(RESOURCE_METADATA_URL)
            auth_resp = await client.get(AUTH_SERVER_URL)
            metadata: dict[str, Any] = {}
            if resource_resp.status_code == 200:
                metadata.update(resource_resp.json())
            if auth_resp.status_code == 200:
                metadata.update(auth_resp.json())
            if not metadata:
                return {"status": resource_resp.status_code, "hint": "OAuth discovery failed"}
            return metadata

    def _load_client(self) -> dict[str, Any]:
        raw = self.store.get_state(CLIENT_STATE_KEY)
        if raw:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass
        return {"client_id": "robinhood-agentic-bot"}

    def _save_client(self, client: dict[str, Any]) -> None:
        self.store.set_state(CLIENT_STATE_KEY, json.dumps(client))

    async def ensure_client(self, metadata: dict[str, Any]) -> dict[str, Any]:
        client = self._load_client()
        if client.get("client_id") and client.get("client_id") != "robinhood-agentic-bot":
            return client
        registration_endpoint = metadata.get("registration_endpoint")
        if not registration_endpoint:
            return client
        async with async_client(timeout=30) as client_http:
            response = await client_http.post(
                registration_endpoint,
                json={
                    "redirect_uris": [self.redirect_uri],
                    "client_name": app_name(),
                    "token_endpoint_auth_method": "none",
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                },
            )
            if response.status_code >= 400:
                return client
            registered = response.json()
            client = {
                "client_id": registered.get("client_id", client.get("client_id")),
                "client_secret": registered.get("client_secret"),
            }
            self._save_client(client)
            return client

    async def start(self) -> OAuthStartResult:
        metadata = await self.discover_metadata()
        client = await self.ensure_client(metadata)
        OAUTH_STATE["metadata"] = metadata
        OAUTH_STATE["client"] = client
        OAUTH_STATE["status"] = "waiting"
        OAUTH_STATE["error"] = None

        auth_endpoint = metadata.get("authorization_endpoint", "https://robinhood.com/oauth")
        scopes = metadata.get("scopes_supported") or ["internal"]
        scope = scopes[0] if len(scopes) == 1 else " ".join(scopes)

        state = secrets.token_urlsafe(16)
        verifier, challenge = _pkce_pair()
        OAUTH_STATE["state"] = state
        OAUTH_STATE["code_verifier"] = verifier

        params = {
            "response_type": "code",
            "client_id": client["client_id"],
            "redirect_uri": self.redirect_uri,
            "state": state,
            "scope": scope,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        auth_url = f"{auth_endpoint}?{urlencode(params)}"

        thread = threading.Thread(target=self._run_callback_server, daemon=True)
        thread.start()
        webbrowser.open(auth_url)

        return OAuthStartResult(
            status="started",
            message="Browser opened for Robinhood login. Complete auth in the browser.",
            auth_url=auth_url,
        )

    def _run_callback_server(self) -> None:
        flow = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path != "/callback":
                    self.send_response(404)
                    self.end_headers()
                    return
                params = parse_qs(parsed.query)
                code = params.get("code", [None])[0]
                state = params.get("state", [None])[0]
                error = params.get("error", [None])[0]
                if error:
                    OAUTH_STATE["status"] = "error"
                    OAUTH_STATE["error"] = error
                    body = f"<h1>Auth failed</h1><p>{error}</p>"
                elif state != OAUTH_STATE.get("state"):
                    OAUTH_STATE["status"] = "error"
                    OAUTH_STATE["error"] = "Invalid OAuth state"
                    body = "<h1>Invalid state</h1>"
                elif code:
                    asyncio.run(flow._exchange_code(code))
                    body = "<h1>Robinhood connected</h1><p>Return to the bot dashboard.</p>"
                else:
                    body = "<h1>Missing code</h1>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(body.encode())

        server = HTTPServer(("127.0.0.1", self.callback_port), Handler)
        OAUTH_STATE["server_running"] = True
        server.timeout = 1
        for _ in range(300):
            if OAUTH_STATE.get("status") in ("connected", "error"):
                break
            server.handle_request()
        server.server_close()

    async def _exchange_code(self, code: str) -> None:
        metadata = OAUTH_STATE.get("metadata", {})
        client = OAUTH_STATE.get("client") or self._load_client()
        token_url = metadata.get("token_endpoint", "https://api.robinhood.com/oauth2/token/")
        async with async_client(timeout=30) as client_http:
            response = await client_http.post(
                token_url,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                    "client_id": client["client_id"],
                    "code_verifier": OAUTH_STATE.get("code_verifier", ""),
                },
            )
            if response.status_code >= 400:
                OAUTH_STATE["status"] = "error"
                OAUTH_STATE["error"] = response.text
                return
            data = response.json()
            expires_at = None
            if data.get("expires_in"):
                expires_at = (
                    datetime.now(timezone.utc) + timedelta(seconds=int(data["expires_in"]))
                ).isoformat()
            self.token_store.save(
                TokenData(
                    access_token=data["access_token"],
                    refresh_token=data.get("refresh_token"),
                    expires_at=expires_at,
                    token_type=data.get("token_type", "Bearer"),
                )
            )
            OAUTH_STATE["status"] = "connected"

    def get_status(self) -> dict[str, Any]:
        return {
            "oauth_status": OAUTH_STATE.get("status", "idle"),
            "oauth_error": OAUTH_STATE.get("error"),
            "robinhood_connected": self.token_store.is_connected(),
        }

    def disconnect(self) -> None:
        self.token_store.clear()
        OAUTH_STATE.clear()
