# -*- coding: utf-8 -*-
"""Shared non-streaming scheduler step runner for orchestration execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List

from sqlalchemy.orm import Session

from models.database import TaskRun
from services.orchestration_events import record_scheduler_step_dispatched, record_scheduler_step_failed
from services.orchestration_handoffs import (
    acknowledge_orchestration_step_handoffs,
    build_orchestration_previous_work,
    claim_orchestration_step_handoffs,
    fail_orchestration_step_handoffs,
)
from services.orchestration_step_completion import complete_orchestration_scheduler_step
from services.orchestration_step_state import OrchestrationStepOutputState, record_orchestration_step_output


ExecuteTurn = Callable[..., Awaitable[tuple[str, Any]]]
PublishMessage = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class OrchestrationStepRunResult:
    content: str
    message: Any
    ready_steps: list[Any]


async def run_nonstream_orchestration_step(
    *,
    db: Session,
    task_run: TaskRun | None,
    queue: Any,
    step: Any,
    agent: Any,
    agent_name: str,
    chatroom_id: int,
    project: Any,
    agents: list[Any],
    user_message: str,
    client_turn_id: str | None,
    output_state: OrchestrationStepOutputState,
    pending_handoffs: Dict[str, List[Dict[str, str]]],
    orchestration_policy: Any,
    stage_policy: Any = None,
    execute_turn: ExecuteTurn,
    publish_message: PublishMessage,
    message_metadata: Dict[str, Any] | None = None,
    extra_context: str = "",
    checkpoint_snapshot: Dict[str, Any] | None = None,
    dispatch_extra: Dict[str, Any] | None = None,
    include_result: bool = True,
    summary_prefix: str = "Scheduler",
    recovered: bool = False,
) -> OrchestrationStepRunResult:
    """Dispatch, execute, publish, record output, and complete one non-stream orchestration step."""

    resolved_dispatch_extra = dict(dispatch_extra or {})
    if recovered:
        resolved_dispatch_extra["recovered"] = True
    if checkpoint_snapshot is not None:
        resolved_dispatch_extra["checkpoint_snapshot"] = checkpoint_snapshot
    record_scheduler_step_dispatched(
        db,
        task_run,
        agent_name=agent_name,
        queue=queue,
        step=step,
        summary_prefix=summary_prefix,
        stage_policy=stage_policy,
        extra={key: value for key, value in resolved_dispatch_extra.items() if value is not None},
    )

    previous_work = build_orchestration_previous_work(output_state.completed_turns)
    resolved_extra_context = f"{previous_work}\n{extra_context}".strip() if extra_context else previous_work
    handoff_state = claim_orchestration_step_handoffs(
        db,
        task_run=task_run,
        step=step,
        agent_name=agent_name,
        pending_handoffs=pending_handoffs,
    )
    try:
        content, message = await execute_turn(
            agent=agent,
            chatroom_id=chatroom_id,
            project=project,
            agents=agents,
            user_message=user_message,
            extra_context=resolved_extra_context,
            inter_agent_messages=handoff_state.messages,
            db=db,
            client_turn_id=client_turn_id,
            task_run=task_run,
            checkpoint_snapshot=checkpoint_snapshot,
        )
    except Exception as exc:
        record_scheduler_step_failed(
            db,
            task_run,
            queue,
            step,
            agent_name=agent_name,
            summary_prefix=summary_prefix,
            stage_policy=stage_policy,
            error=exc,
            extra={"recovered": True} if recovered else None,
        )
        fail_orchestration_step_handoffs(db, handoff_state, error=str(exc), retry=True)
        raise

    try:
        if content:
            await publish_message(
                db,
                chatroom_id,
                message_id=message.id,
                content=content,
                agent_name=agent_name,
                message_type="text",
                created_at=message.created_at,
                metadata=message_metadata or {},
            )
            record_orchestration_step_output(
                output_state,
                agent_name=agent_name,
                content=content,
                dispatch_kind=step.dispatch_kind,
                include_result=include_result,
            )

        ready_steps = complete_orchestration_scheduler_step(
            db,
            task_run,
            queue=queue,
            step=step,
            orchestration_policy=orchestration_policy,
            agent_name=agent_name,
            content=content,
            pending_handoffs=pending_handoffs,
            stage_policy=stage_policy,
            summary_prefix=summary_prefix,
            recovered=recovered,
        )
    except Exception as exc:
        fail_orchestration_step_handoffs(db, handoff_state, error=str(exc), retry=True)
        raise
    acknowledge_orchestration_step_handoffs(db, handoff_state)
    return OrchestrationStepRunResult(content=content, message=message, ready_steps=ready_steps)
