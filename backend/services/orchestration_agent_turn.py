# -*- coding: utf-8 -*-
"""Non-streaming agent turn runner used by orchestration steps."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List

from sqlalchemy.orm import Session

from agents.identity import agent_name_of
from models.database import Chatroom, TaskRun
from services.nonstream_turn_executor import execute_non_stream_turn_loop
from services.runner_lifecycle import (
    complete_agent_turn as record_agent_turn_completed,
    record_tool_round as record_runner_tool_round,
    start_agent_turn as record_agent_turn_started,
)
from services.runtime_event_helpers import build_runtime_event_payload
from services.turn_state import TurnContextState, build_tool_result_record


@dataclass(frozen=True)
class OrchestrationAgentTurnDeps:
    """Route-owned dependencies required to run an orchestrated agent turn."""

    ensure_collaboration_context: Callable[[list[Any], int], None]
    prepare_chat_turn_runtime: Callable[..., Awaitable[Any]]
    build_context_compaction_callback: Callable[..., Any]
    assemble_chat_messages: Callable[..., list[dict[str, Any]]]
    save_message: Callable[..., Awaitable[Any]]
    message_metadata: Callable[[str | None], dict[str, Any]]
    schedule_memory_extraction: Callable[[Any, str, str], Any]
    max_tool_iterations: int = 50


async def run_orchestration_agent_turn(
    *,
    deps: OrchestrationAgentTurnDeps,
    agent: Any,
    chatroom_id: int,
    project: Any,
    agents: list[Any],
    user_message: str,
    extra_context: str,
    db: Session,
    client_turn_id: str | None = None,
    inter_agent_messages: List[Dict[str, Any]] | None = None,
    task_run: TaskRun | None = None,
    checkpoint_snapshot: Dict[str, Any] | None = None,
) -> tuple[str | None, Any | None]:
    """Execute one non-streaming agent turn for orchestration."""

    current_chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()

    deps.ensure_collaboration_context(agents, chatroom_id)
    runtime = await deps.prepare_chat_turn_runtime(
        agent=agent,
        chatroom_id=chatroom_id,
        project=project,
        checkpoint_snapshot=checkpoint_snapshot,
        previous_agent_work=extra_context,
        inter_agent_messages=inter_agent_messages or [],
        recent_message_limit=6,
    )
    compaction_callback = deps.build_context_compaction_callback(
        db,
        task_run,
        agent_name=runtime.agent_label,
        extra_payload={
            "run_kind": "multi_agent_orchestration",
            "chatroom_id": chatroom_id,
            "client_turn_id": client_turn_id,
        },
    )
    record_agent_turn_started(
        db,
        task_run,
        agent_name=runtime.agent_label,
        summary=f"{runtime.agent_label} started an orchestrated turn.",
        payload=build_runtime_event_payload(
            client_turn_id=client_turn_id,
            inter_agent_message_count=len(inter_agent_messages or []),
        ),
    )

    def _assemble_orchestration_turn_messages(current_turn_state: TurnContextState) -> list[dict[str, Any]]:
        return deps.assemble_chat_messages(
            db=db,
            agent=agent,
            agent_name=runtime.agent_label,
            model_id=getattr(runtime.llm_client, "model", ""),
            chatroom=current_chatroom,
            project=project,
            agents=agents,
            recent_messages=runtime.recent_messages,
            user_message=user_message,
            available_tools=runtime.available_tools,
            history_limit=4,
            standalone_note=(
                "This is a standalone chat. Reply directly, stay concise, "
                "and coordinate with mentioned teammates when it helps."
            )
            if not project
            else "",
            turn_state=current_turn_state,
            on_compaction=compaction_callback,
        )

    async def _execute_orchestration_tool(frame: Any, tool_call: dict[str, Any]):
        tool_name = tool_call["function"]["name"]
        tool_args_str = tool_call["function"].get("arguments", "{}")
        try:
            tool_args = json.loads(tool_args_str or "{}")
            from tools import tool_registry

            tool_result = await tool_registry.execute(
                tool_name,
                **tool_args,
                **runtime.runtime_kwargs,
            )
            result_str = str(tool_result) if tool_result else "(no output)"
            tool_success = True
        except Exception as te:
            result_str = f"Error: {te}"
            tool_success = False
        return build_tool_result_record(
            tool_call_id=tool_call.get("id"),
            tool_name=tool_name,
            arguments=tool_args_str,
            result=result_str,
            success=tool_success,
        )

    async def _on_orchestration_tool_round(frame: Any, tool_results: list[Any], current_turn_state: TurnContextState):
        record_runner_tool_round(
            db,
            task_run,
            agent_name=runtime.agent_label,
            turn=frame.turn_index + 1,
            tool_names=[tool_call["function"]["name"] for tool_call in frame.normalized_tool_calls],
            tool_results=tool_results,
            summary=f"{runtime.agent_label} completed a tool round.",
        )

    response_content = await execute_non_stream_turn_loop(
        llm_client=runtime.llm_client,
        tools=runtime.tool_schemas,
        turn_state=runtime.turn_state,
        assemble_messages=_assemble_orchestration_turn_messages,
        execute_tool_call=_execute_orchestration_tool,
        max_turns=deps.max_tool_iterations,
        on_tool_round=_on_orchestration_tool_round,
    )

    if not response_content:
        return None, None

    agent_msg = await deps.save_message(
        chatroom_id=chatroom_id,
        agent_id=agent.id,
        content=response_content,
        message_type="text",
        metadata=deps.message_metadata(client_turn_id),
        agent_name=agent_name_of(agent),
    )
    record_agent_turn_completed(
        db,
        task_run,
        agent_name=agent_name_of(agent),
        message_id=agent_msg.id,
        response_content=response_content,
        summary=f"{agent_name_of(agent)} completed the orchestrated turn.",
    )

    if len(response_content) > 30:
        scheduled = deps.schedule_memory_extraction(agent, user_message, response_content)
        if asyncio.iscoroutine(scheduled):
            asyncio.create_task(scheduled)

    return response_content, agent_msg
