# -*- coding: utf-8 -*-
"""Event-sourced TaskRun state derivation."""

from __future__ import annotations

from typing import Any, Protocol


class _EventLike(Protocol):
    """Minimal interface for an event object."""

    event_type: str
    payload_json: str | None
    created_at: Any


_TERMINAL_FAILED_TYPES = frozenset({
    "task_run_failed",
    "task_run_interrupted",
})
_TERMINAL_CANCELLED_TYPES = frozenset({
    "task_run_cancelled",
})
_PAUSED_TYPES = frozenset({
    "approval_queue_item_created",
})
_RUNNING_TYPES = frozenset({
    "task_run_created",
    "agent_turn_started",
    "agent_turn_resumed",
    "scheduler_step_dispatched",
    "scheduler_step_resumed",
    "task_run_recovery_started",
    "approval_queue_item_resolved",
    "approval_queue_item_followup_triggered",
    "tracked_run_shell_completed",
    "tracked_run_shell_followup_queued",
    "run_shell_continuation_claimed",
    "task_run_waiting_for_delegated_work",
    "task_run_manual_resume_requested",
})


def derive_task_run_status(events: list[_EventLike]) -> str:
    """Derive the TaskRun status from its event stream.

    The derivation walks backward through the stream and only reacts to events
    that change lifecycle state. Informational events such as recovery markers
    or tool-round audit records do not override an existing terminal,
    completed, or paused state.
    """

    if not events:
        return "unknown"

    for index in range(len(events) - 1, -1, -1):
        event_type = events[index].event_type
        if event_type in _TERMINAL_FAILED_TYPES:
            return "failed"
        if event_type in _TERMINAL_CANCELLED_TYPES:
            return "cancelled"
        if event_type == "agent_turn_completed":
            if _has_post_completion_activity(events, index):
                return "running"
            if _has_pending_approval(events[: index + 1]):
                return "paused"
            return "completed"
        if event_type in _PAUSED_TYPES:
            return "paused"
        if event_type in _RUNNING_TYPES:
            return "running"

    return "running"


def _has_pending_approval(events: list[_EventLike]) -> bool:
    """Return whether the latest approval queue item is still unresolved."""

    last_created_idx = -1
    last_resolved_idx = -1

    for index, event in enumerate(events):
        if event.event_type == "approval_queue_item_created":
            last_created_idx = index
        elif event.event_type == "approval_queue_item_resolved":
            last_resolved_idx = index

    return last_created_idx > last_resolved_idx


def _has_post_completion_activity(events: list[_EventLike], completion_index: int) -> bool:
    """Return whether later events show the broader task run continued after one turn."""

    for event in events[completion_index + 1 :]:
        if event.event_type in _RUNNING_TYPES:
            return True
    return False


def is_terminal_status(status: str) -> bool:
    """Return True if the status is terminal."""

    return status in ("completed", "failed", "cancelled")


def derive_step_status(events: list[_EventLike], step_id: str) -> str:
    """Derive the status of a scheduler step from TaskRun events."""

    step_events = [event for event in events if _event_step_id(event) == step_id]
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
