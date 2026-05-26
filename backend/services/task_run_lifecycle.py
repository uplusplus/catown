# -*- coding: utf-8 -*-
"""Single entry point for all TaskRun terminal transitions.

All paths that end a TaskRun (success, failure, cancellation, interruption,
watchdog sweep) MUST go through ``terminalize_task_run`` so that:
  1. No caller can silently leave a TaskRun in ``running``.
  2. The transition table is always validated.
  3. A terminal event is always appended before the status change.
  4. Recovery lease fields are cleared on completion.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from models.database import TaskRun
from models.enums import EventType
from services.run_ledger import append_task_event, complete_task_run


# Canonical terminal status → default event type mapping.
_TERMINAL_EVENT_MAP: dict[str, EventType] = {
    "completed": EventType.AGENT_TURN_COMPLETED,
    "failed": EventType.TASK_RUN_FAILED,
    "cancelled": EventType.TASK_RUN_CANCELLED,
}


def terminalize_task_run(
    db: Session,
    task_run: TaskRun | None,
    *,
    status: str,
    summary: str = "",
    error: Exception | str | None = None,
    agent_name: str | None = None,
    event_type: EventType | str | None = None,
    payload: Any = None,
) -> TaskRun | None:
    """Transition a TaskRun to a terminal status, idempotently.

    If the TaskRun is already terminal (completed/failed/cancelled), this is a
    no-op and returns the task_run unchanged.  This makes it safe to call from
    any error path without double-terminalizing.

    Parameters
    ----------
    status : str
        Target terminal status: ``"completed"``, ``"failed"``, or ``"cancelled"``.
    summary : str
        Human-readable summary stored on the TaskRun.
    error : Exception | str | None
        If provided, converted to string and merged into the event payload.
    agent_name : str | None
        Agent name recorded on the terminal event.
    event_type : EventType | str | None
        Override the default event type (normally inferred from *status*).
    payload : Any
        Extra payload merged into the terminal event.
    """
    if task_run is None:
        return None

    current = (task_run.status or "").strip().lower()

    # Idempotent: already terminal → no-op.
    if current in ("completed", "failed", "cancelled"):
        return task_run

    resolved_status = status.strip().lower()
    if resolved_status not in _TERMINAL_EVENT_MAP:
        raise ValueError(f"Unsupported terminal status: {status!r}")

    # Build event payload.
    merged_payload: dict[str, Any] = {}
    if payload is not None and isinstance(payload, dict):
        merged_payload.update(payload)
    if error is not None:
        merged_payload["error"] = str(error)

    resolved_event_type = event_type or _TERMINAL_EVENT_MAP[resolved_status]
    resolved_summary = (summary or "").strip()
    if not resolved_summary and error is not None:
        resolved_summary = str(error)[:280]

    append_task_event(
        db,
        task_run,
        resolved_event_type,
        agent_name=agent_name,
        summary=resolved_summary,
        payload=merged_payload or None,
    )

    return complete_task_run(
        db,
        task_run,
        status=resolved_status,
        summary=resolved_summary,
    )
