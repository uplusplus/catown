# -*- coding: utf-8 -*-
"""Subagent lifecycle projection helpers for scheduler-driven orchestration."""

from __future__ import annotations

import json
from typing import Any


TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def build_subagent_lifecycle_from_events(events: list[Any]) -> dict[str, Any]:
    """Project task-run scheduler events into a Codex-style subagent lifecycle view."""

    states: dict[str, dict[str, Any]] = {}
    transition_count = 0

    for event in events:
        event_type = str(getattr(event, "event_type", "") or "")
        payload = _load_event_payload(event)
        if event_type == "scheduler_plan_created":
            for step in _steps_from_plan_payload(payload):
                step_id = _step_id(step)
                if not step_id:
                    continue
                if step_id not in states:
                    states[step_id] = _initial_state(step)
                    transition_count += 1
            continue

        if event_type not in {
            "scheduler_step_dispatched",
            "scheduler_step_resumed",
            "scheduler_step_completed",
            "scheduler_step_failed",
            "scheduler_step_cancelled",
        }:
            continue

        step = payload if isinstance(payload, dict) else {}
        step_id = _step_id(step)
        if not step_id:
            continue
        state = states.setdefault(step_id, _initial_state(step))
        _merge_step_identity(state, step)
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


def cancellable_subagents_from_lifecycle(lifecycle: Any) -> list[dict[str, Any]]:
    """Return subagents that can still be moved to a cancelled terminal state."""

    state = lifecycle if isinstance(lifecycle, dict) else {}
    subagents = state.get("subagents") if isinstance(state.get("subagents"), list) else []
    cancellable: list[dict[str, Any]] = []
    for subagent in subagents:
        if not isinstance(subagent, dict):
            continue
        status = str(subagent.get("status") or "").strip().lower()
        if status and status not in TERMINAL_STATUSES:
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
        "agent_type": None,
        "dispatch_kind": None,
        "position": None,
        "wait_for_step_id": None,
        "attached_to_step_id": None,
        "status": "spawned",
        "started_at": None,
        "completed_at": None,
        "last_event_type": None,
        "last_event_at": None,
        "transition_reason": "planned",
    }
    _merge_step_identity(state, step)
    return state


def _merge_step_identity(state: dict[str, Any], step: dict[str, Any]) -> None:
    for key in [
        "agent_name",
        "agent_type",
        "dispatch_kind",
        "position",
        "wait_for_step_id",
        "attached_to_step_id",
    ]:
        value = step.get(key)
        if value is not None:
            state[key] = value


def _transition(state: dict[str, Any], status: str, *, event: Any, reason: str) -> None:
    created_at = getattr(event, "created_at", None)
    created_at_text = created_at.isoformat() if created_at is not None else None
    state["status"] = status
    state["last_event_type"] = getattr(event, "event_type", None)
    state["last_event_at"] = created_at_text
    state["transition_reason"] = reason
    if status == "running" and state.get("started_at") is None:
        state["started_at"] = created_at_text
    if status in TERMINAL_STATUSES:
        state["completed_at"] = created_at_text
