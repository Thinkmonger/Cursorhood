from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from src.db.store import Store
from src.settings.schema import BotAppConfig, LimitsConfig, StrategyUpdate
from src.settings.service import SettingsService

router = APIRouter(prefix="/api/bots/{bot_id}/settings", tags=["bot-settings"])


def _settings(bot_id: str) -> SettingsService:
    if not Store().get_bot(bot_id):
        raise HTTPException(status_code=404, detail="Bot not found")
    return SettingsService(bot_id)


@router.get("")
async def get_bot_settings(bot_id: str) -> dict[str, Any]:
    store = Store()
    bot = store.get_bot(bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")
    return {
        **_settings(bot_id).to_bot_settings_dict(),
        "name": bot["name"],
    }


@router.put("/strategy")
async def update_strategy(bot_id: str, body: StrategyUpdate) -> dict[str, bool]:
    _settings(bot_id).write_strategy(body.content)
    return {"saved": True}


@router.put("/limits")
async def update_limits(bot_id: str, body: LimitsConfig) -> dict[str, bool]:
    _settings(bot_id).write_limits(body)
    return {"saved": True}


@router.put("/app")
async def update_app(bot_id: str, body: BotAppConfig) -> dict[str, bool]:
    _settings(bot_id).write_bot_app(body)
    return {"saved": True}
