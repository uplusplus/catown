# -*- coding: utf-8 -*-
"""Shared streaming scheduler step runner helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List

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
BuildCheckpointSnapshot = Callable[[TaskRun | None], Dict[str, Any]]
FindStagePolicy = Callable[[Any, str], Any]
AgentNameOf = Callable[[Any], str]
SetActiveAgent = Callable[[str, Any], Any]
FailTaskRun = Callable[..., Any]
FinalizeTaskRun = Callable[..., Any]


@dataclass(frozen=True)
class StreamOrchestrationRuntimeDeps:
    iter_agent_events: IterAgentEvents
    save_message: SaveMessage
    publish_message: PublishMessage
    record_turn_completed: RecordTurnCompleted
    message_metadata: Callable[[str | None], Dict[str, Any]]
    schedule_memory_extraction: ScheduleMemoryExtraction
    build_checkpoint_snapshot: BuildCheckpointSnapshot
    find_stage_policy: FindStagePolicy
    agent_name_of: AgentNameOf
    fail_task_run: FailTaskRun
    finalize_task_run: FinalizeTaskRun
    set_active_agent: SetActiveAgent | None = None


@dataclass(frozen=True)
class StreamOrchestrationRuntimeEvent:
    type: str
    payload: Dict[str, Any] | None = None
    card_type: str | None = None
    card_payload: Dict[str, Any] | None = None


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


async def iter_stream_orchestration_runtime_events(
    *,
    db: Session,
    task_run: TaskRun | None,
    chatroom: Any,
    project: Any,
    agents: list[Any],
    resolved_agents: list[Any],
    user_message: str,
    client_turn_id: str | None,
    queue: Any,
    orchestration_policy: Any,
    output_state: OrchestrationStepOutputState,
    pending_handoffs: Dict[str, List[Dict[str, str]]],
    standalone_note: str,
    deps: StreamOrchestrationRuntimeDeps,
) -> AsyncIterator[StreamOrchestrationRuntimeEvent]:
    """Run streaming orchestration and yield transport-neutral runtime events."""

    agents_by_id = {getattr(agent, "id", None): agent for agent in resolved_agents}
    while True:
        step = queue.pop_ready()
        if step is None:
            break

        agent = agents_by_id.get(step.agent_id)
        if agent is None:
            continue

        agent_label = deps.agent_name_of(agent)
        step_policy = deps.find_stage_policy(orchestration_policy, step.step_id)
        db.refresh(task_run)
        step_checkpoint_snapshot = deps.build_checkpoint_snapshot(task_run)
        if callable(deps.set_active_agent):
            deps.set_active_agent(agent_label, agent.id)

        yield StreamOrchestrationRuntimeEvent(
            type="sse",
            payload=start_stream_orchestration_step(
                db,
                task_run,
                queue=queue,
                step=step,
                agent_name=agent_label,
                stage_policy=step_policy,
            ),
        )

        saved = None
        step_content = ""
        try:
            async for event in iter_stream_orchestration_agent_events(
                iter_agent_events=deps.iter_agent_events,
                agent=agent,
                chatroom=chatroom,
                project=project,
                agents=agents,
                user_message=user_message,
                db=db,
                client_turn_id=client_turn_id,
                output_state=output_state,
                pending_handoffs=pending_handoffs,
                step=step,
                standalone_note=standalone_note,
                task_run=task_run,
                checkpoint_snapshot=step_checkpoint_snapshot,
            ):
                if event["type"] == "runtime_card":
                    yield StreamOrchestrationRuntimeEvent(
                        type="runtime_card",
                        card_type=event["card_type"],
                        card_payload=event["payload"],
                    )
                    continue

                if event["type"] == "turn_complete":
                    step_content = event.get("content") or ""
                    if step_content:
                        saved = await handle_stream_orchestration_turn_complete(
                            db=db,
                            task_run=task_run,
                            chatroom=chatroom,
                            agent=agent,
                            agent_name=agent_label,
                            step=step,
                            content=step_content,
                            client_turn_id=client_turn_id,
                            output_state=output_state,
                            save_message=deps.save_message,
                            publish_message=deps.publish_message,
                            record_turn_completed=deps.record_turn_completed,
                            message_metadata=deps.message_metadata(client_turn_id),
                            schedule_memory_extraction=deps.schedule_memory_extraction,
                            user_message=user_message,
                        )
                    continue

                yield StreamOrchestrationRuntimeEvent(type="sse", payload=event)
        except Exception as exc:
            fail_stream_orchestration_step(
                db,
                task_run,
                queue=queue,
                step=step,
                agent_name=agent_label,
                stage_policy=step_policy,
                error=exc,
            )
            deps.fail_task_run(
                db,
                task_run,
                summary=f"Streaming orchestration failed at {agent_label}.",
                agent_name=agent_label,
                payload={"error": str(exc), "step_id": step.step_id},
            )
            yield StreamOrchestrationRuntimeEvent(
                type="sse",
                payload={
                    "type": "error",
                    "error": str(exc)[:2000],
                    "agent_name": agent_label,
                    "client_turn_id": client_turn_id,
                },
            )
            yield StreamOrchestrationRuntimeEvent(
                type="sse",
                payload={
                    "type": "done",
                    "agent_name": agent_label,
                    "collab": True,
                    "client_turn_id": client_turn_id,
                },
            )
            return

        ready_steps, step_done_payload = complete_stream_orchestration_step(
            db,
            task_run,
            queue=queue,
            step=step,
            orchestration_policy=orchestration_policy,
            agent_name=agent_label,
            content=step_content,
            pending_handoffs=pending_handoffs,
            stage_policy=step_policy,
        )
        step_done_payload["message_id"] = saved.id if saved else None
        yield StreamOrchestrationRuntimeEvent(type="sse", payload=step_done_payload)

    resolved_names = [deps.agent_name_of(agent) for agent in resolved_agents]
    deps.finalize_task_run(
        db,
        task_run,
        last_blocking_result=output_state.last_blocking_result,
        completed_turns=output_state.completed_turns,
        fallback="Streaming orchestration completed.",
    )
    yield StreamOrchestrationRuntimeEvent(
        type="sse",
        payload={
            "type": "done",
            "agent_name": ", ".join(resolved_names),
            "collab": True,
            "client_turn_id": client_turn_id,
        },
    )
