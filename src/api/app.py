from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from src.api.bot_settings import router as bot_settings_router
from src.api.bots import router as bots_router
from src.api.security import install_security
from src.api.settings import router as settings_router
from src.api.statistics import router as statistics_router
from src.api.system import router as system_router
from src.api.trading import research_router, scans_router, watchlists_router
from src.api.trading import router as trading_router
from src.api.ws import websocket_endpoint
from src.db.migrate import DEFAULT_BOT_ID
from src.i18n import app_name
from src.paths import PROJECT_ROOT, WEB_DIR, load_dotenv

load_dotenv()


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        try:
            from src.bots.manager import get_bot_manager

            mgr = get_bot_manager()
            await asyncio.wait_for(mgr.shutdown_gracefully(timeout=5.0), timeout=8.0)
        except RuntimeError:
            pass
        except asyncio.TimeoutError:
            import logging

            logging.getLogger(__name__).warning("Graceful shutdown timed out — forcing Cursor SDK close")
            from runner.cursor_agent import close_cursor_sdk, request_cursor_bridge_shutdown

            request_cursor_bridge_shutdown()
            close_cursor_sdk()

    app = FastAPI(title=app_name(), lifespan=lifespan)

    install_security(app)
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    app.include_router(settings_router)
    app.include_router(bots_router)
    app.include_router(bot_settings_router)
    app.include_router(trading_router)
    app.include_router(research_router)
    app.include_router(scans_router)
    app.include_router(watchlists_router)
    app.include_router(statistics_router)
    app.include_router(system_router)

    static_dir = WEB_DIR / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    locales_dir = PROJECT_ROOT / "locales"
    if locales_dir.exists():
        app.mount("/locales", StaticFiles(directory=str(locales_dir)), name="locales")

    @app.websocket("/ws")
    async def ws_route(websocket: WebSocket):
        await websocket_endpoint(websocket)

    def _page(name: str) -> Path:
        return WEB_DIR / name

    def _html_page(name: str) -> FileResponse:
        # no-cache (not no-store) still revalidates but allows a 304 instead of
        # re-sending the whole page on every navigation.
        return FileResponse(_page(name), headers={"Cache-Control": "no-cache"})

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        icon = WEB_DIR / "static" / "favicon.svg"
        if icon.exists():
            return FileResponse(icon, media_type="image/svg+xml")
        return Response(status_code=204)

    @app.get("/")
    async def index():
        return _html_page("dashboard.html")

    @app.get("/dashboard")
    async def dashboard_page():
        return _html_page("dashboard.html")

    @app.get("/bots")
    async def bots_redirect():
        return RedirectResponse("/", status_code=302)

    @app.get("/bots/{bot_id}")
    async def bot_dashboard_page(bot_id: str):
        return _html_page("bot-dashboard.html")

    @app.get("/bots/{bot_id}/agents")
    async def bot_agents_page(bot_id: str):
        return _html_page("agents.html")

    @app.get("/bots/{bot_id}/settings")
    async def bot_settings_redirect(bot_id: str):
        return RedirectResponse(f"/bots/{bot_id}?tab=config", status_code=302)

    @app.get("/statistics")
    async def statistics_page():
        return _html_page("statistics.html")

    @app.get("/research")
    async def research_page():
        return _html_page("research.html")

    @app.get("/research/watchlists/{watchlist_id}")
    async def watchlist_overview_page(watchlist_id: str):
        return _html_page("watchlist.html")

    @app.get("/setup")
    async def setup_redirect():
        return RedirectResponse("/?tab=config", status_code=302)

    @app.get("/settings")
    async def settings_redirect():
        return RedirectResponse("/?tab=config", status_code=302)

    @app.get("/agents")
    async def agents_redirect():
        return RedirectResponse(f"/bots/{DEFAULT_BOT_ID}/agents", status_code=302)

    return app
