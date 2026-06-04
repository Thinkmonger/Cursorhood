from __future__ import annotations

import json
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

mcp = FastMCP("robinhood-status")
_last_cycle: dict[str, dict] = {}


@mcp.tool()
def log_event(summary_json: str) -> str:
    """Log a structured JSON summary for the dashboard and Agent View."""
    try:
        data = json.loads(summary_json)
    except json.JSONDecodeError:
        data = {"raw": summary_json}
    try:
        from src.api.ws import emit_agent_event
        from src.cycle_context import resolve_cycle_context
        from src.simulation.ledger import apply_status_log, is_simulation_mode

        run_id, bot_id, _ = resolve_cycle_context()
        if run_id is None or not bot_id:
            return "logged"

        _last_cycle[bot_id] = data
        event_id = emit_agent_event(run_id, bot_id, "status_log", data)
        if is_simulation_mode(bot_id):
            apply_status_log(bot_id, run_id, data, event_id=event_id)
    except Exception:
        pass
    return "logged"


@mcp.tool()
def get_last_cycle() -> str:
    """Return the last cycle summary JSON."""
    try:
        from src.cycle_context import resolve_cycle_context
        from src.db.migrate import DEFAULT_BOT_ID

        _, bot_id, _ = resolve_cycle_context()
        if not bot_id:
            bot_id = DEFAULT_BOT_ID
        return json.dumps(_last_cycle.get(bot_id) or {})
    except Exception:
        return json.dumps({})


if __name__ == "__main__":
    mcp.run()
