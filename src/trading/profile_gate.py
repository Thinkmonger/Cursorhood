"""Detect Robinhood agentic investor-profile gate from run summaries."""
from __future__ import annotations

import json
import re
from typing import Any

from src.db.migrate import DEFAULT_BOT_ID
from src.db.store import Store

PROFILE_GATE_RE = re.compile(
    r"investor\s+profile|investment_profile|second[\s-]?trade",
    re.IGNORECASE,
)
ACCOUNT_RE = re.compile(r"account_number=(\d+)", re.IGNORECASE)

ACK_SUFFIX = "investor_profile_acknowledged"


def _scan_text(text: str) -> dict[str, Any] | None:
    if not text or not PROFILE_GATE_RE.search(text):
        return None
    account = None
    m = ACCOUNT_RE.search(text)
    if m:
        account = m.group(1)
    return {
        "account_number": account,
        "snippet": text[:280],
    }


def _scan_run(run: dict[str, Any]) -> dict[str, Any] | None:
    for blob in (run.get("summary"), run.get("error")):
        if not blob:
            continue
        hit = _scan_text(str(blob))
        if hit:
            return hit
        try:
            data = json.loads(blob)
            if isinstance(data, dict):
                hit = _scan_text(json.dumps(data))
                if hit:
                    return hit
                hit = _scan_text(str(data.get("reason", "")))
                if hit:
                    return hit
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def get_profile_gate_status(
    *, bot_id: str = DEFAULT_BOT_ID, scan_limit: int = 15
) -> dict[str, Any]:
    store = Store()
    acknowledged = store.get_bot_state(bot_id, ACK_SUFFIX) == "true"

    hit: dict[str, Any] | None = None
    hit_run_id: int | None = None
    for run in store.get_runs(limit=scan_limit, bot_id=bot_id):
        found = _scan_run(run)
        if found:
            hit = found
            hit_run_id = int(run["id"])
            break

    active = hit is not None and not acknowledged

    if not hit:
        return {"active": False, "acknowledged": acknowledged, "bot_id": bot_id}

    account_suffix = hit.get("account_number") or "?"
    return {
        "active": active,
        "acknowledged": acknowledged,
        "bot_id": bot_id,
        "run_id": hit_run_id,
        "account_number": hit.get("account_number"),
        "account_hint": f"···{account_suffix[-4:]}" if len(account_suffix) > 4 else account_suffix,
        "title": "Investor profile required (Robinhood agentic account)",
        "message": (
            "Your first agentic trade went through. Robinhood blocks trade #2 until you "
            "complete the investor profile in the Robinhood mobile app."
        ),
        "why_link_fails": (
            "The applink.robinhood.com URL only opens inside the Robinhood app — "
            "it will not work in a desktop browser (the popup closing is expected)."
        ),
        "steps": [
            "Open the Robinhood app on your phone (not the browser).",
            f"Switch to your Agentic account ending in {account_suffix[-4:] if len(account_suffix) >= 4 else account_suffix}.",
            "Complete the investor profile / suitability questionnaire when prompted.",
            "Return here and click “I've completed the profile”, then run a new cycle.",
        ],
    }


def acknowledge_profile_gate(bot_id: str = DEFAULT_BOT_ID) -> None:
    Store().set_bot_state(bot_id, ACK_SUFFIX, "true")


def clear_profile_acknowledgement(bot_id: str = DEFAULT_BOT_ID) -> None:
    Store().set_bot_state(bot_id, ACK_SUFFIX, "false")


def note_run_finished(bot_id: str, summary: str | None) -> None:
    """Clear a stale acknowledgement when a run still hits the profile gate."""
    if summary and _scan_text(str(summary)):
        clear_profile_acknowledgement(bot_id)
