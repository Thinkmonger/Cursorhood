"""Isolate which layer denies a legitimate same-origin websocket."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, WebSocket  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.middleware.trustedhost import TrustedHostMiddleware  # noqa: E402

from src.api.security import OriginGuardMiddleware, allowed_hosts  # noqa: E402

GOOD = "http://127.0.0.1:8765"


def build(with_host: bool, with_origin: bool) -> FastAPI:
    app = FastAPI()

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_text("hello")

    if with_origin:
        app.add_middleware(OriginGuardMiddleware)
    if with_host:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts())
    return app


def attempt(label: str, app: FastAPI) -> None:
    client = TestClient(app, base_url=GOOD, raise_server_exceptions=False)
    try:
        with client.websocket_connect("/ws", headers={"Origin": GOOD}) as ws:
            print(f"{label:38} -> ACCEPTED {ws.receive_text()!r}")
    except Exception as exc:  # noqa: BLE001
        detail = getattr(getattr(exc, "response", None), "status_code", "")
        print(f"{label:38} -> REJECTED {type(exc).__name__} {detail}")


if __name__ == "__main__":
    attempt("bare app", build(False, False))
    attempt("+ TrustedHostMiddleware", build(True, False))
    attempt("+ OriginGuardMiddleware", build(False, True))
    attempt("+ both", build(True, True))
