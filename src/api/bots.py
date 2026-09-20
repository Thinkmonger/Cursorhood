from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException

from src.bots.manager import get_bot_manager
from src.db.store import Store
from src.settings.schema import BotUpdate, CreateBotBody, ResetSimulationBody, SlugFromNameBody

router = APIRouter(prefix="/api/bots", tags=["bots"])
store = Store()


def _build_bot_cards() -> list[dict[str, Any]]:
    from src.stats.portfolio import enrich_bot_card_stats

    mgr = get_bot_manager()
    bots = mgr.list_bots(stats_run_limit=40, light=True)
    for bot in bots:
        stats = bot.get("stats") or {}
        if bot.get("simulation_mode"):
            try:
                stats = enrich_bot_card_stats(bot["id"], stats, None)
            except Exception:
                pass
        elif stats.get("managed_portfolio_value") is None:
            stats["managed_portfolio_value"] = stats.get("latest_portfolio_value")
        bot["stats"] = stats
    return bots


def _minimal_bot_cards() -> list[dict[str, Any]]:
    return [
        {
            **row,
            "simulation_mode": False,
            "scheduler": {},
            "stats": {"total_runs": 0},
            "last_run": None,
            "active_run": None,
        }
        for row in store.list_bots()
    ]


@router.get("")
async def list_bots() -> dict[str, Any]:
    try:
        bots = await asyncio.wait_for(asyncio.to_thread(_build_bot_cards), timeout=10.0)
    except RuntimeError:
        bots = []
        for row in store.list_bots():
            bots.append({"id": row["id"], "name": row["name"], "stats": {}, "scheduler": {}})
    except asyncio.TimeoutError:
        bots = await asyncio.to_thread(_minimal_bot_cards)

    return {"bots": bots}


@router.post("")
async def create_bot(body: CreateBotBody) -> dict[str, Any]:
    mgr = get_bot_manager()
    bot = mgr.create_bot(body.name.strip())
    return {"bot": bot}


@router.get("/{bot_id}")
async def get_bot(bot_id: str) -> dict[str, Any]:
    try:
        mgr = get_bot_manager()
        return {"bot": mgr.bot_snapshot(bot_id)}
    except KeyError:
        raise HTTPException(status_code=404, detail="Bot not found") from None


@router.patch("/{bot_id}")
async def update_bot(bot_id: str, body: BotUpdate) -> dict[str, Any]:
    store = Store()
    if not store.get_bot(bot_id):
        raise HTTPException(status_code=404, detail="Bot not found")
    mgr = get_bot_manager()

    target_id = bot_id
    if body.id is not None and body.id.strip().lower() != bot_id:
        if bot_id == "default":
            raise HTTPException(status_code=400, detail="Cannot rename the default bot")
        try:
            await mgr.rename_bot(bot_id, body.id.strip().lower())
            target_id = body.id.strip().lower()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if body.name is not None:
        store.update_bot_name(target_id, body.name.strip())

    return {"bot": mgr.bot_snapshot(target_id), "redirect": f"/bots/{target_id}" if target_id != bot_id else None}


@router.post("/{bot_id}/slug-from-name")
async def slug_from_name(bot_id: str, body: SlugFromNameBody | None = None) -> dict[str, str]:
    from src.bots.slug import slugify

    store = Store()
    bot = store.get_bot(bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")
    existing = {b["id"] for b in store.list_bots()} - {bot_id}
    name = (body.name if body and body.name else bot["name"]).strip()
    return {"slug": slugify(name, existing=existing)}


@router.delete("/{bot_id}")
async def delete_bot(bot_id: str) -> dict[str, bool]:
    try:
        mgr = get_bot_manager()
        await mgr.remove_bot(bot_id)
        return {"removed": True}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError:
        raise HTTPException(status_code=404, detail="Bot not found") from None


@router.post("/{bot_id}/start")
async def start_bot(bot_id: str) -> dict[str, Any]:
    try:
        mgr = get_bot_manager()
        return {"bot": await mgr.start_bot(bot_id)}
    except KeyError:
        raise HTTPException(status_code=404, detail="Bot not found") from None


@router.post("/{bot_id}/stop")
async def stop_bot(bot_id: str) -> dict[str, Any]:
    try:
        mgr = get_bot_manager()
        return {"bot": await mgr.stop_bot(bot_id)}
    except KeyError:
        raise HTTPException(status_code=404, detail="Bot not found") from None


@router.post("/{bot_id}/pause")
async def pause_bot(bot_id: str) -> dict[str, Any]:
    try:
        mgr = get_bot_manager()
        return {"bot": mgr.pause_bot(bot_id)}
    except KeyError:
        raise HTTPException(status_code=404, detail="Bot not found") from None


@router.post("/{bot_id}/resume")
async def resume_bot(bot_id: str) -> dict[str, Any]:
    try:
        mgr = get_bot_manager()
        return {"bot": mgr.resume_bot(bot_id)}
    except KeyError:
        raise HTTPException(status_code=404, detail="Bot not found") from None


@router.post("/{bot_id}/reset-simulation")
async def reset_bot_simulation(
    bot_id: str,
    body: ResetSimulationBody | None = None,
) -> dict[str, Any]:
    from src.simulation.ledger import _CASH_UNSET, reset_simulation

    store = Store()
    if not store.get_bot(bot_id):
        raise HTTPException(status_code=404, detail="Bot not found")

    mgr = get_bot_manager()
    sched = mgr.scheduler_snapshot(bot_id)
    if sched.get("running"):
        raise HTTPException(status_code=400, detail="Cannot reset simulation while a cycle is running")

    cash_override = _CASH_UNSET
    if body is not None and "simulated_cash_starting_value" in body.model_fields_set:
        cash_override = body.simulated_cash_starting_value

    try:
        result = await reset_simulation(bot_id, configured_cash_override=cash_override)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"bot_id": bot_id, **result}


