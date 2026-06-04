from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from src.db.migrate import DEFAULT_BOT_ID
from src.trading.profile_gate import (
    acknowledge_profile_gate,
    clear_profile_acknowledgement,
    get_profile_gate_status,
)

router = APIRouter(prefix="/api/trading", tags=["trading"])


@router.get("/profile-gate")
async def profile_gate_status(bot_id: str = DEFAULT_BOT_ID) -> dict[str, Any]:
    return get_profile_gate_status(bot_id=bot_id)


@router.post("/profile-gate/acknowledge")
async def profile_gate_acknowledge(bot_id: str = DEFAULT_BOT_ID) -> dict[str, Any]:
    acknowledge_profile_gate(bot_id)
    return get_profile_gate_status(bot_id=bot_id)


@router.post("/profile-gate/reset")
async def profile_gate_reset(bot_id: str = DEFAULT_BOT_ID) -> dict[str, Any]:
    clear_profile_acknowledgement(bot_id)
    return get_profile_gate_status(bot_id=bot_id)
