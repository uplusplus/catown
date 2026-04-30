# -*- coding: utf-8 -*-
"""Subagent lifecycle projection helpers for scheduler-driven orchestration."""

from __future__ import annotations

import json
from typing import Any


TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
RUNTIME_NATIVE_STATUS_MAP = {
    "ready": "spawned",
    "waiting": "spawned",
    "running": "running",
    "completed": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
}


def build_subagent_lifecycle_from_events(events: list[Any]) -> dict[str, Any]:
    """Project task-run scheduler events into a Codex-style subagent lifecycle view."""

    states: dict[str, dict[str, Any]] = {}
    transition_count = 0

    for event in events:
        event_type = str(getattr(event, "event_type", "") or "")
        payload = _load_event_payload(event)
        if event_type in {"scheduler_plan_created", "scheduler_recovery_state_rebuilt"}:
            for step in _steps_from_plan_payload(payload):
                step_id = _step_id(step)
                if not step_id:
                    continue
                if step_id not in states:
                    states[step_id] = _initial_state(step)
                    transition_count += 1
                previous_status = states[step_id].get("status")
                _merge_step_identity(states[step_id], step)
                _merge_runtime_state(states[step_id], _runtime_state_payload(step))
                if states[step_id].get("last_event_index") is None:
                    states[step_id]["last_event_index"] = getattr(event, "event_index", None)
                if states[step_id].get("status") != previous_status:
                    transition_count += 1
            for step in _runtime_steps_from_payload(payload):
                step_id = _step_id(step)
                if not step_id:
                    continue
                state = states.setdefault(step_id, _initial_state(step))
                previous_status = state.get("status")
                _merge_step_identity(state, step)
                _merge_runtime_state(state, step)
                if state.get("last_event_index") is None:
                    state["last_event_index"] = getattr(event, "event_index", None)
                if state.get("status") != previous_status:
                    transition_count += 1
            continue

        if event_type not in {
            "scheduler_step_dispatched",
            "scheduler_step_resumed",
            "scheduler_step_completed",
            "scheduler_step_failed",
            "scheduler_step_cancelled",
            "subagent_handle_closed",
        }:
            continue

        step = payload if isinstance(payload, dict) else {}
        step_id = _step_id(step)
        if not step_id:
            continue
        state = states.setdefault(step_id, _initial_state(step))
        _merge_step_identity(state, step)
        _merge_runtime_state(state, _runtime_state_payload(step))
        _merge_event_context(state, step)
        previous_status = state.get("status")

        if event_type == "scheduler_step_dispatched":
            _transition(state, "running", event=event, reason="dispatched")
        elif event_type == "scheduler_step_resumed":
            if state.get("status") not in TERMINAL_STATUSES:
                _transition(state, "spawned", event=event, reason="resumed")
        elif event_type == "scheduler_step_completed":
            _transition(state, "completed", event=event, reason="completed")
        elif event_type == "scheduler_step_failed":
            _transition(state, "failed", event=event, reason="failed")
            if isinstance(payload.get("error"), str):
                state["error"] = payload.get("error")
        elif event_type == "scheduler_step_cancelled":
            _transition(state, "cancelled", event=event, reason="cancelled")
        elif event_type == "subagent_handle_closed":
            state["handle_closed"] = True
            state["closed_by"] = payload.get("closed_by")
            state["close_note"] = payload.get("note")
            created_at = getattr(event, "created_at", None)
            state["closed_at"] = created_at.isoformat() if created_at is not None else None
            state["last_event_index"] = getattr(event, "event_index", None)
            state["last_event_type"] = getattr(event, "event_type", None)
            state["last_event_at"] = state["closed_at"]
            state["transition_reason"] = "closed"

        if state.get("status") != previous_status:
            transition_count += 1

    ordered_states = sorted(states.values(), key=lambda item: (item.get("position") or 0, item.get("step_id") or ""))
    status_counts: dict[str, int] = {}
    for state in ordered_states:
        status = str(state.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1

    return {
        "subagent_count": len(ordered_states),
        "transition_count": transition_count,
        "status_counts": status_counts,
        "subagents": ordered_states,
    }


def summarize_subagent_lifecycle(lifecycle: Any) -> str | None:
    state = lifecycle if isinstance(lifecycle, dict) else {}
    try:
        total = int(state.get("subagent_count") or 0)
    except (TypeError, ValueError):
        total = 0
    if total <= 0:
        return None

    counts = state.get("status_counts") if isinstance(state.get("status_counts"), dict) else {}
    parts = [f"{total} subagents"]
    for status in ["spawned", "running", "completed", "failed", "cancelled"]:
        try:
            count = int(counts.get(status) or 0)
        except (TypeError, ValueError):
            count = 0
        if count:
            parts.append(f"{count} {status}")
    return " · ".join(parts)


def build_subagent_runtime_handles(lifecycle: Any) -> dict[str, Any]:
    """Project lifecycle state into lightweight runtime handles with control-facing flags."""

    state = lifecycle if isinstance(lifecycle, dict) else {}
    subagents = state.get("subagents") if isinstance(state.get("subagents"), list) else []

    entries: list[dict[str, Any]] = []
    control_state_counts: dict[str, int] = {}
    cancellable_count = 0

    for subagent in subagents:
        if not isinstance(subagent, dict):
            continue
        status = str(subagent.get("status") or "").strip().lower()
        scheduler_status = str(subagent.get("scheduler_status") or "").strip().lower() or None
        control_state = _control_state_for_subagent(subagent)
        terminal = status in TERMINAL_STATUSES
        awaitable = control_state in {"await_dependency", "await_dispatch", "await_completion"}
        closed = bool(subagent.get("handle_closed"))
        cancellable = bool(status) and not terminal and not closed
        dependency_step_id = (
            str(subagent.get("wait_for_step_id") or "").strip()
            or str(subagent.get("attached_to_step_id") or "").strip()
            or None
        )
        entry = {
            "step_id": subagent.get("step_id"),
            "agent_name": subagent.get("agent_name"),
            "agent_type": subagent.get("agent_type"),
            "dispatch_kind": subagent.get("dispatch_kind"),
            "status": status or None,
            "scheduler_status": scheduler_status,
            "control_state": control_state,
            "terminal": terminal,
            "closed": closed,
            "awaitable": awaitable,
            "cancellable": cancellable,
            "dependency_step_id": dependency_step_id,
            "released_by_step_id": subagent.get("released_by_step_id"),
            "resumed_by_step_id": subagent.get("resumed_by_step_id"),
            "resumed_by_agent": subagent.get("resumed_by_agent"),
            "dispatch_count": subagent.get("dispatch_count"),
            "completion_count": subagent.get("completion_count"),
            "last_event_index": subagent.get("last_event_index"),
            "last_event_type": subagent.get("last_event_type"),
            "last_event_at": subagent.get("last_event_at"),
            "closed_at": subagent.get("closed_at"),
            "closed_by": subagent.get("closed_by"),
            "close_note": subagent.get("close_note"),
            "error": subagent.get("error"),
            "available_actions": _available_actions_for_control_state(
                control_state=control_state,
                cancellable=cancellable,
                terminal=terminal,
                closed=closed,
            ),
        }
        entries.append(entry)
        control_state_counts[control_state] = control_state_counts.get(control_state, 0) + 1
        if cancellable:
            cancellable_count += 1

    return {
        "handle_count": len(entries),
        "cancellable_count": cancellable_count,
        "control_state_counts": control_state_counts,
        "entries": entries,
    }


def summarize_subagent_runtime_handles(handles: Any) -> str | None:
    projection = handles if isinstance(handles, dict) else {}
    try:
        total = int(projection.get("handle_count") or 0)
    except (TypeError, ValueError):
        total = 0
    if total <= 0:
        return None

    counts = (
        projection.get("control_state_counts")
        if isinstance(projection.get("control_state_counts"), dict)
        else {}
    )
    parts = [f"{total} handle" if total == 1 else f"{total} handles"]
    for control_state in [
        "await_dependency",
        "await_dispatch",
        "await_completion",
        "completed",
        "failed",
        "cancelled",
    ]:
        try:
            count = int(counts.get(control_state) or 0)
        except (TypeError, ValueError):
            count = 0
        if count:
            parts.append(f"{count} {control_state.replace('_', ' ')}")

    try:
        cancellable_count = int(projection.get("cancellable_count") or 0)
    except (TypeError, ValueError):
        cancellable_count = 0
    if cancellable_count:
        parts.append(f"{cancellable_count} cancellable")
    return " · ".join(parts)


def find_subagent_runtime_handle(handles: Any, step_id: str) -> dict[str, Any] | None:
    """Return one projected subagent handle by step id."""

    normalized_step_id = str(step_id or "").strip()
    if not normalized_step_id:
        return None

    projection = handles if isinstance(handles, dict) else {}
    entries = projection.get("entries") if isinstance(projection.get("entries"), list) else []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("step_id") or "").strip() == normalized_step_id:
            return entry
    return None


