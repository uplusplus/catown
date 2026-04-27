# -*- coding: utf-8 -*-
"""Shared streaming scheduler step runner helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List

from sqlalchemy.orm import Session

from models.database import TaskRun
from services.orchestration_events import record_scheduler_step_dispatched, record_scheduler_step_failed
from services.orchestration_handoffs import build_orchestration_previous_work
from services.orchestration_step_completion import complete_orchestration_scheduler_step
from services.orchestration_step_state import OrchestrationStepOutputState, record_orchestration_step_output


@dataclass
class StreamOrchestrationStepState:
    saved_message: Any = None
    content: str = ""


IterAgentEvents = Callable[..., Any]
SaveMessage = Callable[..., Awaitable[Any]]
PublishMessage = Callable[..., Awaitable[Any]]
RecordTurnCompleted = Callable[..., Any]
ScheduleMemoryExtraction = Callable[..., Any]


def start_stream_orchestration_step(
    db: Session,
    task_run: TaskRun | None,
    *,
    queue: Any,
    step: Any,
    agent_name: str,
    stage_policy: Any = None,
) -> dict[str, Any]:
    """Record dispatch and return the public SSE collab_step payload."""

    record_scheduler_step_dispatched(
        db,
        task_run,
        agent_name=agent_name,
        queue=queue,
        step=step,
        stage_policy=stage_policy,
    )
    return {
        "type": "collab_step",
        "step": step.position,
        "total": len(queue.plan.steps),
        "agent": step.requested_name,
        "agent_name": agent_name,
        "dispatch_kind": step.dispatch_kind,
        "attached_to_step_id": step.attached_to_step_id,
        "runtime": queue.runtime_snapshot_payload(),
        "step_state": queue.runtime_state_payload_for_step(step.step_id),
    }


def iter_stream_orchestration_agent_events(
    *,
    iter_agent_events: IterAgentEvents,
    agent: Any,
    chatroom: Any,
    project: Any,
    agents: list[Any],
    user_message: str,
    db: Session,
    client_turn_id: str | None,
    output_state: OrchestrationStepOutputState,
    pending_handoffs: Dict[str, List[Dict[str, str]]],
    step: Any,
    standalone_note: str,
    task_run: TaskRun | None,
    checkpoint_snapshot: Dict[str, Any] | None,
) -> Any:
    """Build the route-local async iterator for a streaming agent turn."""

    return iter_agent_events(
        agent=agent,
        chatroom_id=chatroom.id,
        chatroom=chatroom,
        project=project,
        agents=agents,
        user_message=user_message,
        db=db,
        client_turn_id=client_turn_id,
        previous_agent_work=build_orchestration_previous_work(output_state.completed_turns),
        inter_agent_messages=pending_handoffs.pop(step.step_id, []),
        history_limit=3,
        standalone_note=standalone_note,
        task_run=task_run,
        checkpoint_snapshot=checkpoint_snapshot,
    )


async def handle_stream_orchestration_turn_complete(
    *,
    db: Session,
    task_run: TaskRun | None,
    chatroom: Any,
    agent: Any,
    agent_name: str,
    step: Any,
    content: str,
    client_turn_id: str | None,
    output_state: OrchestrationStepOutputState,
    save_message: SaveMessage,
    publish_message: PublishMessage,
    record_turn_completed: RecordTurnCompleted,
    message_metadata: Dict[str, Any],
    schedule_memory_extraction: ScheduleMemoryExtraction | None = None,
    user_message: str = "",
) -> Any:
    """Persist a streaming turn_complete event and update shared output state."""

    if not content:
        return None
    saved = await save_message(
        chatroom_id=chatroom.id,
        agent_id=agent.id,
        content=content,
        message_type="text",
        metadata=message_metadata,
        agent_name=agent_name,
    )
    await publish_message(
        db,
        chatroom.id,
        message_id=saved.id,
        content=content,
        agent_name=agent_name,
        message_type="text",
        created_at=saved.created_at,
        metadata=message_metadata,
    )
    record_turn_completed(
        db,
        task_run,
        agent_name=agent_name,
        message_id=saved.id,
        response_content=content,
        summary=f"{agent_name} completed the orchestrated streaming turn.",
    )
    if schedule_memory_extraction is not None and len(content) > 30:
        schedule_memory_extraction(agent, user_message, content)
    record_orchestration_step_output(
        output_state,
        agent_name=agent_name,
        content=content,
        dispatch_kind=step.dispatch_kind,
        include_result=False,
    )
    return saved


def fail_stream_orchestration_step(
    db: Session,
    task_run: TaskRun | None,
    *,
    queue: Any,
    step: Any,
    agent_name: str,
    stage_policy: Any = None,
    error: Any,
) -> None:
    record_scheduler_step_failed(
        db,
        task_run,
        queue,
        step,
        agent_name=agent_name,
        stage_policy=stage_policy,
        error=error,
    )


def complete_stream_orchestration_step(
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
) -> tuple[list[Any], dict[str, Any]]:
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
    )
    return ready_steps, {
        "type": "collab_step_done",
        "agent": step.requested_name,
        "agent_name": agent_name,
        "dispatch_kind": step.dispatch_kind,
        "attached_to_step_id": step.attached_to_step_id,
        "runtime": queue.runtime_snapshot_payload(),
        "step_state": queue.runtime_state_payload_for_step(step.step_id),
        "released_step_ids": [next_step.step_id for next_step in ready_steps],
    }
