"""Cursor SDK agent execution following Cursor production best practices."""
from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from src.cursor_models import normalize_cursor_model, subscription_model_warning

logger = logging.getLogger(__name__)

# Bots run concurrently: the SDK bridge is a thread-safe singleton that hosts
# multiple agents (one agent_id per run). Runs are NOT serialized.
_ACTIVE_RUNS = 0
# Guards _ACTIVE_RUNS and lets shutdown wait for in-flight runs to drain.
_ACTIVE_RUNS_COND = threading.Condition()
_SHUTDOWN_REQUESTED = threading.Event()
_DEFAULT_RUN_TIMEOUT_SECONDS = 600


class CursorRunInterrupted(RuntimeError):
    """Raised when a Cursor run is stopped because the process is shutting down."""


@dataclass(frozen=True)
class CursorAgentRunResult:
    """Outcome of one local SDK agent cycle."""

    result: Any
    agent_id: str | None
    cursor_run_id: str | None


def request_cursor_bridge_shutdown() -> None:
    """Signal in-flight Cursor runs that the bridge is about to close."""
    _SHUTDOWN_REQUESTED.set()


def shutdown_requested() -> bool:
    return _SHUTDOWN_REQUESTED.is_set()


def active_cursor_run_count() -> int:
    with _ACTIVE_RUNS_COND:
        return _ACTIVE_RUNS


def wait_for_cursor_runs(timeout: float = 60.0) -> bool:
    """Block until worker-thread Cursor runs finish (or timeout)."""
    deadline = time.monotonic() + max(0.0, timeout)
    with _ACTIVE_RUNS_COND:
        while _ACTIVE_RUNS > 0:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            _ACTIVE_RUNS_COND.wait(timeout=remaining)
    return True


def _track_cursor_run_started() -> None:
    with _ACTIVE_RUNS_COND:
        global _ACTIVE_RUNS
        _ACTIVE_RUNS += 1


def _track_cursor_run_finished() -> None:
    with _ACTIVE_RUNS_COND:
        global _ACTIVE_RUNS
        _ACTIVE_RUNS = max(0, _ACTIVE_RUNS - 1)
        _ACTIVE_RUNS_COND.notify_all()


def close_cursor_sdk(*, wait_timeout: float = 0.0) -> None:
    """Release the module-level Cursor SDK bridge (long-running processes)."""
    request_cursor_bridge_shutdown()
    if wait_timeout > 0:
        wait_for_cursor_runs(wait_timeout)
    try:
        from cursor_sdk import close_default_client

        close_default_client()
    except Exception:
        pass


def _reset_bridge_unless_busy(*, bot_id: str, run_id: int) -> None:
    """Reset the shared bridge after an error, but only if this is the sole
    active run. Tearing down the singleton bridge while a sibling bot is still
    streaming would abort its run (WinError 10038 on Windows)."""
    active = active_cursor_run_count()
    if active > 1:
        logger.warning(
            "Skipping bridge reset bot=%s run=%s — %d concurrent run(s) active",
            bot_id,
            run_id,
            active,
        )
        return
    close_cursor_sdk()


def run_timeout_seconds() -> int:
    raw = os.environ.get("CURSOR_RUN_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return _DEFAULT_RUN_TIMEOUT_SECONDS
    try:
        return max(60, int(raw))
    except ValueError:
        return _DEFAULT_RUN_TIMEOUT_SECONDS


def _drive_run(
    run: Any,
    run_id: int,
    on_message: Callable[[int, Any], None],
) -> Any:
    """Stream SDK messages, then block until the run reaches a terminal state."""
    for message in run.messages():
        on_message(run_id, message)
    return run.wait()


def _close_agent_safely(agent_ctx: Any, *, bot_id: str, run_id: int) -> None:
    try:
        agent_ctx.__exit__(None, None, None)
    except Exception as exc:
        logger.warning("Cursor agent close failed (bridge may reset): %s", exc)
        if not shutdown_requested():
            _reset_bridge_unless_busy(bot_id=bot_id, run_id=run_id)


def execute_local_agent_run(
    *,
    run_id: int,
    bot_id: str,
    prompt: str,
    api_key: str,
    model: str,
    project_root: str,
    mcp_servers: dict[str, Any],
    on_message: Callable[[int, Any], None],
) -> CursorAgentRunResult:
    """Run one local Cursor agent cycle (sync — call via asyncio.to_thread).

    All Cursor SDK I/O stays on this thread. Do not stream ``run.messages()``
    from a nested executor — the bridge httpx client is not thread-safe on Windows.
    """
    try:
        from cursor_sdk import Agent, AgentOptions, CursorAgentError, LocalAgentOptions
    except ImportError as exc:
        raise RuntimeError("cursor-sdk not installed") from exc

    model_id = normalize_cursor_model(model)
    billing_warning = subscription_model_warning(model_id)
    if billing_warning:
        logger.warning(billing_warning)

    options = AgentOptions(
        model=model_id,
        api_key=api_key.strip(),
        name=f"robinhood-{bot_id}",
        local=LocalAgentOptions(cwd=project_root),
        mcp_servers=mcp_servers,
        mode="agent",
    )

    agent_ctx = None
    _track_cursor_run_started()
    try:
        agent_ctx = Agent.create(options)
        agent = agent_ctx.__enter__()
        agent_id = getattr(agent, "agent_id", None)
        run = agent.send(prompt)
        cursor_run_id = getattr(run, "id", None)
        logger.info(
            "Cursor agent run started bot=%s db_run=%s agent_id=%s cursor_run_id=%s",
            bot_id,
            run_id,
            agent_id,
            cursor_run_id,
        )

        result = _drive_run(run, run_id, on_message)
        status = getattr(result, "status", "finished")

        if status == "error":
            logger.error(
                "Cursor run failed mid-flight bot=%s db_run=%s agent_id=%s cursor_run_id=%s",
                bot_id,
                run_id,
                agent_id,
                cursor_run_id,
            )
        return CursorAgentRunResult(
            result=result,
            agent_id=agent_id,
            cursor_run_id=cursor_run_id,
        )
    except CursorAgentError as exc:
        if shutdown_requested():
            raise CursorRunInterrupted(f"Server shutting down: {exc.message}") from exc
        retry_after = getattr(exc, "retry_after", None)
        logger.error(
            "Cursor agent error bot=%s db_run=%s retryable=%s retry_after=%s: %s",
            bot_id,
            run_id,
            exc.is_retryable,
            retry_after,
            exc.message,
        )
        _reset_bridge_unless_busy(bot_id=bot_id, run_id=run_id)
        raise
    except Exception as exc:
        from cursor_sdk.errors import NetworkError

        if isinstance(exc, NetworkError):
            if shutdown_requested():
                raise CursorRunInterrupted(f"Server shutting down: {exc}") from exc
            logger.error(
                "Cursor bridge network error bot=%s db_run=%s: %s",
                bot_id,
                run_id,
                exc,
            )
            _reset_bridge_unless_busy(bot_id=bot_id, run_id=run_id)
            raise RuntimeError(f"Cursor bridge network error: {exc}") from exc
        raise
    finally:
        if agent_ctx is not None:
            _close_agent_safely(agent_ctx, bot_id=bot_id, run_id=run_id)
        _track_cursor_run_finished()
