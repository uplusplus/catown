# -*- coding: utf-8 -*-
"""Shared non-stream orchestration runtime runner."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict

from sqlalchemy.orm import Session

from services.orchestration_step_runner import run_nonstream_orchestration_step
from services.orchestration_step_state import OrchestrationStepOutputState
from services.runner_policy import find_stage_policy
from services.task_run_control import raise_if_task_run_cancelled

logger = logging.getLogger("catown.orchestration_runtime_runner")


@dataclass(frozen=True)
class NonstreamOrchestrationRuntimeDeps:
    execute_turn: Callable[..., Awaitable[tuple[str, Any]]]
    publish_message: Callable[..., Awaitable[Any]]
    message_metadata: Dict[str, Any] | None = None
    before_next_step: Callable[[], Awaitable[Any] | Any] | None = None
    build_step_context: Callable[[Any, Any, str], Awaitable[dict[str, Any]] | dict[str, Any]] | None = None
    log_agent_type: Callable[[Any], str] | None = None


async def run_nonstream_orchestration_runtime(
    *,
    db: Session,
    task_run: Any,
    queue: Any,
    resolved_agents: list[Any],
    chatroom_id: int,
    project: Any,
    agents: list[Any],
    user_message: str,
    client_turn_id: str | None,
    output_state: OrchestrationStepOutputState,
    pending_handoffs: Dict[str, list[Dict[str, str]]],
    orchestration_policy: Any,
    deps: NonstreamOrchestrationRuntimeDeps,
) -> None:
    """Run the non-stream orchestration step loop until queue exhaustion or cancellation."""

    agents_by_id = {getattr(agent, "id", None): agent for agent in resolved_agents}
    while True:
        if deps.before_next_step is not None:
            await _maybe_await(deps.before_next_step())
        raise_if_task_run_cancelled(db, task_run, context="nonstream orchestration runtime")
        step = queue.pop_ready()
        if step is None:
            break

        agent = agents_by_id.get(step.agent_id)
        if agent is None:
            continue

        agent_label = getattr(agent, "name", None) or getattr(step, "agent_name", None) or "agent"
        step_policy = find_stage_policy(orchestration_policy, step.step_id)
        if deps.log_agent_type is not None:
            logger.info(
                "[Collab] Step %s/%s: %s",
                step.position,
                len(queue.plan.steps),
                deps.log_agent_type(agent),
            )

        step_context = {}
        if deps.build_step_context is not None:
            step_context = await _maybe_await(deps.build_step_context(step, agent, agent_label)) or {}

        await run_nonstream_orchestration_step(
            db=db,
            task_run=task_run,
            queue=queue,
            step=step,
            agent=agent,
            agent_name=agent_label,
            chatroom_id=chatroom_id,
            project=project,
            agents=agents,
            user_message=user_message,
            client_turn_id=client_turn_id,
            output_state=output_state,
            pending_handoffs=pending_handoffs,
            orchestration_policy=orchestration_policy,
            stage_policy=step_policy,
            execute_turn=deps.execute_turn,
            publish_message=deps.publish_message,
            message_metadata=deps.message_metadata,
            **step_context,
        )


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value
