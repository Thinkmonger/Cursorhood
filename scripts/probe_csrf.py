"""Read-only probe: can a cross-origin, bodyless POST reach a state-changing route?

Uses a nonexistent bot id so the handler runs but has no side effect. A 404
means the request passed body validation and no origin/host check blocked it.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from src.api.app import create_app  # noqa: E402

EVIL = "https://evil.example"
GHOST = "__probe_no_such_bot__"
PORT = 8765


def main() -> None:
    # raise_server_exceptions=False: a 500 still proves the handler body ran.
    client = TestClient(
        create_app(), base_url=f"http://127.0.0.1:{PORT}", raise_server_exceptions=False
    )
    results = []
    good = f"http://127.0.0.1:{PORT}"

    # Positive control: the real UI's same-origin request must still get through.
    r = client.post(f"/api/bots/{GHOST}/pause", headers={"Origin": good})
    results.append({"path": "CONTROL same-origin pause", "status": r.status_code, "origin": good})
    r = client.get("/api/settings", headers={"Origin": good})
    results.append({
        "path": "CONTROL same-origin GET /api/settings",
        "status": r.status_code,
        "xcto": r.headers.get("x-content-type-options"),
        "xfo": r.headers.get("x-frame-options"),
        "referrer": r.headers.get("referrer-policy"),
    })
    static = client.get("/static/app.js")
    results.append({
        "path": "GET /static/app.js",
        "status": static.status_code,
        "cache": static.headers.get("cache-control"),
        "xcto": static.headers.get("x-content-type-options"),
    })
    try:
        with client.websocket_connect("/ws", headers={"Origin": good}):
            ctl_ws = "ACCEPTED"
    except Exception as exc:  # noqa: BLE001
        ctl_ws = f"REJECTED: {type(exc).__name__}"
    results.append({"path": "CONTROL same-origin WS", "status": ctl_ws})

    # Bodyless POSTs: CORS "simple requests" a browser sends without preflight.
    for path in (
        f"/api/bots/{GHOST}/pause",
        f"/api/bots/{GHOST}/resume",
        f"/api/bots/{GHOST}/stop",
        f"/api/bots/{GHOST}/reset-simulation",
    ):
        r = client.post(path, headers={"Origin": EVIL})
        results.append({"path": path, "status": r.status_code, "origin": EVIL})

    # Host header not validated -> DNS rebinding reaches the same routes.
    r = client.get("/api/settings", headers={"Host": "attacker.example", "Origin": EVIL})
    results.append({"path": "GET /api/settings", "status": r.status_code, "host": "attacker.example"})

    # Does any response carry CORS headers (i.e. is CORS configured at all)?
    cors = {k: v for k, v in r.headers.items() if k.lower().startswith("access-control")}
    results.append({"path": "cors_headers_present", "status": len(cors), "headers": cors})

    # WebSocket accepts any Origin?
    try:
        with client.websocket_connect("/ws", headers={"Origin": EVIL}):
            ws = "ACCEPTED"
    except Exception as exc:  # noqa: BLE001
        ws = f"REJECTED: {type(exc).__name__}"
    results.append({"path": "WS /ws", "status": ws, "origin": EVIL})

    for row in results:
        print(row)


if __name__ == "__main__":
    main()
