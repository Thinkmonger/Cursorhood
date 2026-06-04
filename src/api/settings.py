from __future__ import annotations



from typing import Any



from fastapi import APIRouter, HTTPException

from pydantic import BaseModel



from src.cursor_models import DEFAULT_API_MODEL, fetch_account_models
from src.settings.service import SettingsService

from src.setup.oauth import OAuthFlow

from src.setup.state import SetupState



router = APIRouter(prefix="/api/settings", tags=["settings"])

settings = SettingsService()

oauth = OAuthFlow()

setup = SetupState()





class ConnectionsBody(BaseModel):

    cursor_api_key: str


class MassiveKeyBody(BaseModel):

    massive_api_key: str





@router.get("")

async def get_settings() -> dict[str, Any]:

    return settings.to_connections_dict()





@router.get("/cursor/models")
async def list_cursor_models() -> dict[str, Any]:
    api_key = settings.get_cursor_api_key()
    if not api_key:
        return {
            "ok": False,
            "error": "No Cursor API key configured",
            "default": DEFAULT_API_MODEL,
            "models": [],
        }
    return await fetch_account_models(api_key)


@router.put("/connections")

async def update_connections(body: ConnectionsBody) -> dict[str, Any]:

    settings.save_cursor_api_key(body.cursor_api_key.strip())

    validation = await setup.validate_cursor_key(body.cursor_api_key)

    return {"saved": True, **validation}





@router.post("/robinhood/connect")

async def connect_robinhood() -> dict[str, Any]:

    result = await oauth.start()

    return {"status": result.status, "message": result.message, "auth_url": result.auth_url}





@router.post("/robinhood/test")

async def test_robinhood() -> dict[str, Any]:

    result = await setup.test_mcp()

    if not result.get("ok"):

        raise HTTPException(status_code=400, detail=result)

    return result


@router.put("/massive")

async def update_massive_key(body: MassiveKeyBody) -> dict[str, Any]:

    api_key = body.massive_api_key.strip()

    if not api_key:

        raise HTTPException(status_code=400, detail={"error": "Massive API key is required"})

    settings.save_massive_api_key(api_key)

    validation = await setup.test_massive(api_key)

    return {"saved": True, **validation}


@router.post("/massive/test")

async def test_massive() -> dict[str, Any]:

    result = await setup.test_massive()

    if not result.get("ok"):

        raise HTTPException(status_code=400, detail=result)

    return result





@router.delete("/robinhood")

async def disconnect_robinhood() -> dict[str, bool]:

    oauth.disconnect()

    from src.setup.mcp_client import set_cursor_mcp_mode



    set_cursor_mcp_mode(False)

    return {"disconnected": True}





@router.post("/robinhood/use-cursor-mcp")

async def use_cursor_mcp_settings() -> dict[str, Any]:

    setup.enable_cursor_mcp_mode()

    return setup.status()

