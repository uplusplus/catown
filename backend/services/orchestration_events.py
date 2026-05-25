# -*- coding: utf-8 -*-
"""Shared ledger event helpers for orchestration scheduler execution."""

from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from models.database import TaskRun
from models.enums import EventType
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


def runner_policy_payload(runner_policy: Any) -> Dict[str, Any] | None:
    return runner_policy.to_payload() if runner_policy is not None else None


def record_orchestration_started(
    db: Session,
    task_run: TaskRun | None,
    *,
    requested_agents: list[str],
    resolved_agents: list[str],
    project_id: int | None,
    runner_policy: Any,
    client_turn_id: str | None = None,
    streaming: bool = False,
) -> Any:
    payload = {
        "requested_agents": requested_agents,
        "resolved_agents": resolved_agents,
        "project_id": project_id,
        "runner_policy": runner_policy_payload(runner_policy),
    }
    if client_turn_id is not None:
        payload["client_turn_id"] = client_turn_id
    return append_task_event(
        db,
        task_run,
        EventType.ORCHESTRATION_STARTED,
        summary=(
            "Multi-agent streaming orchestration started."
            if streaming
            else "Multi-agent orchestration started."
        ),
        payload=payload,
    )


def record_scheduler_plan_created(
    db: Session,
    task_run: TaskRun | None,
    queue: Any,
    *,
    runner_policy: Any,
    streaming: bool = False,
) -> Any:
    mode = getattr(queue.plan, "mode", None)
    if streaming:
        summary = (
            "Built a blocking-chain streaming schedule with sidecars."
            if mode == "blocking_chain_with_sidecars"
            else "Built a linear blocking streaming schedule."
        )
    else:
        summary = (
            "Built a blocking-chain orchestration schedule with sidecars."
            if mode == "blocking_chain_with_sidecars"
            else "Built a linear blocking orchestration schedule."
        )
    return append_task_event(
        db,
        task_run,
        EventType.SCHEDULER_PLAN_CREATED,
        summary=summary,
        payload=scheduler_plan_payload(
            queue,
            extra={"runner_policy": runner_policy_payload(runner_policy)},
        ),
    )


def record_task_run_recovery_started(
    db: Session,
    task_run: TaskRun | None,
    *,
    run_kind: str,
    requested_agents: list[str],
    resolved_agents: list[str],
    project_id: int | None,
    chatroom_id: int,
    trigger: str,
    recovery_owner: str,
    recovery_lease_expires_at: Any,
    checkpoint_snapshot: Dict[str, Any],
    recovery_continuation_state: Dict[str, Any],
    runner_policy: Any,
) -> Any:
    return append_task_event(
        db,
        task_run,
        EventType.TASK_RUN_RECOVERY_STARTED,
        summary=(
            "Manual resume started recovery for an interrupted orchestration run."
            if trigger == "manual"
            else "Detected an interrupted orchestration run and started recovery."
        ),
        payload={
            "task_run_id": getattr(task_run, "id", None),
            "run_kind": run_kind,
            "requested_agents": requested_agents,
            "resolved_agents": resolved_agents,
            "project_id": project_id,
            "chatroom_id": chatroom_id,
            "trigger": trigger,
            "recovery_owner": recovery_owner,
            "recovery_lease_expires_at": (
                recovery_lease_expires_at.isoformat() if recovery_lease_expires_at else None
            ),
            "checkpoint_snapshot": checkpoint_snapshot,
            "recovery_continuation_state": recovery_continuation_state,
            "runner_policy": runner_policy_payload(runner_policy),
        },
    )


def record_scheduler_recovery_state_rebuilt(
    db: Session,
    task_run: TaskRun | None,
    queue: Any,
    *,
    checkpoint_snapshot: Dict[str, Any],
    recovery_continuation_state: Dict[str, Any],
    runner_policy: Any,
    completed_step_ids: list[str],
    replayed_turn_count: int,
) -> Any:
    return append_task_event(
        db,
        task_run,
        EventType.SCHEDULER_RECOVERY_STATE_REBUILT,
        summary=(
            f"Rebuilt scheduler state with {len(completed_step_ids)} completed step(s) and "
            f"{queue.runtime_snapshot().ready_step_count} ready step(s)."
        ),
        payload=scheduler_plan_payload(
            queue,
            extra={
                "checkpoint_snapshot": checkpoint_snapshot,
                "recovery_continuation_state": recovery_continuation_state,
                "runner_policy": runner_policy_payload(runner_policy),
                "recovery": {
                    "completed_step_ids": completed_step_ids,
                    "replayed_turn_count": replayed_turn_count,
                },
            },
        ),
    )


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
        EventType.SCHEDULER_STEP_DISPATCHED,
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
        EventType.SCHEDULER_STEP_COMPLETED,
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
        EventType.SCHEDULER_STEP_RESUMED,
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
        EventType.SCHEDULER_STEP_FAILED,
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
        EventType.SCHEDULER_STEP_CANCELLED,
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
