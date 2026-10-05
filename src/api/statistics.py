from __future__ import annotations

from collections import Counter
from typing import Any

from fastapi import APIRouter

from src.bots.manager import get_bot_manager
from src.db.store import Store, utc_now
from src.stats.cursor_usage import get_footer_info
from src.stats.service import all_stats

router = APIRouter(prefix="/api/statistics", tags=["statistics"])


def _scheduler_bucket(sched: dict[str, Any]) -> str:
    if sched.get("running") or sched.get("running_cycle"):
        return "running_cycle"
    if not sched.get("started"):
        return "stopped"
    if sched.get("paused"):
        return "paused"
    return "active"


@router.get("")
async def statistics() -> dict[str, Any]:
    store = Store()
    stats = all_stats()
    usage = store.usage_counters()
    footer = await get_footer_info()
    mgr = get_bot_manager()
    bots_meta = []
    scheduler_mix: Counter[str] = Counter()
    for bot in store.list_bots():
        snap = mgr.bot_snapshot(bot["id"], include_stats=False)
        sched = snap.get("scheduler") or {}
        scheduler_mix[_scheduler_bucket(sched)] += 1
        bots_meta.append(
            {
                "id": bot["id"],
                "name": bot["name"],
                "asset_class": snap.get("asset_class"),
                "simulation_mode": snap.get("simulation_mode"),
                "scheduler": sched,
                "stats": stats["bots"].get(bot["id"]) or {},
            }
        )
    aggregate = {
        **stats["aggregate"],
        "scheduler": dict(scheduler_mix),
        "usage": {**usage, "cursor": footer.get("cursor") or {}},
    }
    return {"aggregate": aggregate, "bots": bots_meta, "as_of": utc_now()}
