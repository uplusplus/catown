# -*- coding: utf-8 -*-
"""Shared non-streaming LLM/tool loop helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from services.turn_state import ToolResultRecord, TurnContextState, normalize_tool_call


@dataclass
class NonStreamTurnFrame:
    turn_index: int
    messages: list[dict[str, Any]]
    model: str | None = None
    response: dict[str, Any] = field(default_factory=dict)
    content: str = ""
    normalized_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    executed_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: Any = None
    state: Any = None
    blocked_tool_result: ToolResultRecord | None = None


@dataclass(frozen=True)
class NonStreamTurnLoopResult:
    final_content: str = ""
    awaiting_tool_approval: bool = False
    awaiting_background_tool: bool = False


async def execute_non_stream_turn_loop(
    *,
    llm_client: Any,
    tools: list[dict[str, Any]] | None,
    turn_state: TurnContextState,
    assemble_messages: Callable[[TurnContextState], list[dict[str, Any]]],
    execute_tool_call: Callable[[NonStreamTurnFrame, dict[str, Any]], Awaitable[ToolResultRecord]],
    max_turns: int = 20,
    before_turn: Callable[[int, TurnContextState], Awaitable[None] | None] | None = None,
    before_llm_call: Callable[[NonStreamTurnFrame, TurnContextState], Awaitable[Any] | Any] | None = None,
    on_llm_response: Callable[[NonStreamTurnFrame, TurnContextState], Awaitable[None] | None] | None = None,
    on_llm_error: Callable[[NonStreamTurnFrame, Exception, TurnContextState], Awaitable[None] | None] | None = None,
    before_tool_call: Callable[[NonStreamTurnFrame, dict[str, Any], TurnContextState], Awaitable[None] | None] | None = None,
    on_tool_round: Callable[[NonStreamTurnFrame, list[ToolResultRecord], TurnContextState], Awaitable[None] | None] | None = None,
) -> NonStreamTurnLoopResult:
    final_content = ""

    for turn_index in range(max_turns):
        if before_turn is not None:
            await _maybe_await(before_turn(turn_index, turn_state))

        frame = NonStreamTurnFrame(
            turn_index=turn_index,
            messages=assemble_messages(turn_state),
            model=getattr(llm_client, "model", None),
        )
        if before_llm_call is not None:
            frame.state = await _maybe_await(before_llm_call(frame, turn_state))

        try:
            frame.response = await llm_client.chat_with_tools(frame.messages, tools if tools else None)
        except Exception as exc:
            if on_llm_error is not None:
                await _maybe_await(on_llm_error(frame, exc, turn_state))
            raise

        frame.content = frame.response.get("content") or ""
        frame.normalized_tool_calls = [
            normalize_tool_call(tool_call)
            for tool_call in (frame.response.get("tool_calls") or [])
        ]
        frame.usage = frame.response.get("usage")

        if on_llm_response is not None:
            await _maybe_await(on_llm_response(frame, turn_state))

        if frame.normalized_tool_calls:
            tool_results: list[ToolResultRecord] = []
            executed_tool_calls: list[dict[str, Any]] = []
            blocked_tool_result: ToolResultRecord | None = None
            for tool_call in frame.normalized_tool_calls:
                if before_tool_call is not None:
                    await _maybe_await(before_tool_call(frame, tool_call, turn_state))
                result = await execute_tool_call(frame, tool_call)
                if getattr(result, "blocked", False):
                    blocked_tool_result = result
                    break
                executed_tool_calls.append(tool_call)
                tool_results.append(result)

            frame.executed_tool_calls = executed_tool_calls
            frame.blocked_tool_result = blocked_tool_result

            if tool_results:
                turn_state.record_tool_round(
                    assistant_content=frame.content,
                    tool_calls=executed_tool_calls,
                    tool_results=tool_results,
                )

            if on_tool_round is not None and (tool_results or blocked_tool_result is not None):
                tool_round_decision = await _maybe_await(on_tool_round(frame, tool_results, turn_state))
                if isinstance(tool_round_decision, dict) and tool_round_decision.get("stop"):
                    return NonStreamTurnLoopResult(
                        final_content=str(tool_round_decision.get("final_content") or ""),
                        awaiting_tool_approval=False,
                        awaiting_background_tool=bool(tool_round_decision.get("awaiting_background_tool")),
                    )
            if blocked_tool_result is not None:
                return NonStreamTurnLoopResult(awaiting_tool_approval=True)
            continue

        if not frame.content:
            break
        final_content = frame.content
        break

    return NonStreamTurnLoopResult(final_content=final_content)


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value
