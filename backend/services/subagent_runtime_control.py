# -*- coding: utf-8 -*-
"""Shared runtime control helpers for orchestration subagent handles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.subagent_lifecycle import (
    build_subagent_wait_result,
    cancellable_subagent_handles,
    cancellable_subagents_from_lifecycle,
    find_subagent_lifecycle_entry,
    find_subagent_runtime_handle,
)


@dataclass(frozen=True)
class SubagentRuntimeControlError(Exception):
    """Service-layer error for invalid subagent runtime operations."""

    status_code: int
    detail: str


def build_task_run_subagent_projection(task_run: Any) -> dict[str, Any]:
    """Return the current lifecycle + handle projection for one task run."""

    from services.run_ledger import build_task_run_checkpoint_snapshot

    checkpoint_snapshot = build_task_run_checkpoint_snapshot(task_run)
    return {
        "task_run_id": task_run.id,
        "status": task_run.status,
        "subagent_lifecycle_summary": checkpoint_snapshot.get("subagent_lifecycle_summary"),
        "subagent_handles_summary": checkpoint_snapshot.get("subagent_handles_summary"),
        "subagent_lifecycle": checkpoint_snapshot.get("subagent_lifecycle"),
        "subagent_handles": checkpoint_snapshot.get("subagent_handles"),
    }


def observe_task_run_subagent(task_run: Any, step_id: str, *, since_event_index: int | None = None) -> dict[str, Any]:
    """Observe one subagent handle from the current checkpoint projection."""

    from services.run_ledger import build_task_run_checkpoint_snapshot

    checkpoint_snapshot = build_task_run_checkpoint_snapshot(task_run)
    handles = checkpoint_snapshot.get("subagent_handles")
    handle = find_subagent_runtime_handle(handles, step_id)
    if handle is None:
        raise SubagentRuntimeControlError(404, "Subagent handle not found.")

    latest_event_index = _latest_task_run_event_index(task_run)
    wait_result = build_subagent_wait_result(
        handle=handle,
        current_event_index=latest_event_index,
        since_event_index=since_event_index,
    )
    return {
        "task_run_id": task_run.id,
        "status": task_run.status,
        "step_id": step_id,
        "subagent_handle": handle,
        "wait_result": wait_result,
    }


def cancel_task_run_subagent_handle(
    db: Any,
    task_run: Any,
    *,
    step_id: str,
    cancelled_by: str = "user",
    note: str = "",
) -> dict[str, Any]:
    """Cancel one projected subagent handle without always terminalizing the whole task run."""

    if (task_run.status or "").lower() != "running":
        raise SubagentRuntimeControlError(409, "Only running task runs can cancel subagents.")

    from services.orchestration_events import record_scheduler_step_cancelled
    from services.run_ledger import (
        append_task_event,
        build_task_run_checkpoint_snapshot,
        complete_task_run,
        serialize_task_run_detail,
    )

    checkpoint_snapshot = build_task_run_checkpoint_snapshot(task_run)
    handles = checkpoint_snapshot.get("subagent_handles")
    lifecycle = checkpoint_snapshot.get("subagent_lifecycle")
    handle = find_subagent_runtime_handle(handles, step_id)
    if handle is None:
        raise SubagentRuntimeControlError(404, "Subagent handle not found.")
    if not handle.get("cancellable"):
        raise SubagentRuntimeControlError(409, "Subagent handle is not cancellable.")

    subagent = find_subagent_lifecycle_entry(lifecycle, step_id)
    if subagent is None:
        raise SubagentRuntimeControlError(404, "Subagent state not found.")

    record_scheduler_step_cancelled(
        db,
        task_run,
        subagent,
        cancelled_by=cancelled_by,
        note=note,
    )
    db.refresh(task_run)

    updated_snapshot = build_task_run_checkpoint_snapshot(task_run)
    updated_handles = updated_snapshot.get("subagent_handles")
    updated_handle = find_subagent_runtime_handle(updated_handles, step_id)
    remaining_cancellable_handles = cancellable_subagent_handles(updated_handles)
    task_run_cancelled = len(remaining_cancellable_handles) == 0

    append_task_event(
        db,
        task_run,
        "task_run_subagent_cancelled",
        summary=note or f"Cancelled subagent {step_id} from the API.",
        payload={
            "task_run_id": task_run.id,
            "step_id": step_id,
            "cancelled_by": cancelled_by,
            "note": note or None,
            "task_run_cancelled": task_run_cancelled,
            "remaining_cancellable_subagent_count": len(remaining_cancellable_handles),
            "subagent_handle": updated_handle,
        },
    )

    if task_run_cancelled:
        append_task_event(
            db,
            task_run,
            "task_run_cancelled",
            summary=note or f"Task run cancelled after subagent {step_id} was cancelled.",
            payload={
                "task_run_id": task_run.id,
                "run_kind": task_run.run_kind,
                "cancelled_by": cancelled_by,
                "cancelled_subagent_count": 1,
                "cancelled_step_ids": [step_id],
                "note": note or None,
                "checkpoint_snapshot": updated_snapshot,
            },
        )
        complete_task_run(db, task_run, status="cancelled", summary=note or "Task run cancelled.")
        db.refresh(task_run)

    return {
        "message": (
            "Subagent cancelled and task run terminalized."
            if task_run_cancelled
            else "Subagent cancelled."
        ),
        "cancelled": True,
        "task_run_cancelled": task_run_cancelled,
        "task_run_id": task_run.id,
        "step_id": step_id,
        "status": task_run.status,
        "remaining_cancellable_subagent_count": len(remaining_cancellable_handles),
        "detail": serialize_task_run_detail(task_run),
    }


def close_task_run_subagent_handle(
    db: Any,
    task_run: Any,
    *,
    step_id: str,
    closed_by: str = "user",
    note: str = "",
) -> dict[str, Any]:
    """Close one terminal subagent handle and archive it from the active handle set."""

    from services.run_ledger import (
        append_task_event,
        build_task_run_checkpoint_snapshot,
        serialize_task_run_detail,
    )

    checkpoint_snapshot = build_task_run_checkpoint_snapshot(task_run)
    handles = checkpoint_snapshot.get("subagent_handles")
    handle = find_subagent_runtime_handle(handles, step_id)
    if handle is None:
        raise SubagentRuntimeControlError(404, "Subagent handle not found.")
    if not handle.get("terminal"):
        raise SubagentRuntimeControlError(409, "Only terminal subagent handles can be closed.")
    if handle.get("closed"):
        raise SubagentRuntimeControlError(409, "Subagent handle is already closed.")

    append_task_event(
        db,
        task_run,
        "subagent_handle_closed",
        summary=note or f"Closed subagent handle {step_id} from the API.",
        payload={
            "task_run_id": task_run.id,
            "step_id": step_id,
            "closed_by": closed_by,
            "note": note or None,
            "status": handle.get("status"),
            "control_state": handle.get("control_state"),
        },
    )
    db.refresh(task_run)

    updated_detail = serialize_task_run_detail(task_run)
    updated_handle = find_subagent_runtime_handle(
        updated_detail.get("checkpoint_snapshot", {}).get("subagent_handles"),
        step_id,
    )
    return {
        "message": "Subagent handle closed.",
        "closed": True,
        "task_run_id": task_run.id,
        "step_id": step_id,
        "subagent_handle": updated_handle,
        "detail": updated_detail,
    }


def cancel_task_run_with_subagents(
    db: Any,
    task_run: Any,
    *,
    cancelled_by: str = "user",
    note: str = "",
) -> dict[str, Any]:
    """Cancel a running task run and terminalize any non-terminal subagent states."""

    if (task_run.status or "").lower() != "running":
        raise SubagentRuntimeControlError(409, "Only running task runs can be cancelled.")

    from services.orchestration_events import record_scheduler_step_cancelled
    from services.run_ledger import (
        append_task_event,
        build_task_run_checkpoint_snapshot,
        complete_task_run,
        serialize_task_run_detail,
    )

    checkpoint_snapshot = build_task_run_checkpoint_snapshot(task_run)
    lifecycle = checkpoint_snapshot.get("subagent_lifecycle")
    cancellable_subagents = cancellable_subagents_from_lifecycle(lifecycle)

    for subagent in cancellable_subagents:
        record_scheduler_step_cancelled(
            db,
            task_run,
            subagent,
            cancelled_by=cancelled_by,
            note=note,
        )

    append_task_event(
        db,
        task_run,
        "task_run_cancelled",
        summary=note or "Task run cancelled from the API.",
        payload={
            "task_run_id": task_run.id,
            "run_kind": task_run.run_kind,
            "cancelled_by": cancelled_by,
            "cancelled_subagent_count": len(cancellable_subagents),
            "cancelled_step_ids": [
                subagent.get("step_id")
                for subagent in cancellable_subagents
                if subagent.get("step_id") is not None
            ],
            "note": note or None,
            "checkpoint_snapshot": checkpoint_snapshot,
        },
    )
    complete_task_run(db, task_run, status="cancelled", summary=note or "Task run cancelled.")
    db.refresh(task_run)
    return {
        "message": "Task run cancelled.",
        "cancelled": True,
        "task_run_id": task_run.id,
        "status": task_run.status,
        "cancelled_subagent_count": len(cancellable_subagents),
        "detail": serialize_task_run_detail(task_run),
    }


def _latest_task_run_event_index(task_run: Any) -> int:
    events = list(getattr(task_run, "events", []) or [])
    if not events:
        return 0
    try:
        return max(int(getattr(event, "event_index", 0) or 0) for event in events)
    except (TypeError, ValueError):
        return 0
