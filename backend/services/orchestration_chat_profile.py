# -*- coding: utf-8 -*-
"""Turn-level execution profiles for orchestration chat paths."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from services.chat_runtime import build_runtime_environment_context
from services.turn_state import TurnContextState, build_tool_result_record


@dataclass(frozen=True)
class OrchestrationSyncTurnProfile:
    runtime: Any
    assemble_messages: Callable[[TurnContextState], list[dict[str, Any]]]
    execute_tool_call: Callable[[Any, dict[str, Any]], Awaitable[Any]]
    on_tool_round: Callable[[Any, list[Any], TurnContextState], Awaitable[None]]
    check_cancel: Callable[..., Awaitable[None]]


@dataclass(frozen=True)
class OrchestrationStreamTurnProfile:
    runtime: Any
    assemble_messages: Callable[[TurnContextState], list[dict[str, Any]]]
    execute_tool: Callable[[str, dict[str, Any], str, str | None, int, int], Awaitable[Any]]
    on_tool_round: Callable[[Any, list[dict[str, Any]], list[Any], TurnContextState], Awaitable[None]]
    check_cancel: Callable[..., Awaitable[None]]
    build_llm_runtime_card: Callable[[Any, str, list[dict[str, Any]] | None, list[dict[str, Any]], dict[str, Any]], dict[str, Any]]


def build_orchestration_sync_turn_profile(
    *,
    runtime: Any,
    agent: Any,
    db: Any,
    project: Any,
    agents: list[Any],
    chatroom: Any,
    user_message: str,
    standalone_note: str,
    task_run: Any,
    client_turn_id: str | None,
    max_turns: int,
    assemble_chat_messages: Callable[..., list[dict[str, Any]]],
    save_tool_progress: Callable[[dict[str, Any]], Awaitable[None]],
    execute_tool: Callable[..., Awaitable[Any]],
    record_tool_call_started: Callable[..., Any],
    record_tool_round: Callable[..., Any],
    check_cancel: Callable[..., Awaitable[None]],
    compaction_callback: Callable[[dict[str, Any]], None] | None = None,
) -> OrchestrationSyncTurnProfile:
    """Build the turn-level sync execution profile for one orchestration agent turn."""

    runtime_context = build_runtime_environment_context(project)

    def _assemble_messages(current_turn_state: TurnContextState) -> list[dict[str, Any]]:
        return assemble_chat_messages(
            db=db,
            agent=agent,
            agent_name=runtime.agent_label,
            model_id=getattr(runtime.llm_client, "model", ""),
            chatroom=chatroom,
            project=project,
            agents=agents,
            recent_messages=runtime.recent_messages,
            user_message=user_message,
            available_tools=runtime.available_tools,
            tool_schemas=runtime.tool_schemas,
            tool_schemas_before_filter=getattr(runtime, "tool_schemas_before_filter", None),
            tool_schema_filter=getattr(runtime, "tool_schema_filter", None),
            history_limit=4,
            standalone_note=standalone_note,
            runtime_context=runtime_context,
            turn_state=current_turn_state,
            on_compaction=compaction_callback,
        )

    async def _execute_tool_call(frame: Any, tool_call: dict[str, Any]):
        tool_name = tool_call["function"]["name"]
        tool_args_str = tool_call["function"].get("arguments", "{}")
        record_tool_call_started(
            db,
            task_run,
            agent_name=runtime.agent_label,
            turn=frame.turn_index + 1,
            tool_name=tool_name,
            arguments=tool_args_str,
        )

        async def emit_tool_progress(progress: dict[str, Any]) -> None:
            await save_tool_progress(
                {
                    "type": "tool_call",
                    "source": "chatroom",
                    "agent": runtime.agent_label,
                    "tool": tool_name,
                    "arguments": tool_args_str,
                    "success": None,
                    "status": "running",
                    "blocked": False,
                    "result": str(progress.get("tail_output") or "").strip() or "Tool is running.",
                    "duration_ms": progress.get("duration_ms"),
                    "pid": progress.get("pid"),
                    "tracked_process": progress.get("tracked_process"),
                    "tool_call_id": tool_call.get("id"),
                    "client_turn_id": client_turn_id,
                    "run_id": getattr(task_run, "id", None) if task_run is not None else None,
                    "turn": frame.turn_index + 1,
                }
            )

        try:
            tool_args = json.loads(tool_args_str or "{}")
            tool_result = await execute_tool(
                tool_name,
                tool_args,
                tool_args_str,
                tool_call.get("id"),
                frame.turn_index + 1,
                emit_tool_progress if tool_name == "run_shell" else None,
            )
            tool_success = bool(tool_result.get("success")) if isinstance(tool_result, dict) and tool_result.get("__catown_tool_result__") is True else True
        except Exception as exc:
            tool_result = f"Error: {exc}"
            tool_success = False

        return build_tool_result_record(
            tool_call_id=tool_call.get("id"),
            tool_name=tool_name,
            arguments=tool_args_str,
            result=tool_result,
            success=tool_success,
        )

    async def _on_tool_round(frame: Any, tool_results: list[Any], current_turn_state: TurnContextState) -> None:
        blocked_tool_result = getattr(frame, "blocked_tool_result", None)
        record_tool_round(
            db,
            task_run,
            agent_name=runtime.agent_label,
            turn=frame.turn_index + 1,
            tool_names=[
                tool_call["function"]["name"]
                for tool_call in (
                    (frame.executed_tool_calls or frame.normalized_tool_calls)
                    + ([{"function": {"name": blocked_tool_result.tool_name}}] if blocked_tool_result is not None else [])
                )
            ],
            tool_results=tool_results,
            blocked_tool_results=[blocked_tool_result] if blocked_tool_result is not None else None,
            summary=f"{runtime.agent_label} completed a tool round.",
            assistant_content=frame.content,
        )

    return OrchestrationSyncTurnProfile(
        runtime=runtime,
        assemble_messages=_assemble_messages,
        execute_tool_call=_execute_tool_call,
        on_tool_round=_on_tool_round,
        check_cancel=check_cancel,
    )


def build_orchestration_stream_turn_profile(
    *,
    runtime: Any,
    agent: Any,
    db: Any,
    project: Any,
    agents: list[Any],
    chatroom: Any,
    user_message: str,
    history_limit: int,
    standalone_note: str,
    task_run: Any,
    client_turn_id: str | None,
    assemble_chat_messages: Callable[..., list[dict[str, Any]]],
    save_tool_progress: Callable[[dict[str, Any]], Awaitable[None]],
    execute_tool: Callable[..., Awaitable[Any]],
    record_tool_round: Callable[..., Any],
    check_cancel: Callable[..., Awaitable[None]],
    build_llm_card_payload: Callable[..., dict[str, Any]],
) -> OrchestrationStreamTurnProfile:
    """Build the turn-level stream execution profile for one orchestration agent turn."""

    runtime_context = build_runtime_environment_context(project)

    def _assemble_messages(current_turn_state: TurnContextState) -> list[dict[str, Any]]:
        return assemble_chat_messages(
            db=db,
            agent=agent,
            agent_name=runtime.agent_label,
            model_id=getattr(runtime.llm_client, "model", ""),
            chatroom=chatroom,
            project=project,
            agents=agents,
            recent_messages=runtime.recent_messages,
            user_message=user_message,
            available_tools=runtime.available_tools,
            tool_schemas=runtime.tool_schemas,
            tool_schemas_before_filter=getattr(runtime, "tool_schemas_before_filter", None),
            tool_schema_filter=getattr(runtime, "tool_schema_filter", None),
            history_limit=history_limit,
            standalone_note=standalone_note,
            runtime_context=runtime_context,
            turn_state=current_turn_state,
        )

    async def _execute_tool(
        tool_name: str,
        tool_args: dict[str, Any],
        tool_args_str: str,
        tool_call_id: str | None,
        tool_index: int,
        turn_index: int,
    ):
        async def emit_tool_progress(progress: dict[str, Any]) -> None:
            await save_tool_progress(
                {
                    "type": "tool_call",
                    "source": "chatroom",
                    "agent": runtime.agent_label,
                    "tool": tool_name,
                    "arguments": tool_args_str,
                    "success": None,
                    "status": "running",
                    "blocked": False,
                    "result": str(progress.get("tail_output") or "").strip() or "Tool is running.",
                    "duration_ms": progress.get("duration_ms"),
                    "pid": progress.get("pid"),
                    "tracked_process": progress.get("tracked_process"),
                    "tool_call_index": tool_index,
                    "tool_call_id": tool_call_id,
                    "client_turn_id": client_turn_id,
                    "run_id": getattr(task_run, "id", None) if task_run is not None else None,
                    "turn": turn_index,
                }
            )

        return await execute_tool(
            tool_name,
            tool_args,
            tool_args_str,
            tool_call_id,
            tool_index,
            turn_index,
            emit_tool_progress if tool_name == "run_shell" else None,
        )

    async def _on_tool_round(frame: Any, normalized_tool_calls: list[dict[str, Any]], tool_results: list[Any], current_turn_state: TurnContextState) -> None:
        blocked_tool_result = getattr(frame, "blocked_tool_result", None)
        record_tool_round(
            db,
            task_run,
            agent_name=runtime.agent_label,
            turn=frame.turn_index,
            tool_names=[
                tool_call["function"]["name"]
                for tool_call in (
                    normalized_tool_calls
                    + ([{"function": {"name": blocked_tool_result.tool_name}}] if blocked_tool_result is not None else [])
                )
            ],
            tool_results=tool_results,
            blocked_tool_results=[blocked_tool_result] if blocked_tool_result is not None else None,
            summary=f"{runtime.agent_label} completed a streaming tool round.",
            assistant_content=frame.llm_content,
        )

    def _build_llm_runtime_card(frame: Any, response_content: str, raw_tool_calls: list[dict[str, Any]] | None, tool_call_previews: list[dict[str, Any]], raw_event: dict[str, Any]) -> dict[str, Any]:
        return build_llm_card_payload(
            agent_name=runtime.agent_label,
            llm_client=runtime.llm_client,
            turn=frame.turn_index,
            duration_ms=int((raw_event.get("timings", {}) or {}).get("completed_ms") or ((time.time() - frame.llm_started_at) * 1000)),
            system_prompt=frame.system_prompt,
            prompt_messages=frame.prompt_snapshot,
            response_content=response_content,
            tool_call_previews=tool_call_previews,
            raw_tool_calls=raw_tool_calls,
            usage=raw_event.get("usage"),
            finish_reason=raw_event.get("finish_reason"),
            timings=raw_event.get("timings"),
            provider_session=raw_event.get("provider_session") if isinstance(raw_event.get("provider_session"), dict) else None,
            provider_request=raw_event.get("provider_request") if isinstance(raw_event.get("provider_request"), dict) else None,
        )

    return OrchestrationStreamTurnProfile(
        runtime=runtime,
        assemble_messages=_assemble_messages,
        execute_tool=_execute_tool,
        on_tool_round=_on_tool_round,
        check_cancel=check_cancel,
        build_llm_runtime_card=_build_llm_runtime_card,
    )
