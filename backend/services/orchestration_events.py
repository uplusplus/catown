# -*- coding: utf-8 -*-
"""Shared ledger event helpers for orchestration scheduler execution."""

from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from models.database import TaskRun
from services.run_ledger import append_task_event


def scheduler_event_payload(queue: Any, step: Any, *, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = step.to_payload()
    payload["step_state"] = queue.runtime_state_payload_for_step(step.step_id)
    payload["runtime"] = queue.runtime_snapshot_payload()
    if extra:
        payload.update(extra)
    return payload


def scheduler_plan_payload(queue: Any, *, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = queue.plan.to_payload()
    payload["runtime"] = queue.runtime_snapshot_payload()
    if extra:
        payload.update(extra)
    return payload


def stage_policy_payload(stage_policy: Any) -> Dict[str, Any] | None:
    return stage_policy.to_payload() if stage_policy is not None else None


def record_scheduler_step_dispatched(
    db: Session,
    task_run: TaskRun | None,
    queue: Any,
    step: Any,
    *,
    agent_name: str,
    summary_prefix: str = "Scheduler",
    stage_policy: Any = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Any:
    payload_extra = {"stage_policy": stage_policy_payload(stage_policy)}
    if extra:
        payload_extra.update(extra)
    return append_task_event(
        db,
        task_run,
        "scheduler_step_dispatched",
        agent_name=agent_name,
        summary=f"{summary_prefix} dispatched {step.dispatch_kind} work to {agent_name}.",
        payload=scheduler_event_payload(queue, step, extra=payload_extra),
    )


def record_scheduler_step_completed(
    db: Session,
    task_run: TaskRun | None,
    queue: Any,
    step: Any,
    *,
    agent_name: str,
    ready_steps: list[Any],
    completed_with_output: bool,
    summary_prefix: str = "Scheduler",
    stage_policy: Any = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Any:
    payload_extra = {
        "stage_policy": stage_policy_payload(stage_policy),
        "released_step_ids": [next_step.step_id for next_step in ready_steps],
        "released_step_count": len(ready_steps),
        "completed_with_output": bool(completed_with_output),
    }
    if extra:
        payload_extra.update(extra)
    return append_task_event(
        db,
        task_run,
        "scheduler_step_completed",
        agent_name=agent_name,
        summary=(
            f"{summary_prefix} marked {agent_name} complete and released {len(ready_steps)} waiting step(s)."
            if ready_steps
            else f"{summary_prefix} marked {agent_name} complete."
        ),
        payload=scheduler_event_payload(queue, step, extra=payload_extra),
    )


def record_scheduler_step_resumed(
    db: Session,
    task_run: TaskRun | None,
    queue: Any,
    step: Any,
    *,
    resumed_by_step_id: str,
    resumed_by_agent: str,
    summary_prefix: str = "Scheduler",
    stage_policy: Any = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Any:
    payload_extra = {
        "stage_policy": stage_policy_payload(stage_policy),
        "resumed_by_step_id": resumed_by_step_id,
        "resumed_by_agent": resumed_by_agent,
    }
    if extra:
        payload_extra.update(extra)
    return append_task_event(
        db,
        task_run,
        "scheduler_step_resumed",
        agent_name=step.agent_name,
        summary=f"{summary_prefix} resumed {step.agent_name} after {resumed_by_agent}.",
        payload=scheduler_event_payload(queue, step, extra=payload_extra),
    )


def record_scheduler_step_failed(
    db: Session,
    task_run: TaskRun | None,
    queue: Any,
    step: Any,
    *,
    agent_name: str,
    error: Any,
    summary_prefix: str = "Scheduler",
    stage_policy: Any = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Any:
    payload_extra = {
        "stage_policy": stage_policy_payload(stage_policy),
        "error": str(error)[:2000],
    }
    if extra:
        payload_extra.update(extra)
    return append_task_event(
        db,
        task_run,
        "scheduler_step_failed",
        agent_name=agent_name,
        summary=f"{summary_prefix} marked {agent_name} failed.",
        payload=scheduler_event_payload(queue, step, extra=payload_extra),
    )


def record_scheduler_step_cancelled(
    db: Session,
    task_run: TaskRun | None,
    subagent: dict[str, Any],
    *,
    cancelled_by: str,
    note: str = "",
) -> Any:
    agent_name = str(subagent.get("agent_name") or "").strip() or None
    return append_task_event(
        db,
        task_run,
        "scheduler_step_cancelled",
        agent_name=agent_name,
        summary=f"Cancelled subagent {agent_name or subagent.get('step_id')}.",
        payload={
            "step_id": subagent.get("step_id"),
            "position": subagent.get("position"),
            "agent_name": subagent.get("agent_name"),
            "agent_type": subagent.get("agent_type"),
            "dispatch_kind": subagent.get("dispatch_kind"),
            "wait_for_step_id": subagent.get("wait_for_step_id"),
            "attached_to_step_id": subagent.get("attached_to_step_id"),
            "previous_status": subagent.get("status"),
            "cancelled_by": cancelled_by,
            "note": note or None,
        },
    )