def find_subagent_lifecycle_entry(lifecycle: Any, step_id: str) -> dict[str, Any] | None:
    """Return one lifecycle entry by step id."""

    normalized_step_id = str(step_id or "").strip()
    if not normalized_step_id:
        return None

    state = lifecycle if isinstance(lifecycle, dict) else {}
    subagents = state.get("subagents") if isinstance(state.get("subagents"), list) else []
    for subagent in subagents:
        if not isinstance(subagent, dict):
            continue
        if str(subagent.get("step_id") or "").strip() == normalized_step_id:
            return subagent
    return None


def cancellable_subagent_handles(handles: Any) -> list[dict[str, Any]]:
    """Return projected handles that still advertise cancel as an available action."""

    projection = handles if isinstance(handles, dict) else {}
    entries = projection.get("entries") if isinstance(projection.get("entries"), list) else []
    return [
        entry
        for entry in entries
        if isinstance(entry, dict) and bool(entry.get("cancellable"))
    ]


def build_subagent_wait_result(
    *,
    handle: Any,
    current_event_index: int,
    since_event_index: int | None = None,
) -> dict[str, Any]:
    """Project a non-blocking wait observation for one handle."""

    entry = handle if isinstance(handle, dict) else {}
    step_id = str(entry.get("step_id") or "").strip() or None
    control_state = str(entry.get("control_state") or "").strip() or None
    terminal = bool(entry.get("terminal"))
    awaitable = bool(entry.get("awaitable"))

    last_event_index = _coerce_nonnegative_int(entry.get("last_event_index"))
    if last_event_index is None:
        last_event_index = current_event_index

    observed_since = (
        since_event_index is not None
        and last_event_index > max(int(since_event_index), 0)
    )
    state_changed = terminal or observed_since

    return {
        "step_id": step_id,
        "control_state": control_state,
        "awaitable": awaitable,
        "terminal": terminal,
        "state_changed": state_changed,
        "last_event_index": last_event_index,
        "since_event_index": since_event_index,
        "suggested_poll": "immediate" if state_changed else "continue",
    }


