# -*- coding: utf-8 -*-
"""Shared completion helper for orchestration scheduler steps."""

from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy.orm import Session

from models.database import TaskRun
from services.orchestration_events import record_scheduler_step_completed, record_scheduler_step_resumed
from services.orchestration_handoffs import record_orchestration_handoffs
from services.runner_policy import find_stage_policy


def complete_orchestration_scheduler_step(
    db: Session,
    task_run: TaskRun | None,
    *,
    queue: Any,
    step: Any,
    orchestration_policy: Any,
    agent_name: str,
    content: str,
    pending_handoffs: Dict[str, List[Dict[str, str]]],
    stage_policy: Any = None,
    summary_prefix: str = "Scheduler",
    recovered: bool = False,
) -> list[Any]:
    """Mark a scheduler step complete, resume released steps, and enqueue handoffs."""

    ready_steps = queue.mark_completed(step.step_id)
    extra = {"recovered": True} if recovered else None
    record_scheduler_step_completed(
        db,
        task_run,
        queue,
        step,
        agent_name=agent_name,
        ready_steps=ready_steps,
        completed_with_output=bool(content),
        summary_prefix=summary_prefix,
        stage_policy=stage_policy,
        extra=extra,
    )
    for next_step in ready_steps:
        next_step_policy = find_stage_policy(orchestration_policy, next_step.step_id)
        record_scheduler_step_resumed(
            db,
            task_run,
            queue,
            next_step,
            summary_prefix=summary_prefix,
            stage_policy=next_step_policy,
            resumed_by_step_id=step.step_id,
            resumed_by_agent=agent_name,
            extra=extra,
        )

    if content:
        record_orchestration_handoffs(
            db,
            task_run,
            pending_handoffs,
            from_agent_name=agent_name,
            from_step_id=step.step_id,
            content=content,
            ready_steps=ready_steps,
            recovered=recovered,
        )
    return ready_steps
