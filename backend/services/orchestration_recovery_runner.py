# -*- coding: utf-8 -*-
"""Shared interrupted-orchestration recovery runtime runner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict

from sqlalchemy.orm import Session

from services.orchestration_events import (
    record_scheduler_recovery_state_rebuilt,
    record_task_run_recovery_started,
)
from services.orchestration_finalizer import finalize_orchestration_task_run, summarize_orchestration_result
from services.orchestration_guards import fail_recovery_guard
from services.orchestration_runtime_runner import (
    NonstreamOrchestrationRuntimeDeps,
    run_nonstream_orchestration_runtime,
)
from services.orchestration_scheduler import OrchestrationRuntimeQueue
from services.orchestration_step_state import OrchestrationStepOutputState
from services.run_ledger import append_task_event
from services.task_run_control import TaskRunCancelledError, raise_if_task_run_cancelled


@dataclass(frozen=True)
class OrchestrationRecoveryRuntimeResult:
    resumed: bool
    reason: str
    status: str
    detail: str
    owner: str
    lease_expires_at: datetime | None = None


@dataclass(frozen=True)
class OrchestrationRecoveryRuntimeDeps:
    build_checkpoint_snapshot: Callable[[Any], Dict[str, Any]]
    describe_recovery_continuation_state: Callable[[Any], Dict[str, Any]]
    rebuild_recovery_state: Callable[..., tuple[list[Dict[str, str]], Dict[str, list[Dict[str, str]]], str, list[str]]]
    execute_turn: Callable[..., Awaitable[tuple[str, Any]]]
    publish_message: Callable[..., Awaitable[Any]]
    message_metadata: Dict[str, Any] | None
    renew_lease: Callable[[], datetime]
    recovery_owner: str


async def run_orchestration_recovery_runtime(
    *,
    db: Session,
    task_run: Any,
    task_run_id: int,
    chatroom: Any,
    project: Any,
    agents: list[Any],
    agent_names: list[str],
    resolved_agents: list[Any],
    plan: Any,
    orchestration_policy: Any,
    trigger: str,
    lease_expires_at: datetime | None,
    deps: OrchestrationRecoveryRuntimeDeps,
) -> OrchestrationRecoveryRuntimeResult:
    """Run recovery for an already prepared orchestration runtime."""

    queue = OrchestrationRuntimeQueue(plan)
    recovery_checkpoint_snapshot = deps.build_checkpoint_snapshot(task_run)
    recovery_continuation_state = deps.describe_recovery_continuation_state(recovery_checkpoint_snapshot)
    record_task_run_recovery_started(
        db,
        task_run,
        run_kind=task_run.run_kind,
        requested_agents=agent_names,
        resolved_agents=[getattr(agent, "name", None) or "" for agent in resolved_agents],
        project_id=project.id if project else None,
        chatroom_id=chatroom.id,
        trigger=trigger,
        recovery_owner=deps.recovery_owner,
        recovery_lease_expires_at=lease_expires_at,
        checkpoint_snapshot=recovery_checkpoint_snapshot,
        recovery_continuation_state=recovery_continuation_state,
        runner_policy=orchestration_policy,
    )
    completed_turns, pending_handoffs, last_blocking_result, completed_step_ids = deps.rebuild_recovery_state(
        db,
        task_run=task_run,
        queue=queue,
    )
    output_state = OrchestrationStepOutputState(
        completed_turns=completed_turns,
        last_blocking_result=last_blocking_result,
    )
    record_scheduler_recovery_state_rebuilt(
        db,
        task_run,
        queue,
        checkpoint_snapshot=recovery_checkpoint_snapshot,
        recovery_continuation_state=recovery_continuation_state,
        runner_policy=orchestration_policy,
        completed_step_ids=completed_step_ids,
        replayed_turn_count=len(completed_turns),
    )

    initial_runtime = queue.runtime_snapshot()
    if (
        initial_runtime.ready_step_count == 0
        and initial_runtime.completed_step_count < initial_runtime.step_count
    ):
        outcome = fail_recovery_guard(
            db,
            task_run,
            task_run_id=task_run_id,
            kind="no_runnable_steps",
            owner=deps.recovery_owner,
            lease_expires_at=lease_expires_at,
            payload={"runner_policy": orchestration_policy.to_payload(), **queue.plan.to_payload(), "runtime": queue.runtime_snapshot_payload()},
        )
        return OrchestrationRecoveryRuntimeResult(
            resumed=False,
            reason=outcome.reason,
            status=outcome.status,
            detail=outcome.detail,
            owner=outcome.owner or deps.recovery_owner,
            lease_expires_at=outcome.lease_expires_at,
        )

    def _before_recovery_step():
        nonlocal lease_expires_at
        lease_expires_at = deps.renew_lease()
        return None

    def _build_recovery_step_context(step, agent, agent_label):
        step_checkpoint_snapshot = deps.build_checkpoint_snapshot(task_run)
        step_recovery_continuation_state = deps.describe_recovery_continuation_state(step_checkpoint_snapshot)
        return {
            "checkpoint_snapshot": step_checkpoint_snapshot,
            "dispatch_extra": {"recovery_continuation_state": step_recovery_continuation_state},
            "include_result": False,
            "summary_prefix": "Recovery",
            "recovered": True,
        }

    try:
        await run_nonstream_orchestration_runtime(
            db=db,
            task_run=task_run,
            queue=queue,
            resolved_agents=resolved_agents,
            chatroom_id=chatroom.id,
            project=project,
            agents=agents,
            user_message=task_run.user_request or "",
            client_turn_id=task_run.client_turn_id,
            output_state=output_state,
            pending_handoffs=pending_handoffs,
            orchestration_policy=orchestration_policy,
            deps=NonstreamOrchestrationRuntimeDeps(
                execute_turn=deps.execute_turn,
                publish_message=deps.publish_message,
                message_metadata=deps.message_metadata,
                before_next_step=_before_recovery_step,
                build_step_context=_build_recovery_step_context,
            ),
        )
    except TaskRunCancelledError:
        refreshed_status = getattr(task_run, "status", None) or "cancelled"
        return OrchestrationRecoveryRuntimeResult(
            resumed=False,
            reason="cancelled",
            status=str(refreshed_status),
            detail="Recovery stopped because the task run was cancelled.",
            owner=deps.recovery_owner,
            lease_expires_at=lease_expires_at,
        )

    raise_if_task_run_cancelled(db, task_run, context="recovery finalize")
    final_runtime = queue.runtime_snapshot()
    if final_runtime.completed_step_count < final_runtime.step_count:
        outcome = fail_recovery_guard(
            db,
            task_run,
            task_run_id=task_run_id,
            kind="incomplete",
            owner=deps.recovery_owner,
            lease_expires_at=lease_expires_at,
            payload={"runner_policy": orchestration_policy.to_payload(), **queue.plan.to_payload(), "runtime": queue.runtime_snapshot_payload()},
        )
        return OrchestrationRecoveryRuntimeResult(
            resumed=False,
            reason=outcome.reason,
            status=outcome.status,
            detail=outcome.detail,
            owner=outcome.owner or deps.recovery_owner,
            lease_expires_at=outcome.lease_expires_at,
        )

    recovery_summary = summarize_orchestration_result(
        last_blocking_result=output_state.last_blocking_result,
        completed_turns=completed_turns,
        fallback="Recovered orchestration completed.",
    )
    append_task_event(
        db,
        task_run,
        "task_run_recovery_completed",
        summary="Interrupted orchestration recovery completed.",
        payload={
            "task_run_id": task_run.id,
            "completed_step_count": queue.runtime_snapshot().completed_step_count,
            "step_count": len(queue.plan.steps),
            "recovery_continuation_state": recovery_continuation_state,
        },
    )
    finalize_orchestration_task_run(
        db,
        task_run,
        last_blocking_result=output_state.last_blocking_result,
        completed_turns=completed_turns,
        fallback="Recovered orchestration completed.",
    )
    return OrchestrationRecoveryRuntimeResult(
        resumed=True,
        reason="completed",
        status="completed",
        detail=recovery_summary,
        owner=deps.recovery_owner,
        lease_expires_at=lease_expires_at,
    )
