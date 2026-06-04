from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from src.bots.manager import get_bot_manager
from src.db.store import Store
from src.stats.service import all_stats, bot_stats

router = APIRouter(prefix="/api/statistics", tags=["statistics"])


@router.get("")
async def statistics() -> dict[str, Any]:
    store = Store()
    stats = all_stats()
    mgr = get_bot_manager()
    bots_meta = []
    for bot in store.list_bots():
        snap = mgr.bot_snapshot(bot["id"])
        bots_meta.append(
            {
                "id": bot["id"],
                "name": bot["name"],
                "scheduler": snap["scheduler"],
                "stats": bot_stats(bot["id"]),
            }
        )
    return {"aggregate": stats["aggregate"], "bots": bots_meta}
