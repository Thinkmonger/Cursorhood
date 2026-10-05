from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import webbrowser
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.paths import load_dotenv

load_dotenv()

from src.compat import patch_cursor_sdk_for_python311

patch_cursor_sdk_for_python311()

from src.api.app import create_app
from src.bots.manager import BotManager, set_bot_manager
from src.db.migrate import DEFAULT_BOT_ID
from src.settings.service import SettingsService
from src.system.awake import allow_sleep, prevent_sleep

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def run_server(host: str, port: int) -> None:
    app = create_app()
    manager = BotManager()
    set_bot_manager(manager)
    await manager.start_enabled_bots()

    settings = SettingsService()
    store = manager.store
    app_cfg = settings.read_app()

    if store.is_setup_complete():
        logger.info("Multi-bot scheduler ready")
    else:
        logger.info("Setup incomplete — finish /setup before trading")

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)

    from src.system.restart import set_shutdown_callback

    async def _shutdown() -> None:
        server.should_exit = True

    set_shutdown_callback(_shutdown)
    await server.serve()


def main() -> None:
    parser = argparse.ArgumentParser(description="Cursorhood Agentic Trading Console")
    parser.add_argument("command", nargs="?", default="run", choices=["run", "run-once"])
    parser.add_argument("--bot", default=DEFAULT_BOT_ID, help="Bot id for run-once")
    args = parser.parse_args()

    settings = SettingsService()
    app_cfg = settings.read_app()
    host = app_cfg.dashboard_host
    port = app_cfg.dashboard_port

    if args.command == "run-once":
        async def once() -> None:
            from runner.scheduler import Scheduler

            sched = Scheduler(args.bot)
            if not sched.store.is_setup_complete():
                logger.error("Setup not complete. Run the bot and finish /setup first.")
                sys.exit(1)
            run_id = await sched.run_once(trigger="manual")
            logger.info("Manual run finished: %s", run_id)

        prevent_sleep()
        try:
            asyncio.run(once())
        finally:
            allow_sleep()
            from runner.cursor_agent import close_cursor_sdk

            close_cursor_sdk()
        return

    url = f"http://{host}:{port}/"
    prevent_sleep()
    try:
        if app_cfg.open_browser_on_start:
            webbrowser.open(url)
        logger.info("Starting dashboard at %s", url)
        asyncio.run(run_server(host, port))
    except KeyboardInterrupt:
        logger.info("Shutdown requested — stopping dashboard")
    finally:
        allow_sleep()
        from runner.cursor_agent import close_cursor_sdk

        close_cursor_sdk()


if __name__ == "__main__":
    main()
