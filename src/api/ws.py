from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from src.api.security import is_allowed_origin

logger = logging.getLogger(__name__)

_connections: set[WebSocket] = set()

# Clients treat events as change triggers and then re-fetch over HTTP, reading
# only small scalar fields. Broadcasting a whole portfolio_snapshot pushes
# ~120 KB per cycle to every open tab for no benefit.
_MAX_BROADCAST_VALUE_BYTES = 512


def _project_for_broadcast(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep the small fields the UI renders; drop bulk market/position data."""
    projected: dict[str, Any] = {}
    omitted: list[str] = []
    for key, value in payload.items():
        if value is None or isinstance(value, bool) or isinstance(value, (int, float)):
            projected[key] = value
            continue
        try:
            encoded = json.dumps(value)
        except (TypeError, ValueError):
            omitted.append(key)
            continue
        if len(encoded) <= _MAX_BROADCAST_VALUE_BYTES:
            projected[key] = value
        else:
            omitted.append(key)
    if omitted:
        projected["_omitted"] = sorted(omitted)
    return projected


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
    # Websockets are exempt from CORS, so without this check any page the user
    # has open could stream live positions and portfolio values.
    origin = websocket.headers.get("origin")
    if not is_allowed_origin(origin):
        logger.warning("Rejected websocket from origin %s", origin)
        await websocket.close(code=1008)
        return
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
            "payload": _project_for_broadcast(payload),
        },
    }
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(broadcast(message))
    except RuntimeError:
        pass
    return event_id
