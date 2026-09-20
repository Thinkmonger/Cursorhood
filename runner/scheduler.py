from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from src.compat import patch_cursor_sdk_for_python311

patch_cursor_sdk_for_python311()

from src.api.ws import emit_agent_event
from src.auth.tokens import TokenStore
from src.db.migrate import DEFAULT_BOT_ID
from src.db.store import Store
from src.settings.service import SettingsService
from src.setup.mcp_client import MCPClient, use_cursor_mcp_mode
from src.setup.mcp_servers import build_agent_mcp_servers, runner_trading_ready
from src.trading.market_hours import compute_next_scheduled_run, is_market_hours, seconds_until_next_wake
from runner.prompts import build_cycle_prompt, finalize_run_record, parse_sdk_message

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, bot_id: str = DEFAULT_BOT_ID) -> None:
        self.bot_id = bot_id
        self.store = Store()
        self.settings = SettingsService(bot_id)
        self.tokens = TokenStore()
        self._task: asyncio.Task[None] | None = None
        paused = self.store.get_bot_state(bot_id, "scheduler_paused", "false")
        self._paused = paused == "true"
        self._running_cycle = False
        self._next_scheduled_run_at: datetime | None = None
        self._shutdown = False
        self._manual_tasks: set[asyncio.Task[None]] = set()

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def running_cycle(self) -> bool:
        return self._running_cycle

    @property
    def scheduler_enabled(self) -> bool:
        return self.settings.read_bot_app().scheduler_enabled

    def pause(self) -> None:
        self._paused = True
        self.store.set_bot_state(self.bot_id, "scheduler_paused", "true")
        self._update_next_scheduled_run()

    def resume(self) -> None:
        self._paused = False
        self.store.set_bot_state(self.bot_id, "scheduler_paused", "false")
        self._update_next_scheduled_run()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop())
        self._update_next_scheduled_run()

    async def stop(self, *, wait_cycle: bool = False, timeout: float = 60.0) -> None:
        self._shutdown = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if not wait_cycle:
            for task in list(self._manual_tasks):
                task.cancel()
        self._next_scheduled_run_at = None
        if wait_cycle and self._running_cycle:
            from runner.cursor_agent import wait_for_cursor_runs

            loop = asyncio.get_running_loop()
            ok = await loop.run_in_executor(None, wait_for_cursor_runs, timeout)
            if not ok:
                logger.warning(
                    "Timed out waiting for bot %s cycle to finish during shutdown",
                    self.bot_id,
                )

    @property
    def next_scheduled_run_at(self) -> datetime | None:
        return self._next_scheduled_run_at

    def _max_runs(self) -> int | None:
        return self.settings.read_bot_app().max_runs

    def _max_runs_reached(self) -> bool:
        max_runs = self._max_runs()
        if max_runs is None:
            return False
        return self.store.count_runs(self.bot_id) >= max_runs

    async def _handle_max_runs_reached(self) -> None:
        logger.info("Max runs reached for bot %s", self.bot_id)
        self.store.set_bot_state(self.bot_id, "scheduler_max_runs_reached", "true")
        await self.stop()
        self.store.set_bot_state(self.bot_id, "scheduler_started", "false")

    def runs_status(self) -> dict[str, Any]:
        max_runs = self._max_runs()
        run_count = self.store.count_runs(self.bot_id)
        return {
            "run_count": run_count,
            "max_runs": max_runs,
            "max_runs_reached": max_runs is not None and run_count >= max_runs,
        }

    def _scheduler_active(self) -> bool:
        app = self.settings.read_bot_app()
        return (
            not self._paused
            and app.scheduler_enabled
            and self.store.is_setup_complete()
            and self._task is not None
            and not self._task.done()
        )

    def _update_next_scheduled_run(self) -> None:
        app = self.settings.read_bot_app()
        limits = self.settings.read_limits()
        self._next_scheduled_run_at = compute_next_scheduled_run(
            interval=app.cycle_interval_seconds,
            market_hours_only=limits.market_hours_only,
            active=self._scheduler_active() and not self._running_cycle,
            trades_24_7=limits.crypto_enabled,
        )

    async def _loop(self) -> None:
        await asyncio.sleep(3)
        while not self._shutdown:
            app = self.settings.read_bot_app()
            limits = self.settings.read_limits()
            # Crypto trades around the clock, so those bots keep cycling after the equity close.
            market_only = limits.market_hours_only and not limits.crypto_enabled
            can_run = (
                not self._paused
                and app.scheduler_enabled
                and self.store.is_setup_complete()
                and not self._running_cycle
                and not self._max_runs_reached()
            )
            if can_run and (not market_only or is_market_hours()):
                try:
                    await self.run_once(trigger="scheduled")
                    if self._max_runs_reached():
                        await self._handle_max_runs_reached()
                except Exception as exc:
                    logger.exception("Scheduled cycle failed for bot %s: %s", self.bot_id, exc)

            sleep_seconds = seconds_until_next_wake(
                app.cycle_interval_seconds,
                market_only,
            )
            if can_run:
                self._next_scheduled_run_at = datetime.now(timezone.utc) + timedelta(
                    seconds=sleep_seconds
                )
            else:
                self._next_scheduled_run_at = None
            await asyncio.sleep(sleep_seconds)

    def _reserve_cycle(self, trigger: str) -> tuple[int, bool]:
        """Claim this bot's single-cycle slot and create the run row.

        Returns ``(run_id, is_new)``. If a cycle is already in flight, returns the
        existing active run id with ``is_new=False`` instead of starting another.
        """
        if self._max_runs_reached():
            max_runs = self._max_runs()
            raise RuntimeError(f"Max runs ({max_runs}) reached for this bot")
        if self._running_cycle:
            active = self.store.get_active_run(bot_id=self.bot_id)
            if active:
                return int(active["id"]), False
        self._running_cycle = True
        run_id = self.store.create_run(bot_id=self.bot_id, trigger=trigger)
        emit_agent_event(run_id, self.bot_id, "run_start", {"trigger": trigger})
        return run_id, True

    async def _perform_cycle(self, run_id: int) -> None:
        from src.cycle_context import clear_cycle_context, publish_cycle_context

        publish_cycle_context(self.bot_id, run_id)
        try:
            await self._execute_cursor_run(run_id)
        except Exception as exc:
            from runner.cursor_agent import CursorRunInterrupted

            if isinstance(exc, CursorRunInterrupted):
                logger.info("Run interrupted for bot %s during shutdown: %s", self.bot_id, exc)
                self.store.finish_run(run_id, "error", error=str(exc))
                emit_agent_event(
                    run_id,
                    self.bot_id,
                    "run_end",
                    {"status": "interrupted", "error": str(exc)},
                )
            else:
                logger.exception("Run failed for bot %s", self.bot_id)
                self.store.finish_run(run_id, "error", error=str(exc))
                emit_agent_event(
                    run_id, self.bot_id, "run_end", {"status": "error", "error": str(exc)}
                )
        finally:
            clear_cycle_context(self.bot_id)
            self._running_cycle = False
            self._update_next_scheduled_run()

    async def run_once(self, trigger: str = "manual") -> int:
        """Run a cycle to completion. Used by the scheduled loop."""
        run_id, is_new = self._reserve_cycle(trigger)
        if not is_new:
            return run_id
        await self._perform_cycle(run_id)
        return run_id

    def start_background_run(self, trigger: str = "manual") -> int:
        """Start a cycle without blocking the caller; return the run id immediately.

        Manual ("Run Cycle") triggers must not hold the HTTP request open for the
        multi-minute Cursor run.
        """
        run_id, is_new = self._reserve_cycle(trigger)
        if is_new:
            task = asyncio.create_task(self._perform_cycle(run_id))
            self._manual_tasks.add(task)
            task.add_done_callback(self._manual_tasks.discard)
        return run_id

    async def _execute_cursor_run(self, run_id: int) -> None:
        api_key = self.settings.get_cursor_api_key()
        if not api_key:
            raise RuntimeError("CURSOR_API_KEY not configured")

        via_cursor = use_cursor_mcp_mode()
        token = self.tokens.get_access_token()
        ready, reason = runner_trading_ready(token, via_cursor)
        if not ready:
            raise RuntimeError(reason)

        snapshot: dict[str, Any] = {"ok": False}
        if token:
            snapshot = await MCPClient(token=token).fetch_trading_snapshot(bot_id=self.bot_id)
            if snapshot.get("ok"):
                from src.simulation.ledger import apply_simulation_snapshot_policy

                snapshot = apply_simulation_snapshot_policy(self.bot_id, snapshot)
                stored_snapshot = {
                    k: v
                    for k, v in snapshot.items()
                    if k
                    not in (
                        "historical_bars_1h",
                        "historical_bars_1d",
                        "accounts",
                        "watchlists",
                    )
                }
                # Keep watchlists, but only the compacted form the dashboard panel needs.
                from src.trading.mcp_tools import compact_watchlists_for_prompt

                compact_watchlists = compact_watchlists_for_prompt(snapshot.get("watchlists"))
                if compact_watchlists:
                    stored_snapshot["watchlists"] = {"data": {"watchlists": compact_watchlists}}
                emit_agent_event(run_id, self.bot_id, "portfolio_snapshot", stored_snapshot)
            else:
                emit_agent_event(
                    run_id,
                    self.bot_id,
                    "portfolio_snapshot",
                    {"ok": False, "error": snapshot.get("error", "Prefetch failed")},
                )

        app = self.settings.read_bot_app()
        prompt = build_cycle_prompt(
            bot_id=self.bot_id,
            trading_snapshot=snapshot if snapshot.get("ok") else None,
        )

        try:
            from runner.cursor_agent import (
                _reset_bridge_unless_busy,
                execute_local_agent_run,
                run_timeout_seconds,
            )
        except ImportError as exc:
            raise RuntimeError("cursor-sdk not installed") from exc

        from src.paths import PROJECT_ROOT

        project_root = str(PROJECT_ROOT)
        mcp_servers = build_agent_mcp_servers(token, bot_id=self.bot_id, run_id=run_id)

        timeout = run_timeout_seconds() + 45
        try:
            outcome = await asyncio.wait_for(
                asyncio.to_thread(
                    execute_local_agent_run,
                    run_id=run_id,
                    bot_id=self.bot_id,
                    prompt=prompt,
                    api_key=api_key,
                    model=app.cursor_model,
                    project_root=project_root,
                    mcp_servers=mcp_servers,
                    on_message=parse_sdk_message,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            _reset_bridge_unless_busy(bot_id=self.bot_id, run_id=run_id)
            raise RuntimeError(
                f"Cursor agent run timed out after {run_timeout_seconds()}s"
            ) from exc
        except Exception as exc:
            from runner.cursor_agent import CursorRunInterrupted

            if isinstance(exc, CursorRunInterrupted):
                raise
            from cursor_sdk import CursorAgentError

            if isinstance(exc, CursorAgentError):
                raise RuntimeError(
                    f"Cursor agent failed (retryable={exc.is_retryable}): {exc.message}"
                ) from exc
            raise

        finalize_run_record(
            self.store,
            run_id,
            outcome.result,
            outcome.cursor_run_id,
            bot_id=self.bot_id,
        )
        emit_agent_event(
            run_id,
            self.bot_id,
            "run_end",
            {
                "status": getattr(outcome.result, "status", "finished"),
                "agent_id": outcome.agent_id,
                "cursor_run_id": outcome.cursor_run_id,
            },
        )
