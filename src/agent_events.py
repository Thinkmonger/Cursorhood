"""Agent event types and UI visibility rules."""
from __future__ import annotations

# Internal SDK / transport events — hidden from Activity timeline and API payloads.
UI_HIDDEN_EVENT_TYPES = frozenset({
    "cursor_run",
    "thinking",
    "status",
    "assistant_text",
    "tool_result",
    "message",
})


def filter_events_for_ui(events: list[dict]) -> list[dict]:
    return [event for event in events if event.get("type") not in UI_HIDDEN_EVENT_TYPES]
