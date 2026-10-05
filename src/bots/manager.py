from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.bots.slug import slugify
from src.db.migrate import DEFAULT_BOT_ID
from src.db.store import Store
from src.settings.service import SettingsService

logger = logging.getLogger(__name__)

_bot_manager: "BotManager | None" = None


def set_bot_manager(manager: "BotManager") -> None:
    global _bot_manager
    _bot_manager = manager


def get_bot_manager() -> "BotManager":
    if _bot_manager is None:
        raise RuntimeError("BotManager not initialized")
    return _bot_manager


class BotManager:
    def __init__(self) -> None:
        from runner.scheduler import Scheduler

        self.store = Store()
        reconciled = self.store.reconcile_orphaned_runs()
        if reconciled:
            logger.info("Reconciled %s orphaned running run(s) after startup", reconciled)
        self._schedulers: dict[str, Scheduler] = {}
        for bot in self.store.list_bots():
            self._schedulers[bot["id"]] = Scheduler(bot["id"])

    def get_scheduler(self, bot_id: str):
        if bot_id not in self._schedulers:
            raise KeyError(f"Unknown bot: {bot_id}")
        return self._schedulers[bot_id]

    def _ensure_bot(self, bot_id: str) -> dict[str, Any]:
        bot = self.store.get_bot(bot_id)
        if not bot:
            raise KeyError(f"Unknown bot: {bot_id}")
        return bot

    def list_bots(self, *, stats_run_limit: int = 500, light: bool = False) -> list[dict[str, Any]]:
        out = []
        for bot in self.store.list_bots():
            out.append(self.bot_snapshot(bot["id"], stats_run_limit=stats_run_limit, light=light))
        return out

    def bot_snapshot(
        self,
        bot_id: str,
        *,
        stats_run_limit: int = 500,
        light: bool = False,
        include_stats: bool = True,
    ) -> dict[str, Any]:
        self._ensure_bot(bot_id)
        sched = self._schedulers[bot_id]
        runs = self.store.get_runs(limit=1, bot_id=bot_id)
        active = self.store.get_active_run(bot_id=bot_id)
        stats: dict[str, Any] = {}
        if include_stats:
            from src.stats.service import bot_stats

            stats = bot_stats(bot_id, run_limit=stats_run_limit, light=light)
        started = self.store.get_bot_state(bot_id, "scheduler_started") == "true"
        paused = sched.paused
        next_run = sched.next_scheduled_run_at
        from src.settings.service import SettingsService

        settings = SettingsService(bot_id)
        limits = settings.read_limits()
        return {
            **self.store.get_bot(bot_id),  # type: ignore[arg-type]
            "asset_class": limits.asset_class,
            "simulation_mode": settings.read_bot_app().simulation_mode,
            "scheduler": {
                "started": started,
                "paused": paused,
                "running": sched.running_cycle,
                "running_cycle": sched.running_cycle,
                "scheduler_enabled": sched.scheduler_enabled,
                "next_scheduled_run_at": next_run.isoformat() if next_run else None,
            },
            "last_run": runs[0] if runs else None,
            "active_run": active,
            "stats": stats,
        }

    def create_bot(self, name: str, asset_class: str = "equity") -> dict[str, Any]:
        existing = {b["id"] for b in self.store.list_bots()}
        bot_id = slugify(name, existing=existing)
        bot = self.store.create_bot(bot_id, name.strip())
        settings = SettingsService(bot_id)
        settings.init_from_class(asset_class)
        from runner.scheduler import Scheduler

        self._schedulers[bot_id] = Scheduler(bot_id)
        return self.bot_snapshot(bot_id)

    async def start_bot(self, bot_id: str) -> dict[str, Any]:
        self._ensure_bot(bot_id)
        sched = self._schedulers[bot_id]
        self.store.set_bot_state(bot_id, "scheduler_max_runs_reached", "false")
        await sched.start()
        self.store.set_bot_state(bot_id, "scheduler_started", "true")
        return self.bot_snapshot(bot_id)

    async def stop_bot(self, bot_id: str) -> dict[str, Any]:
        self._ensure_bot(bot_id)
        sched = self._schedulers[bot_id]
        await sched.stop()
        self.store.set_bot_state(bot_id, "scheduler_started", "false")
        return self.bot_snapshot(bot_id)

    def pause_bot(self, bot_id: str) -> dict[str, Any]:
        self._ensure_bot(bot_id)
        self._schedulers[bot_id].pause()
        return self.bot_snapshot(bot_id)

    def resume_bot(self, bot_id: str) -> dict[str, Any]:
        self._ensure_bot(bot_id)
        self._schedulers[bot_id].resume()
        return self.bot_snapshot(bot_id)

    async def run_now(self, bot_id: str) -> int:
        self._ensure_bot(bot_id)
        return self._schedulers[bot_id].start_background_run(trigger="manual")

    async def rename_bot(self, old_id: str, new_id: str) -> dict[str, Any]:
        import re

        self._ensure_bot(old_id)
        new_id = new_id.strip().lower()
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", new_id):
            raise ValueError("Invalid bot ID — use lowercase letters, numbers, and hyphens only")

        was_started = self.store.get_bot_state(old_id, "scheduler_started") == "true"
        old_sched = self._schedulers[old_id]
        was_paused = old_sched.paused
        if was_started:
            await old_sched.stop()

        self.store.rename_bot(old_id, new_id)
        SettingsService.rename_bot_config(old_id, new_id)
        del self._schedulers[old_id]

        from runner.scheduler import Scheduler

        new_sched = Scheduler(new_id)
        if was_paused:
            new_sched.pause()
        self._schedulers[new_id] = new_sched
        if was_started:
            await new_sched.start()
            self.store.set_bot_state(new_id, "scheduler_started", "true")

        return self.bot_snapshot(new_id)

    async def remove_bot(self, bot_id: str) -> None:
        if bot_id == DEFAULT_BOT_ID:
            raise ValueError("Cannot remove the default bot")
        self._ensure_bot(bot_id)
        await self.stop_bot(bot_id)
        self.store.delete_bot(bot_id)
        SettingsService.remove_bot_config(bot_id)
        del self._schedulers[bot_id]

    async def start_enabled_bots(self) -> None:
        if not self.store.is_setup_complete():
            return
        for bot in self.store.list_bots():
            bot_id = bot["id"]
            settings = SettingsService(bot_id)
            app = settings.read_bot_app()
            started = self.store.get_bot_state(bot_id, "scheduler_started")
            if app.auto_start_scheduler or started == "true":
                try:
                    await self.start_bot(bot_id)
                    logger.info("Started scheduler for bot %s", bot_id)
                except Exception as exc:
                    logger.exception("Failed to start bot %s: %s", bot_id, exc)

    async def shutdown_gracefully(self, timeout: float = 5.0) -> None:
        from runner.cursor_agent import (
            active_cursor_run_count,
            close_cursor_sdk,
            request_cursor_bridge_shutdown,
            wait_for_cursor_runs,
        )

        for bot_id in list(self._schedulers.keys()):
            await self._schedulers[bot_id].stop(wait_cycle=False)

        loop = asyncio.get_running_loop()
        ok = await loop.run_in_executor(None, wait_for_cursor_runs, timeout)
        request_cursor_bridge_shutdown()
        if not ok:
            logger.warning(
                "Force-closing Cursor SDK with %s active run(s) after %.0fs",
                active_cursor_run_count(),
                timeout,
            )
        close_cursor_sdk()

    def scheduler_snapshot(self, bot_id: str) -> dict[str, Any]:
        sched = self._schedulers[bot_id]
        next_run = sched.next_scheduled_run_at
        return {
            "started": self.store.get_bot_state(bot_id, "scheduler_started") == "true",
            "paused": sched.paused,
            "running": sched.running_cycle,
            "scheduler_enabled": sched.scheduler_enabled,
            "next_scheduled_run_at": next_run.isoformat() if next_run else None,
            **sched.runs_status(),
        }
