from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

_connections: set[WebSocket] = set()


async def broadcast(message: dict[str, Any]) -> None:
    dead: list[WebSocket] = []
    payload = json.dumps(message)
    for ws in list(_connections):
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _connections.discard(ws)


async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    _connections.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _connections.discard(websocket)


def emit_agent_event(
    run_id: int,
    bot_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> int:
    from src.db.store import Store

    store = Store()
    event_id = store.add_event(run_id, event_type, payload)
    message = {
        "type": "agent_event",
        "bot_id": bot_id,
        "run_id": run_id,
        "event": {
            "id": event_id,
            "type": event_type,
            "payload": payload,
        },
    }
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(broadcast(message))
    except RuntimeError:
        pass
    return event_id
