# -*- coding: utf-8 -*-
"""Shared streaming LLM/tool loop helpers."""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from services.turn_state import TurnContextState, build_tool_result_record, normalize_tool_call


@dataclass
class StreamTurnFrame:
    turn_index: int
    agent_name: str
    client_turn_id: str | None
    messages: list[dict[str, Any]]
    prompt_snapshot: list[dict[str, Any]]
    system_prompt: str
    llm_started_at: float
    model: str | None = None
    llm_content: str = ""
    executed_tool_calls: list[dict[str, Any]] | None = None
    blocked_tool_result: Any = None


async def iter_stream_turn_events(
    *,
    llm_client: Any,
    tools: list[dict[str, Any]] | None,
    turn_state: TurnContextState,
    agent_name: str,
    client_turn_id: str | None,
    assemble_messages: Callable[[TurnContextState], list[dict[str, Any]]],
    execute_tool: Callable[[str, dict[str, Any], str, str | None, int, int], Awaitable[Any]],
    build_llm_runtime_card: Callable[[StreamTurnFrame, str, list[dict[str, Any]] | None, list[dict[str, Any]], dict[str, Any]], dict[str, Any]],
    snapshot_messages: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    preview_tool_calls: Callable[[Optional[list[dict[str, Any]]]], list[dict[str, Any]]],
    format_prompt_messages: Callable[[list[dict[str, Any]]], Any],
    tool_result_success: Callable[[str], bool],
    max_turns: int = 20,
    before_turn: Callable[[int, TurnContextState], Awaitable[None] | None] | None = None,
    before_event: Callable[[StreamTurnFrame, dict[str, Any], TurnContextState], Awaitable[None] | None] | None = None,
    before_tool_call: Callable[[StreamTurnFrame, dict[str, Any], TurnContextState], Awaitable[None] | None] | None = None,
    on_tool_round: Callable[[StreamTurnFrame, list[dict[str, Any]], list[Any], TurnContextState], Awaitable[None] | None] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    final_content = ""

    for iteration in range(max_turns):
        if before_turn is not None:
            await _maybe_await(before_turn(iteration, turn_state))
        messages = assemble_messages(turn_state)
        prompt_snapshot = snapshot_messages(messages)
        system_prompt = messages[0]["content"] if messages else ""
        frame = StreamTurnFrame(
            turn_index=iteration + 1,
            agent_name=agent_name,
            client_turn_id=client_turn_id,
            messages=messages,
            prompt_snapshot=prompt_snapshot,
            system_prompt=system_prompt,
            llm_started_at=time.time(),
            model=getattr(llm_client, "model", None),
        )
        tool_calls_found = False

        agent_start_event = {
            "type": "agent_start",
            "agent_name": agent_name,
            "model": getattr(llm_client, "model", ""),
            "turn": frame.turn_index,
            "prompt_payload_omitted": True,
            "client_turn_id": client_turn_id,
        }
        if before_event is not None:
            await _maybe_await(before_event(frame, agent_start_event, turn_state))
        yield agent_start_event

        chat_stream_kwargs = _chat_stream_provider_kwargs(llm_client, frame)
        async for event in _iter_with_heartbeat(llm_client.chat_stream(messages, tools or None, **chat_stream_kwargs)):
            if before_event is not None:
                await _maybe_await(before_event(frame, event, turn_state))
            event_type = event["type"]
            if event_type == "__heartbeat__":
                elapsed_ms = int((time.time() - frame.llm_started_at) * 1000)
                yield {
                    "type": "llm_wait",
                    "agent": agent_name,
                    "elapsed_ms": elapsed_ms,
                    "turn": frame.turn_index,
                }
                continue

            if event_type in {"request_sent", "first_chunk", "first_content"}:
                yield {
                    "type": event_type,
                    "agent": agent_name,
                    "elapsed_ms": event.get("elapsed_ms"),
                    "turn": frame.turn_index,
                }
                continue

            if event_type == "tool_call_delta":
                yield {
                    "type": "tool_call_delta",
                    "agent": agent_name,
                    "elapsed_ms": event.get("elapsed_ms"),
                    "turn": frame.turn_index,
                    "tool_call_index": event.get("tool_call_index"),
                    "tool": event.get("tool_name") or "tool",
                    "args": event.get("arguments") or "",
                    "tool_call_id": (event.get("tool_call") or {}).get("id"),
                }
                continue

            if event_type == "tool_call_ready":
                yield {
                    "type": "tool_call_ready",
                    "agent": agent_name,
                    "elapsed_ms": event.get("elapsed_ms"),
                    "turn": frame.turn_index,
                    "tool_calls": event.get("tool_calls") or [],
                }
                continue

            if event_type == "content":
                delta = event.get("delta") or ""
                frame.llm_content += delta
                yield {"type": "content", "delta": delta, "agent": agent_name}
                continue

            if event_type == "done":
                raw_tool_calls = event.get("tool_calls")
                full_content = event.get("full_content")
                blocked_tool_result = None
                if full_content is None:
                    full_content = frame.llm_content
                llm_tool_calls = preview_tool_calls(raw_tool_calls)
                normalized_tool_calls = [normalize_tool_call(tool_call) for tool_call in (raw_tool_calls or [])]

                if normalized_tool_calls:
                    tool_calls_found = True
                    tool_results = []
                    executed_tool_calls: list[dict[str, Any]] = []
                    for tool_index, tool_call in enumerate(normalized_tool_calls):
                        if before_tool_call is not None:
                            await _maybe_await(before_tool_call(frame, tool_call, turn_state))
                        tool_name = tool_call["function"]["name"]
                        tool_args_str = tool_call["function"].get("arguments", "{}")
                        tool_call_id = tool_call.get("id")

                        yield {
                            "type": "tool_start",
                            "tool": tool_name,
                            "agent": agent_name,
                            "args": tool_args_str,
                            "tool_call_index": tool_index,
                            "tool_call_id": tool_call_id,
                        }

                        tool_started_at = time.time()
                        raw_result: Any = "(no output)"
                        try:
                            tool_args = json.loads(tool_args_str or "{}")
                        except json.JSONDecodeError:
                            tool_args = {}

                        try:
                            async for tool_event in _await_with_heartbeat(
                                execute_tool(
                                    tool_name,
                                    tool_args,
                                    tool_args_str,
                                    tool_call_id,
                                    tool_index,
                                    frame.turn_index,
                                )
                            ):
                                if tool_event["type"] == "__heartbeat__":
                                    elapsed_ms = int((time.time() - tool_started_at) * 1000)
                                    yield {
                                        "type": "tool_wait",
                                        "tool": tool_name,
                                        "agent": agent_name,
                                        "elapsed_ms": elapsed_ms,
                                        "tool_call_index": tool_index,
                                        "tool_call_id": tool_call_id,
                                    }
                                    continue
                                raw_result = tool_event["result"]
                                break
                        except Exception as exc:
                            raw_result = f"Error: {exc}"

                        success = tool_result_success(raw_result)
                        tool_result_record = build_tool_result_record(
                            tool_call_id=tool_call_id,
                            tool_name=tool_name,
                            arguments=tool_args_str,
                            result=raw_result,
                            success=success,
                        )
                        tool_duration_ms = int((time.time() - tool_started_at) * 1000)

                        yield {
                            "type": "tool_result",
                            "tool": tool_name,
                            "result": tool_result_record.result[:500],
                            "success": tool_result_record.success,
                            "status": tool_result_record.status,
                            "blocked": tool_result_record.blocked,
                            "blocked_kind": tool_result_record.blocked_kind,
                            "blocked_reason": tool_result_record.blocked_reason,
                            "agent": agent_name,
                            "tool_call_index": tool_index,
                            "tool_call_id": tool_call_id,
                        }
                        yield {
                            "type": "runtime_card",
                            "card_type": "tool_call",
                            "payload": {
                                "agent": agent_name,
                                "tool": tool_name,
                                "arguments": tool_args_str,
                                "success": tool_result_record.success,
                                "status": tool_result_record.status,
                                "blocked": tool_result_record.blocked,
                                "blocked_kind": tool_result_record.blocked_kind,
                                "blocked_reason": tool_result_record.blocked_reason,
                                "result": tool_result_record.result[:1500],
                                "duration_ms": tool_duration_ms,
                                "tool_call_index": tool_index,
                                "tool_call_id": tool_call_id,
                            },
                        }
                        if tool_result_record.blocked:
                            blocked_tool_result = tool_result_record
                            break
                        executed_tool_calls.append(tool_call)
                        tool_results.append(tool_result_record)

                    frame.executed_tool_calls = executed_tool_calls
                    frame.blocked_tool_result = blocked_tool_result

                    if tool_results:
                        turn_state.record_tool_round(
                            assistant_content=full_content or frame.llm_content,
                            tool_calls=executed_tool_calls,
                            tool_results=tool_results,
                        )
                    if on_tool_round is not None and (tool_results or blocked_tool_result is not None):
                        await _maybe_await(on_tool_round(frame, executed_tool_calls, tool_results, turn_state))
                else:
                    final_content = full_content or frame.llm_content

                yield {
                    "type": "runtime_card",
                    "card_type": "llm_call",
                    "payload": build_llm_runtime_card(
                        frame,
                        full_content or frame.llm_content,
                        raw_tool_calls,
                        llm_tool_calls,
                        event,
                    ),
                }
                if blocked_tool_result is not None:
                    blocked_metadata = (
                        getattr(blocked_tool_result, "metadata", {})
                        if isinstance(getattr(blocked_tool_result, "metadata", None), dict)
                        else {}
                    )
                    yield {
                        "type": "approval_queue_updated",
                        "agent_name": agent_name,
                        "client_turn_id": client_turn_id,
                        "turn": frame.turn_index,
                        "queue_item_id": blocked_metadata.get("queue_item_id"),
                        "queue_item_public_id": blocked_metadata.get("queue_item_public_id"),
                    }
                    return
                continue

            if event_type == "error":
                raise RuntimeError(event["error"])

        if not tool_calls_found:
            break

    yield {
        "type": "turn_complete",
        "agent_name": agent_name,
        "content": final_content,
        "client_turn_id": client_turn_id,
    }


async def _iter_with_heartbeat(stream: Any, timeout: float = 1.0):
    iterator = stream.__aiter__()
    pending = asyncio.create_task(iterator.__anext__())
    try:
        while True:
            try:
                event = await asyncio.wait_for(asyncio.shield(pending), timeout=timeout)
            except asyncio.TimeoutError:
                yield {"type": "__heartbeat__"}
                continue
            except StopAsyncIteration:
                break

            yield event
            pending = asyncio.create_task(iterator.__anext__())
    finally:
        if pending and not pending.done():
            pending.cancel()


async def _await_with_heartbeat(awaitable: Awaitable[Any], timeout: float = 1.0):
    pending = asyncio.create_task(awaitable)
    try:
        while True:
            try:
                result = await asyncio.wait_for(asyncio.shield(pending), timeout=timeout)
            except asyncio.TimeoutError:
                yield {"type": "__heartbeat__"}
                continue

            yield {"type": "__result__", "result": result}
            break
    finally:
        if pending and not pending.done():
            pending.cancel()


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


def _chat_stream_provider_kwargs(llm_client: Any, frame: StreamTurnFrame) -> dict[str, Any]:
    provider_session = getattr(frame, "provider_session", None)
    if not isinstance(provider_session, dict):
        return {}

    try:
        signature = inspect.signature(llm_client.chat_stream)
    except (TypeError, ValueError):
        return {}

    supports_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    kwargs: dict[str, Any] = {}
    if supports_kwargs or "provider_session" in signature.parameters:
        kwargs["provider_session"] = provider_session
    previous_response_id = (
        provider_session.get("previous_response_id")
        or provider_session.get("last_response_id")
    )
    if previous_response_id and (supports_kwargs or "previous_response_id" in signature.parameters):
        kwargs["previous_response_id"] = previous_response_id
    return kwargs