def cancellable_subagents_from_lifecycle(lifecycle: Any) -> list[dict[str, Any]]:
    """Return subagents that can still be moved to a cancelled terminal state."""

    state = lifecycle if isinstance(lifecycle, dict) else {}
    subagents = state.get("subagents") if isinstance(state.get("subagents"), list) else []
    subagents_by_step_id = {
        str(subagent.get("step_id") or "").strip(): subagent
        for subagent in subagents
        if isinstance(subagent, dict) and str(subagent.get("step_id") or "").strip()
    }
    projection = build_subagent_runtime_handles(lifecycle)
    entries = cancellable_subagent_handles(projection)
    cancellable: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        step_id = str(entry.get("step_id") or "").strip()
        subagent = subagents_by_step_id.get(step_id)
        if subagent is not None:
            cancellable.append(subagent)
    return cancellable


def _load_event_payload(event: Any) -> dict[str, Any]:
    raw = getattr(event, "payload_json", None)
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _steps_from_plan_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    steps = payload.get("steps")
    if isinstance(steps, list):
        return [step for step in steps if isinstance(step, dict)]
    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
    runtime_steps = runtime.get("steps")
    if isinstance(runtime_steps, list):
        return [step for step in runtime_steps if isinstance(step, dict)]
    return []


def _step_id(step: dict[str, Any]) -> str:
    return str(step.get("step_id") or "").strip()


def _initial_state(step: dict[str, Any]) -> dict[str, Any]:
    state = {
        "step_id": _step_id(step),
        "agent_name": None,
        "requested_name": None,
        "agent_id": None,
        "agent_type": None,
        "dispatch_kind": None,
        "position": None,
        "wait_for_step_id": None,
        "attached_to_step_id": None,
        "source": None,
        "status": "spawned",
        "scheduler_status": None,
        "released_by_step_id": None,
        "dispatch_count": 0,
        "completion_count": 0,
        "started_at": None,
        "completed_at": None,
        "last_event_type": None,
        "last_event_at": None,
        "transition_reason": "planned",
        "resumed_by_step_id": None,
        "resumed_by_agent": None,
        "previous_status": None,
        "cancelled_by": None,
        "note": None,
        "error": None,
        "last_event_index": None,
        "handle_closed": False,
        "closed_at": None,
        "closed_by": None,
        "close_note": None,
    }
    _merge_step_identity(state, step)
    return state


