from __future__ import annotations

import asyncio

from fastapi import APIRouter

from src.stats.cursor_usage import get_footer_info
from src.system.restart import schedule_restart

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/footer")
async def footer_info() -> dict[str, object]:
    return await get_footer_info()


@router.post("/restart")
async def restart_server() -> dict[str, str]:
    asyncio.create_task(schedule_restart())
    return {"ok": "true", "message": "Restarting bot server…"}