@router.post("/{bot_id}/run-now")
async def run_now(bot_id: str) -> dict[str, Any]:
    try:
        mgr = get_bot_manager()
        run_id = await mgr.run_now(bot_id)
        run = store.get_run(run_id, bot_id=bot_id)
        return {"run_id": run_id, "run_number": run.get("run_number") if run else None, "bot_id": bot_id}
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError:
        raise HTTPException(status_code=404, detail="Bot not found") from None


@router.get("/{bot_id}/runs")
async def list_runs(bot_id: str, limit: int = 50) -> dict[str, Any]:
    store = Store()
    if not store.get_bot(bot_id):
        raise HTTPException(status_code=404, detail="Bot not found")
    return {"runs": store.get_runs(limit=limit, bot_id=bot_id), "bot_id": bot_id}


@router.get("/{bot_id}/runs/{run_id}")
async def get_run(bot_id: str, run_id: int) -> dict[str, Any]:
    store = Store()
    run = store.get_run(run_id, bot_id=bot_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    from src.agent_events import filter_events_for_ui

    events = store.get_events(run_id)
    events = filter_events_for_ui(events)
    return {"run": run, "events": events, "bot_id": bot_id}


@router.get("/{bot_id}/active")
async def active_run(bot_id: str) -> dict[str, Any]:
    store = Store()
    if not store.get_bot(bot_id):
        raise HTTPException(status_code=404, detail="Bot not found")
    return {"run": store.get_active_run(bot_id=bot_id), "bot_id": bot_id}


@router.get("/{bot_id}/portfolio")
async def bot_portfolio(bot_id: str) -> dict[str, Any]:
    from src.stats.portfolio import build_portfolio_overview
    from src.stats.service import bot_stats

    store = Store()
    if not store.get_bot(bot_id):
        raise HTTPException(status_code=404, detail="Bot not found")
    stats = await asyncio.to_thread(bot_stats, bot_id)
    overview = await build_portfolio_overview(bot_id, stats=stats)
    return {"bot_id": bot_id, "portfolio_overview": overview}


@router.get("/{bot_id}/dashboard")
async def bot_dashboard(bot_id: str) -> dict[str, Any]:
    from src.settings.service import SettingsService
    from src.trading.profile_gate import get_profile_gate_status

    store = Store()
    if not store.get_bot(bot_id):
        raise HTTPException(status_code=404, detail="Bot not found")
    mgr = get_bot_manager()
    settings = SettingsService(bot_id)
    limits = settings.read_limits()
    payload: dict[str, Any] = {
        "bot_id": bot_id,
        "bot": store.get_bot(bot_id),
        "setup_complete": store.is_setup_complete(),
        "limits": limits.model_dump(),
        "app": settings.read_app().model_dump(),
        "last_runs": store.get_runs(limit=5, bot_id=bot_id),
        "active_run": store.get_active_run(bot_id=bot_id),
        "scheduler": mgr.scheduler_snapshot(bot_id),
        "profile_gate": get_profile_gate_status(bot_id=bot_id),
    }
    payload.update(_last_cycle_assets(store, bot_id, limits))
    return payload


def _last_cycle_assets(store: Store, bot_id: str, limits: Any) -> dict[str, Any]:
    """Options, crypto, watchlists, and paper state from the most recent cycle."""
    from src.simulation.ledger import PaperBroker, is_simulation_mode

    out: dict[str, Any] = {}
    snapshot: dict[str, Any] = {}
    for run in store.get_runs(limit=5, bot_id=bot_id):
        for event in store.get_events(int(run["id"])):
            if event.get("type") == "portfolio_snapshot" and (event.get("payload") or {}).get("ok"):
                snapshot = event["payload"]
                break
        if snapshot:
            break

    if limits.options_enabled:
        out["option_positions"] = snapshot.get("option_positions") or []
    if limits.crypto_enabled:
        out["crypto_positions"] = snapshot.get("crypto_positions") or []
    if snapshot.get("symbol_source"):
        out["symbol_source"] = snapshot["symbol_source"]

    from src.trading.mcp_tools import compact_watchlists_for_prompt

    watchlists = compact_watchlists_for_prompt(snapshot.get("watchlists"))
    if watchlists:
        out["watchlists"] = watchlists

    if is_simulation_mode(bot_id):
        broker = PaperBroker(bot_id)
        ledger = broker.ledger
        out["paper"] = {
            "open_orders": broker.open_orders(),
            "recent_fills": (ledger.get("trades") or [])[-15:],
            "realized_pnl": round(float(ledger.get("realized_pnl") or 0), 2),
        }
    return out


@router.delete("/{bot_id}/paper-orders/{order_id}")
async def cancel_paper_order(bot_id: str, order_id: str) -> dict[str, Any]:
    from src.simulation.ledger import cancel_paper_orders, is_simulation_mode

    if not Store().get_bot(bot_id):
        raise HTTPException(status_code=404, detail="Bot not found")
    if not is_simulation_mode(bot_id):
        raise HTTPException(status_code=400, detail="Simulation mode is not enabled for this bot")
    cancelled = cancel_paper_orders(bot_id, order_id=order_id)
    return {"ok": True, "cancelled": len(cancelled)}
