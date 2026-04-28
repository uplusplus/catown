# -*- coding: utf-8 -*-
"""Shared early-exit guard helpers for orchestration runtime setup and recovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from models.database import TaskRun
from services.orchestration_finalizer import fail_orchestration_task_run


@dataclass(frozen=True)
class RecoveryFailureOutcome:
    reason: str
    status: str
    detail: str
    owner: str | None = None
    lease_expires_at: datetime | None = None


def fail_orchestration_preflight(
    db: Session,
    task_run: TaskRun | None,
    *,
    requested_agents: list[str],
    kind: str,
    streaming: bool = False,
) -> None:
    """Record shared sync/stream preflight failures before orchestration starts."""

    if kind == "no_valid_agents":
        summary = (
            "No valid agents resolved for streaming orchestration."
            if streaming
            else "No valid agents resolved for orchestration."
        )
    elif kind == "runtime_unprepared":
        summary = (
            "Streaming orchestration runtime was not prepared."
            if streaming
            else "Orchestration runtime was not prepared."
        )
    else:
        raise ValueError(f"Unsupported orchestration preflight kind: {kind}")

    fail_orchestration_task_run(
        db,
        task_run,
        summary=summary,
        payload={"requested_agents": requested_agents},
    )


def fail_recovery_guard(
    db: Session,
    task_run: TaskRun | None,
    *,
    task_run_id: int,
    kind: str,
    owner: str,
    lease_expires_at: datetime | None,
    requested_agents: list[str] | None = None,
    payload: dict[str, Any] | None = None,
) -> RecoveryFailureOutcome:
    """Record a shared recovery guard failure and return a normalized outcome."""

    resolved_requested_agents = list(requested_agents or [])
    extra_payload = dict(payload or {})
    if resolved_requested_agents and "requested_agents" not in extra_payload:
        extra_payload["requested_agents"] = resolved_requested_agents

    if kind == "chatroom_missing":
        fail_orchestration_task_run(
            db,
            task_run,
            event_type="task_run_recovery_failed",
            summary="Recovery failed: chatroom missing.",
            event_summary="Recovery aborted because the chatroom no longer exists.",
            payload=extra_payload or {"task_run_id": getattr(task_run, "id", task_run_id)},
        )
        return RecoveryFailureOutcome(
            reason="chatroom_missing",
            status="failed",
            detail="Recovery failed: chatroom missing.",
            owner=owner,
            lease_expires_at=lease_expires_at,
        )

    if kind == "no_valid_agents":
        fail_orchestration_task_run(
            db,
            task_run,
            event_type="task_run_recovery_failed",
            summary="Recovery failed: no valid agents resolved.",
            event_summary="Recovery aborted because no valid orchestration agents could be resolved.",
            payload=extra_payload,
        )
        return RecoveryFailureOutcome(
            reason="no_valid_agents",
            status="failed",
            detail="Recovery failed: no valid agents resolved.",
            owner=owner,
            lease_expires_at=lease_expires_at,
        )

    if kind == "no_runnable_plan":
        fail_orchestration_task_run(
            db,
            task_run,
            event_type="task_run_recovery_failed",
            summary="Recovery failed: no runnable orchestration plan.",
            event_summary="Recovery failed because orchestration runtime preparation returned no runnable plan.",
            payload=extra_payload,
        )
        return RecoveryFailureOutcome(
            reason="no_runnable_plan",
            status="failed",
            detail="Recovery failed: no runnable orchestration plan.",
            owner=owner,
            lease_expires_at=lease_expires_at,
        )

    if kind == "no_runnable_steps":
        fail_orchestration_task_run(
            db,
            task_run,
            event_type="task_run_recovery_failed",
            summary="Recovery failed: no runnable steps after rebuild.",
            event_summary="Recovery rebuilt the scheduler state but found no runnable steps.",
            payload=extra_payload,
        )
        return RecoveryFailureOutcome(
            reason="no_runnable_steps",
            status="failed",
            detail="Recovery failed: no runnable steps after rebuild.",
            owner=owner,
            lease_expires_at=lease_expires_at,
        )

    if kind == "incomplete":
        fail_orchestration_task_run(
            db,
            task_run,
            event_type="task_run_recovery_failed",
            summary="Recovery failed: orchestration remained incomplete.",
            event_summary="Recovery stopped before all scheduled steps completed.",
            payload=extra_payload,
        )
        return RecoveryFailureOutcome(
            reason="incomplete",
            status="failed",
            detail="Recovery failed: orchestration remained incomplete.",
            owner=owner,
            lease_expires_at=lease_expires_at,
        )

    raise ValueError(f"Unsupported recovery guard kind: {kind}")
