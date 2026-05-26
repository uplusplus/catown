# -*- coding: utf-8 -*-
"""Event-sourced TaskRun state derivation.

The canonical source of truth for a TaskRun's lifecycle is its event stream.
``derive_task_run_status()`` computes the current status from events, making
it impossible to "forget" to update status — the status is always a function
of what happened, not what a caller remembered to write.

This module replaces direct ``task_run.status = "..."`` assignments throughout
the codebase.  All callers should use ``record_task_run_event()`` (from
``run_ledger``) to append events, and let the status be derived.
"""

from __future__ import annotations

from typing import Any, Protocol


class _EventLike(Protocol):
    """Minimal interface for an event object."""
    event_type: str
    payload_json: str | None
    created_at: Any


# ── Terminal event types ──────────────────────────────────────────────────────
# An event of one of these types makes the TaskRun permanently terminal.
_TERMINAL_FAILED_TYPES = frozenset({
    "task_run_failed",
    "task_run_interrupted",
})
_TERMINAL_CANCELLED_TYPES = frozenset({
    "task_run_cancelled",
})
_ALL_TERMINAL_TYPES = _TERMINAL_FAILED_TYPES | _TERMINAL_CANCELLED_TYPES


# ── Status derivation ─────────────────────────────────────────────────────────

def derive_task_run_status(events: list[_EventLike]) -> str:
    """Derive the TaskRun status from its event stream.

    This is the **single source of truth** for TaskRun lifecycle state.
    The result is deterministic: given the same events, always returns
    the same status.

    Rules (in priority order):
    1. Terminal events (failed/cancelled/interrupted) → terminal status.
    2. ``agent_turn_completed`` → completed (or paused if pending approval).
    3. ``approval_expired`` → failed.
    4. ``approval_created`` → paused.
    5. ``approval_resolved`` → running (re-activated).
    6. ``task_run_created`` / ``agent_turn_started`` / other → running.
    """
    if not events:
        return "unknown"

    last = events[-1]
    event_type = last.event_type

    # 1. Terminal events
    if event_type in _TERMINAL_FAILED_TYPES:
        return "failed"
    if event_type in _TERMINAL_CANCELLED_TYPES:
        return "cancelled"

    # 2. Turn completed — check for pending approvals
    if event_type == "agent_turn_completed":
        if _has_pending_approval(events):
            return "paused"
        return "completed"

    # 3. Approval expired — auto-fail
    if event_type == "approval_expired":
        return "failed"

    # 4. Approval created — blocked
    if event_type == "approval_created":
        return "paused"

    # 5. Approval resolved — re-activated
    if event_type == "approval_resolved":
        return "running"

    # 6. Default — still running
    return "running"


def _has_pending_approval(events: list[_EventLike]) -> bool:
    """Check if there's an unresolved approval in the event stream.

    An approval is pending if the last ``approval_created`` event has no
    subsequent ``approval_resolved`` or ``approval_expired`` event.
    """
    last_approval_created_idx = -1
    last_approval_resolved_idx = -1

    for i, event in enumerate(events):
        if event.event_type == "approval_created":
            last_approval_created_idx = i
        elif event.event_type in ("approval_resolved", "approval_expired"):
            last_approval_resolved_idx = i

    return last_approval_created_idx > last_approval_resolved_idx


def is_terminal_status(status: str) -> bool:
    """Return True if the status is terminal (no outgoing transitions)."""
    return status in ("completed", "failed", "cancelled")


def derive_step_status(events: list[_EventLike], step_id: str) -> str:
    """Derive the status of a scheduler step from TaskRun events.

    Filters events by ``step_id`` in the payload and derives the step's
    current state.
    """
    step_events = [
        e for e in events
        if _event_step_id(e) == step_id
    ]
    if not step_events:
        return "waiting"

    last = step_events[-1]
    event_type = last.event_type

    if event_type == "scheduler_step_completed":
        return "completed"
    if event_type == "scheduler_step_failed":
        return "failed"
    if event_type == "scheduler_step_cancelled":
        return "cancelled"
    if event_type == "scheduler_step_dispatched":
        return "running"

    return "waiting"


def _event_step_id(event: _EventLike) -> str | None:
    """Extract step_id from an event's payload."""
    import json
    try:
        payload = json.loads(event.payload_json) if event.payload_json else {}
    except (json.JSONDecodeError, TypeError):
        payload = {}
    return payload.get("step_id")
