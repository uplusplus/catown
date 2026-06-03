# -*- coding: utf-8 -*-
"""Non-streaming agent turn runner used by orchestration steps."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List

from sqlalchemy.orm import Session

from agents.identity import agent_name_of
from models.database import Chatroom, TaskRun
from services.chat_runtime import merge_tool_execution_kwargs
from services.nonstream_turn_executor import execute_non_stream_turn_loop
from services.audit_recorder import chain_before_event_callbacks, make_nonstream_audit_callbacks, make_stream_audit_before_event
from services.orchestration_chat_profile import (
    build_orchestration_stream_turn_profile,
    build_orchestration_sync_turn_profile,
)
from services.runner_lifecycle import (
    build_llm_request_prompt_payload,
    complete_agent_turn as record_agent_turn_completed,
    record_llm_request_created,
    record_llm_response_completed,
    record_llm_response_started,
    record_tool_round as record_runner_tool_round,
    start_tool_call as record_tool_call_started,
    start_agent_turn as record_agent_turn_started,
)
from services.provider_sessions import (
    ensure_provider_session,
    extract_response_state,
    serialize_provider_session,
    touch_provider_session_request,
    update_provider_session_response,
)
from services.provider_compaction import (
    build_compaction_context_messages,
    maybe_create_provider_or_local_compaction_checkpoint,
)
from services.runtime_event_helpers import build_runtime_event_payload
from services.run_ledger import build_task_run_checkpoint_snapshot
from services.stream_runtime_persistence import store_runtime_card
from services.stream_turn_executor import iter_stream_turn_events
from services.task_run_control import raise_if_task_run_cancelled
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


@dataclass(frozen=True)
class StreamOrchestrationAgentTurnDeps:
    """Route-owned dependencies required to stream an orchestrated agent turn."""

    ensure_collaboration_context: Callable[[list[Any], int], None]
    prepare_chat_turn_runtime: Callable[..., Awaitable[Any]]
    assemble_chat_messages: Callable[..., list[dict[str, Any]]]
    build_llm_card_payload: Callable[..., dict[str, Any]]
    snapshot_messages: Callable[..., Any]
    preview_tool_calls: Callable[..., Any]
    format_prompt_messages: Callable[..., str]
    tool_result_success: Callable[..., bool]
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
    raise_if_task_run_cancelled(db, task_run, context=f"agent turn {agent_name_of(agent)}")

    deps.ensure_collaboration_context(agents, chatroom_id)
    runtime = await deps.prepare_chat_turn_runtime(
        agent=agent,
        chatroom_id=chatroom_id,
        project=project,
        checkpoint_snapshot=checkpoint_snapshot,
        previous_agent_work=extra_context,
        inter_agent_messages=inter_agent_messages or [],
        user_message=user_message,
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
        summary="",
        payload=build_runtime_event_payload(
            client_turn_id=client_turn_id,
            inter_agent_message_count=len(inter_agent_messages or []),
        ),
    )
    async def _check_cancel(*_args: Any, **_kwargs: Any) -> None:
        raise_if_task_run_cancelled(db, task_run, context=f"agent turn {runtime.agent_label}")

    async def _execute_tool(
        tool_name: str,
        tool_args: dict[str, Any],
        tool_args_str: str,
        tool_call_id: str | None,
        turn: int,
        progress_callback: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ):
        from tools import tool_registry

        return await tool_registry.execute(
            tool_name,
            **merge_tool_execution_kwargs(
                tool_args,
                runtime.runtime_kwargs,
            ),
            task_run_id=getattr(task_run, "id", None) if task_run is not None else None,
            client_turn_id=client_turn_id,
            tool_call_id=tool_call_id,
            turn=turn,
            __catown_runtime_card_managed_by_stream=(tool_name == "run_shell"),
            progress_callback=progress_callback,
        )

    profile = build_orchestration_sync_turn_profile(
        runtime=runtime,
        agent=agent,
        db=db,
        project=project,
        agents=agents,
        chatroom=current_chatroom,
        user_message=user_message,
        standalone_note=(
            "This is a standalone chat. Reply directly, stay concise, "
            "and coordinate with mentioned teammates when it helps."
        )
        if not project
        else "",
        task_run=task_run,
        client_turn_id=client_turn_id,
        max_turns=deps.max_tool_iterations,
        assemble_chat_messages=deps.assemble_chat_messages,
        save_tool_progress=lambda payload: store_runtime_card(chatroom_id, payload),
        execute_tool=_execute_tool,
        record_tool_call_started=record_tool_call_started,
        record_tool_round=record_runner_tool_round,
        check_cancel=_check_cancel,
        compaction_callback=compaction_callback,
    )

    audit_cbs = make_nonstream_audit_callbacks(
        db=db,
        run_id=getattr(task_run, "id", None),
        stage_id=None,
        agent_name=agent_name_of(agent),
    )
    _original_on_tool_round = profile.on_tool_round

    async def _chained_on_tool_round(frame, tool_results, turn_state):
        if audit_cbs.get("on_tool_round"):
            await audit_cbs["on_tool_round"](frame, tool_results, turn_state)
        if _original_on_tool_round:
            return await _original_on_tool_round(frame, tool_results, turn_state)

    loop_result = await execute_non_stream_turn_loop(
        llm_client=profile.runtime.llm_client,
        tools=profile.runtime.tool_schemas,
        turn_state=profile.runtime.turn_state,
        assemble_messages=profile.assemble_messages,
        execute_tool_call=profile.execute_tool_call,
        max_turns=deps.max_tool_iterations,
        before_turn=profile.check_cancel,
        before_tool_call=profile.check_cancel,
        on_tool_round=_chained_on_tool_round,
        before_llm_call=audit_cbs["before_llm_call"],
        on_llm_response=audit_cbs["on_llm_response"],
        on_llm_error=audit_cbs["on_llm_error"],
    )
    if loop_result.awaiting_tool_approval or loop_result.awaiting_background_tool:
        return None, None
    response_content = loop_result.final_content

    if not response_content:
        return None, None

    await _check_cancel()

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
        summary="",
    )

    if len(response_content) > 30:
        scheduled = deps.schedule_memory_extraction(agent, user_message, response_content)
        if asyncio.iscoroutine(scheduled):
            asyncio.create_task(scheduled)

    return response_content, agent_msg


async def iter_stream_orchestration_agent_turn_events(
    *,
    deps: StreamOrchestrationAgentTurnDeps,
    agent: Any,
    chatroom_id: int,
    chatroom: Any,
    project: Any,
    agents: list[Any],
    user_message: str,
    db: Session,
    client_turn_id: str | None = None,
    previous_agent_work: str = "",
    inter_agent_messages: List[Dict[str, Any]] | None = None,
    history_limit: int = 4,
    standalone_note: str = "",
    task_run: TaskRun | None = None,
    checkpoint_snapshot: Dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield stream turn events for one orchestrated agent turn."""

    raise_if_task_run_cancelled(db, task_run, context=f"stream agent turn {agent_name_of(agent)}")

    deps.ensure_collaboration_context(agents, chatroom_id)
    runtime = await deps.prepare_chat_turn_runtime(
        agent=agent,
        chatroom_id=chatroom_id,
        project=project,
        checkpoint_snapshot=checkpoint_snapshot,
        previous_agent_work=previous_agent_work,
        inter_agent_messages=inter_agent_messages or [],
        user_message=user_message,
        recent_message_limit=max(history_limit + 2, 6),
    )
    record_agent_turn_started(
        db,
        task_run,
        agent_name=runtime.agent_label,
        summary="",
        payload=build_runtime_event_payload(
            client_turn_id=client_turn_id,
            inter_agent_message_count=len(inter_agent_messages or []),
        ),
    )
    async def _check_cancel(*_args: Any, **_kwargs: Any) -> None:
        raise_if_task_run_cancelled(db, task_run, context=f"stream agent turn {runtime.agent_label}")

    seen_request_turns: set[int] = set()
    seen_response_started_turns: set[int] = set()
    provider_sessions_by_turn: dict[int, Any] = {}

    async def _record_stream_fact(frame: Any, event: dict[str, Any], _turn_state: TurnContextState) -> None:
        await _check_cancel()
        event_type = str(event.get("type") or "")
        turn_index = int(getattr(frame, "turn_index", 1) or 1)
        if event_type in {"agent_start", "request_sent"} and turn_index not in seen_request_turns:
            seen_request_turns.add(turn_index)
            step_id = f"llm:{runtime.agent_label}:{turn_index}"
            provider_session = ensure_provider_session(
                db,
                task_run=task_run,
                chatroom_id=chatroom_id,
                project_id=getattr(project, "id", None),
                agent_name=runtime.agent_label,
                llm_client=runtime.llm_client,
            )
            previous_response_id = getattr(provider_session, "last_response_id", None)
            provider_session = touch_provider_session_request(db, provider_session)
            provider_sessions_by_turn[turn_index] = provider_session
            provider_session_payload = serialize_provider_session(
                provider_session,
                previous_response_id=previous_response_id,
            )
            setattr(frame, "provider_session", provider_session_payload)
            event["provider_session"] = provider_session_payload
            event["provider_mode"] = provider_session_payload.get("provider_mode")
            record_llm_request_created(
                db,
                task_run,
                agent_name=runtime.agent_label,
                turn=turn_index,
                model=getattr(runtime.llm_client, "model", None),
                client_turn_id=client_turn_id,
                payload=build_llm_request_prompt_payload(
                    frame,
                    elapsed_ms=event.get("elapsed_ms"),
                    step_id=step_id,
                ),
            )
            return
        provider_session = provider_sessions_by_turn.get(turn_index)
        if provider_session is not None:
            provider_session_payload = serialize_provider_session(provider_session)
            setattr(frame, "provider_session", provider_session_payload)
            event.setdefault("provider_session", provider_session_payload)
            event.setdefault("provider_mode", provider_session_payload.get("provider_mode"))
        if event_type in {"first_content", "first_chunk"} and turn_index not in seen_response_started_turns:
            seen_response_started_turns.add(turn_index)
            record_llm_response_started(
                db,
                task_run,
                agent_name=runtime.agent_label,
                turn=turn_index,
                client_turn_id=client_turn_id,
                payload={
                    "elapsed_ms": event.get("elapsed_ms"),
                    "step_id": f"llm:{runtime.agent_label}:{turn_index}",
                },
            )
            return
        if event_type == "done":
            response_state = extract_response_state(event)
            provider_session = update_provider_session_response(
                db,
                provider_sessions_by_turn.get(turn_index),
                response_id=response_state.get("response_id"),
                provider_conversation_id=response_state.get("provider_conversation_id"),
                compact_checkpoint_id=response_state.get("compact_checkpoint_id"),
            )
            provider_session_payload = serialize_provider_session(provider_session)
            setattr(frame, "provider_session", provider_session_payload)
            event["provider_session"] = provider_session_payload
            event["provider_mode"] = provider_session_payload.get("provider_mode")
            record_llm_response_completed(
                db,
                task_run,
                agent_name=runtime.agent_label,
                turn=turn_index,
                finish_reason=event.get("finish_reason"),
                client_turn_id=client_turn_id,
                payload={
                    "response_preview": str(event.get("full_content") or getattr(frame, "llm_content", "") or "")[:280],
                    "tool_call_count": len(event.get("tool_calls") or []),
                    "step_id": f"llm:{runtime.agent_label}:{turn_index}",
                    "provider_mode": provider_session_payload.get("provider_mode"),
                    "provider_session": provider_session_payload,
                    "provider_request": event.get("provider_request") if isinstance(event.get("provider_request"), dict) else {},
                },
            )
            try:
                db.expire(task_run, ["events"])
            except Exception:
                pass
            checkpoint_snapshot = build_task_run_checkpoint_snapshot(task_run)
            latest_context_budget = (
                checkpoint_snapshot.get("latest_context_budget_event")
                if isinstance(checkpoint_snapshot.get("latest_context_budget_event"), dict)
                else {}
            )
            compaction_messages = build_compaction_context_messages(
                getattr(frame, "messages", None),
                response_content=event.get("full_content") or getattr(frame, "llm_content", ""),
                tool_calls=event.get("tool_calls"),
            )
            await maybe_create_provider_or_local_compaction_checkpoint(
                db,
                provider_session=provider_session,
                task_run=task_run,
                llm_client=runtime.llm_client,
                messages=compaction_messages,
                tools=runtime.tool_schemas,
                checkpoint_snapshot=checkpoint_snapshot,
                diagnostics=latest_context_budget,
                agent_name=runtime.agent_label,
            )

    async def _execute_tool(
        tool_name: str,
        tool_args: dict[str, Any],
        tool_args_str: str,
        tool_call_id: str | None,
        tool_index: int,
        turn_index: int,
        progress_callback: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ):
        from tools import tool_registry

        return await tool_registry.execute(
            tool_name,
            **merge_tool_execution_kwargs(
                tool_args,
                runtime.runtime_kwargs,
            ),
            task_run_id=getattr(task_run, "id", None) if task_run is not None else None,
            client_turn_id=client_turn_id,
            tool_call_id=tool_call_id,
            turn=turn_index,
            __catown_runtime_card_managed_by_stream=True,
            progress_callback=progress_callback,
        )

    profile = build_orchestration_stream_turn_profile(
        runtime=runtime,
        agent=agent,
        db=db,
        project=project,
        agents=agents,
        chatroom=chatroom,
        user_message=user_message,
        history_limit=history_limit,
        standalone_note=standalone_note,
        task_run=task_run,
        client_turn_id=client_turn_id,
        assemble_chat_messages=deps.assemble_chat_messages,
        save_tool_progress=lambda payload: store_runtime_card(chatroom_id, payload),
        execute_tool=_execute_tool,
        record_tool_round=record_runner_tool_round,
        check_cancel=_check_cancel,
        build_llm_card_payload=deps.build_llm_card_payload,
    )

    async for event in iter_stream_turn_events(
        llm_client=profile.runtime.llm_client,
        tools=profile.runtime.tool_schemas,
        turn_state=profile.runtime.turn_state,
        agent_name=profile.runtime.agent_label,
        client_turn_id=client_turn_id,
        assemble_messages=profile.assemble_messages,
        execute_tool=profile.execute_tool,
        build_llm_runtime_card=profile.build_llm_runtime_card,
        snapshot_messages=deps.snapshot_messages,
        preview_tool_calls=deps.preview_tool_calls,
        format_prompt_messages=deps.format_prompt_messages,
        tool_result_success=deps.tool_result_success,
        max_turns=deps.max_tool_iterations,
        before_turn=profile.check_cancel,
        before_event=chain_before_event_callbacks(
            _record_stream_fact,
            make_stream_audit_before_event(
                db=db,
                run_id=getattr(task_run, "id", None),
                stage_id=None,
                agent_name=profile.runtime.agent_label,
            ),
        ),
        before_tool_call=profile.check_cancel,
        on_tool_round=profile.on_tool_round,
    ):
        if event["type"] == "turn_complete":
            event["agent"] = agent
        yield event