def _merge_step_identity(state: dict[str, Any], step: dict[str, Any]) -> None:
    for key in [
        "agent_name",
        "requested_name",
        "agent_id",
        "agent_type",
        "dispatch_kind",
        "position",
        "wait_for_step_id",
        "attached_to_step_id",
        "source",
    ]:
        value = step.get(key)
        if value is not None:
            state[key] = value


def _runtime_steps_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
    runtime_steps = runtime.get("steps")
    if isinstance(runtime_steps, list):
        return [step for step in runtime_steps if isinstance(step, dict)]
    return []


def _runtime_state_payload(step: dict[str, Any]) -> dict[str, Any]:
    runtime_state = step.get("step_state")
    if isinstance(runtime_state, dict):
        return runtime_state
    return {}


def _merge_runtime_state(state: dict[str, Any], runtime_state: dict[str, Any]) -> None:
    if not isinstance(runtime_state, dict):
        return

    scheduler_status = str(runtime_state.get("status") or "").strip().lower()
    if scheduler_status:
        state["scheduler_status"] = scheduler_status
        runtime_native_status = RUNTIME_NATIVE_STATUS_MAP.get(scheduler_status)
        if runtime_native_status and state.get("status") not in TERMINAL_STATUSES:
            state["status"] = runtime_native_status
            if state.get("transition_reason") in {None, "", "planned", "runtime_snapshot"}:
                state["transition_reason"] = "runtime_snapshot"

    for key in ["released_by_step_id"]:
        value = runtime_state.get(key)
        if value is not None:
            state[key] = value

    dispatch_count = _coerce_nonnegative_int(runtime_state.get("dispatch_count"))
    if dispatch_count is not None:
        state["dispatch_count"] = dispatch_count

    completion_count = _coerce_nonnegative_int(runtime_state.get("completion_count"))
    if completion_count is not None:
        state["completion_count"] = completion_count


def _merge_event_context(state: dict[str, Any], step: dict[str, Any]) -> None:
    for key in [
        "resumed_by_step_id",
        "resumed_by_agent",
        "previous_status",
        "cancelled_by",
        "note",
        "error",
    ]:
        value = step.get(key)
        if value is not None:
            state[key] = value


def _control_state_for_subagent(subagent: dict[str, Any]) -> str:
    status = str(subagent.get("status") or "").strip().lower()
    if status in TERMINAL_STATUSES:
        return status
    if status == "running":
        return "await_completion"

    scheduler_status = str(subagent.get("scheduler_status") or "").strip().lower()
    if scheduler_status == "waiting":
        return "await_dependency"
    if scheduler_status == "ready":
        return "await_dispatch"

    dependency_step_id = (
        str(subagent.get("wait_for_step_id") or "").strip()
        or str(subagent.get("attached_to_step_id") or "").strip()
    )
    if dependency_step_id and not subagent.get("released_by_step_id"):
        return "await_dependency"
    return "await_dispatch"


def _available_actions_for_control_state(
    *,
    control_state: str,
    cancellable: bool,
    terminal: bool,
    closed: bool,
) -> list[str]:
    actions: list[str] = []
    if control_state in {"await_dependency", "await_dispatch", "await_completion"}:
        actions.append("wait")
    if cancellable:
        actions.append("cancel")
    if control_state in {"completed", "failed"} and terminal and not closed:
        actions.append("close")
    return actions


def _coerce_nonnegative_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return max(number, 0)


def _transition(state: dict[str, Any], status: str, *, event: Any, reason: str) -> None:
    created_at = getattr(event, "created_at", None)
    created_at_text = created_at.isoformat() if created_at is not None else None
    state["status"] = status
    state["last_event_index"] = getattr(event, "event_index", None)
    state["last_event_type"] = getattr(event, "event_type", None)
    state["last_event_at"] = created_at_text
    state["transition_reason"] = reason
    if status == "running" and state.get("started_at") is None:
        state["started_at"] = created_at_text
    if status in TERMINAL_STATUSES:
        state["completed_at"] = created_at_text
