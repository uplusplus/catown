# -*- coding: utf-8 -*-
"""Shared streaming session runner for standalone and single-agent chat paths."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Dict

from services.stream_transport import iter_rendered_stream_turn_events
from services.stream_turn_executor import iter_stream_turn_events


@dataclass(frozen=True)
class SingleAgentStreamSessionResult:
    chunk: str | None = None
    final_content: str | None = None


@dataclass(frozen=True)
class SingleAgentStreamSessionDeps:
    llm_client: Any
    tools: list[dict[str, Any]] | None
    turn_state: Any
    agent_name: str
    client_turn_id: str | None
    assemble_messages: Callable[[Any], list[dict[str, Any]]]
    execute_tool: Callable[..., Awaitable[Any]]
    build_llm_runtime_card: Callable[..., dict[str, Any]]
    snapshot_messages: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
    preview_tool_calls: Callable[[Any], list[dict[str, Any]]]
    format_prompt_messages: Callable[[list[dict[str, Any]]], Any]
    tool_result_success: Callable[[str], bool]
    serialize_payload: Callable[[Any], str]
    store_runtime_card: Callable[[int, Dict[str, Any]], Awaitable[Any]]
    public_runtime_card_payload: Callable[[Dict[str, Any]], Dict[str, Any]]
    chatroom_id: int
    max_turns: int
    on_tool_round: Callable[..., Awaitable[None] | None] | None = None


@dataclass(frozen=True)
class SingleAgentStreamExecutionContext:
    llm_client: Any
    tools: list[dict[str, Any]] | None
    turn_state: Any
    assemble_messages: Callable[[Any], list[dict[str, Any]]]
    execute_tool: Callable[..., Awaitable[Any]]
    build_llm_runtime_card: Callable[..., dict[str, Any]]
    snapshot_messages: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
    preview_tool_calls: Callable[[Any], list[dict[str, Any]]]
    format_prompt_messages: Callable[[list[dict[str, Any]]], Any]
    tool_result_success: Callable[[str], bool]
    serialize_payload: Callable[[Any], str]
    store_runtime_card: Callable[[int, Dict[str, Any]], Awaitable[Any]]
    public_runtime_card_payload: Callable[[Dict[str, Any]], Dict[str, Any]]
    max_turns: int
    on_tool_round: Callable[..., Awaitable[None] | None] | None = None


@dataclass(frozen=True)
class SingleAgentStreamRawExecutionInputs:
    llm_client: Any
    tools: list[dict[str, Any]] | None
    turn_state: Any
    assemble_messages: Callable[[Any], list[dict[str, Any]]]
    execute_tool: Callable[..., Awaitable[Any]]
    build_llm_runtime_card: Callable[..., dict[str, Any]]
    snapshot_messages: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
    preview_tool_calls: Callable[[Any], list[dict[str, Any]]]
    format_prompt_messages: Callable[[list[dict[str, Any]]], Any]
    tool_result_success: Callable[[str], bool]
    serialize_payload: Callable[[Any], str]
    store_runtime_card: Callable[[int, Dict[str, Any]], Awaitable[Any]]
    public_runtime_card_payload: Callable[[Dict[str, Any]], Dict[str, Any]]
    max_turns: int
    on_tool_round: Callable[..., Awaitable[None] | None] | None = None


def build_single_agent_stream_raw_execution_inputs(
    *,
    llm_client: Any,
    tools: list[dict[str, Any]] | None,
    turn_state: Any,
    assemble_messages: Callable[[Any], list[dict[str, Any]]],
    execute_tool: Callable[..., Awaitable[Any]],
    build_llm_runtime_card: Callable[..., dict[str, Any]],
    snapshot_messages: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    preview_tool_calls: Callable[[Any], list[dict[str, Any]]],
    format_prompt_messages: Callable[[list[dict[str, Any]]], Any],
    tool_result_success: Callable[[str], bool],
    serialize_payload: Callable[[Any], str],
    store_runtime_card: Callable[[int, Dict[str, Any]], Awaitable[Any]],
    public_runtime_card_payload: Callable[[Dict[str, Any]], Dict[str, Any]],
    max_turns: int,
    on_tool_round: Callable[..., Awaitable[None] | None] | None = None,
) -> SingleAgentStreamRawExecutionInputs:
    """Build the raw stream-side execution input bundle for one single-agent turn."""

    return SingleAgentStreamRawExecutionInputs(
        llm_client=llm_client,
        tools=tools,
        turn_state=turn_state,
        assemble_messages=assemble_messages,
        execute_tool=execute_tool,
        build_llm_runtime_card=build_llm_runtime_card,
        snapshot_messages=snapshot_messages,
        preview_tool_calls=preview_tool_calls,
        format_prompt_messages=format_prompt_messages,
        tool_result_success=tool_result_success,
        serialize_payload=serialize_payload,
        store_runtime_card=store_runtime_card,
        public_runtime_card_payload=public_runtime_card_payload,
        max_turns=max_turns,
        on_tool_round=on_tool_round,
    )


def build_single_agent_stream_execution_context(
    *,
    llm_client: Any,
    tools: list[dict[str, Any]] | None,
    turn_state: Any,
    assemble_messages: Callable[[Any], list[dict[str, Any]]],
    execute_tool: Callable[..., Awaitable[Any]],
    build_llm_runtime_card: Callable[..., dict[str, Any]],
    snapshot_messages: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    preview_tool_calls: Callable[[Any], list[dict[str, Any]]],
    format_prompt_messages: Callable[[list[dict[str, Any]]], Any],
    tool_result_success: Callable[[str], bool],
    serialize_payload: Callable[[Any], str],
    store_runtime_card: Callable[[int, Dict[str, Any]], Awaitable[Any]],
    public_runtime_card_payload: Callable[[Dict[str, Any]], Dict[str, Any]],
    max_turns: int,
    on_tool_round: Callable[..., Awaitable[None] | None] | None = None,
) -> SingleAgentStreamExecutionContext:
    """Build the stream-specific execution context for one single-agent stream run."""

    return SingleAgentStreamExecutionContext(
        llm_client=llm_client,
        tools=tools,
        turn_state=turn_state,
        assemble_messages=assemble_messages,
        execute_tool=execute_tool,
        build_llm_runtime_card=build_llm_runtime_card,
        snapshot_messages=snapshot_messages,
        preview_tool_calls=preview_tool_calls,
        format_prompt_messages=format_prompt_messages,
        tool_result_success=tool_result_success,
        serialize_payload=serialize_payload,
        store_runtime_card=store_runtime_card,
        public_runtime_card_payload=public_runtime_card_payload,
        max_turns=max_turns,
        on_tool_round=on_tool_round,
    )


def build_single_agent_stream_execution_context_from_raw_inputs(
    inputs: SingleAgentStreamRawExecutionInputs,
) -> SingleAgentStreamExecutionContext:
    """Promote raw stream execution inputs into the stream execution context model."""

    return build_single_agent_stream_execution_context(
        llm_client=inputs.llm_client,
        tools=inputs.tools,
        turn_state=inputs.turn_state,
        assemble_messages=inputs.assemble_messages,
        execute_tool=inputs.execute_tool,
        build_llm_runtime_card=inputs.build_llm_runtime_card,
        snapshot_messages=inputs.snapshot_messages,
        preview_tool_calls=inputs.preview_tool_calls,
        format_prompt_messages=inputs.format_prompt_messages,
        tool_result_success=inputs.tool_result_success,
        serialize_payload=inputs.serialize_payload,
        store_runtime_card=inputs.store_runtime_card,
        public_runtime_card_payload=inputs.public_runtime_card_payload,
        max_turns=inputs.max_turns,
        on_tool_round=inputs.on_tool_round,
    )


def build_single_agent_stream_session_deps(
    *,
    llm_client: Any,
    tools: list[dict[str, Any]] | None,
    turn_state: Any,
    agent_name: str,
    client_turn_id: str | None,
    assemble_messages: Callable[[Any], list[dict[str, Any]]],
    execute_tool: Callable[..., Awaitable[Any]],
    build_llm_runtime_card: Callable[..., dict[str, Any]],
    snapshot_messages: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    preview_tool_calls: Callable[[Any], list[dict[str, Any]]],
    format_prompt_messages: Callable[[list[dict[str, Any]]], Any],
    tool_result_success: Callable[[str], bool],
    serialize_payload: Callable[[Any], str],
    store_runtime_card: Callable[[int, Dict[str, Any]], Awaitable[Any]],
    public_runtime_card_payload: Callable[[Dict[str, Any]], Dict[str, Any]],
    chatroom_id: int,
    max_turns: int,
    on_tool_round: Callable[..., Awaitable[None] | None] | None = None,
) -> SingleAgentStreamSessionDeps:
    """Build the low-level deps bundle for a single-agent streaming session."""

    return SingleAgentStreamSessionDeps(
        llm_client=llm_client,
        tools=tools,
        turn_state=turn_state,
        agent_name=agent_name,
        client_turn_id=client_turn_id,
        assemble_messages=assemble_messages,
        execute_tool=execute_tool,
        build_llm_runtime_card=build_llm_runtime_card,
        snapshot_messages=snapshot_messages,
        preview_tool_calls=preview_tool_calls,
        format_prompt_messages=format_prompt_messages,
        tool_result_success=tool_result_success,
        serialize_payload=serialize_payload,
        store_runtime_card=store_runtime_card,
        public_runtime_card_payload=public_runtime_card_payload,
        chatroom_id=chatroom_id,
        max_turns=max_turns,
        on_tool_round=on_tool_round,
    )


def build_single_agent_stream_session_deps_from_execution_context(
    *,
    execution: SingleAgentStreamExecutionContext,
    agent_name: str,
    client_turn_id: str | None,
    chatroom_id: int,
) -> SingleAgentStreamSessionDeps:
    """Build the low-level deps bundle from a higher-level execution context."""

    return build_single_agent_stream_session_deps(
        llm_client=execution.llm_client,
        tools=execution.tools,
        turn_state=execution.turn_state,
        agent_name=agent_name,
        client_turn_id=client_turn_id,
        assemble_messages=execution.assemble_messages,
        execute_tool=execution.execute_tool,
        build_llm_runtime_card=execution.build_llm_runtime_card,
        snapshot_messages=execution.snapshot_messages,
        preview_tool_calls=execution.preview_tool_calls,
        format_prompt_messages=execution.format_prompt_messages,
        tool_result_success=execution.tool_result_success,
        serialize_payload=execution.serialize_payload,
        store_runtime_card=execution.store_runtime_card,
        public_runtime_card_payload=execution.public_runtime_card_payload,
        chatroom_id=chatroom_id,
        max_turns=execution.max_turns,
        on_tool_round=execution.on_tool_round,
    )


async def iter_single_agent_stream_session(
    deps: SingleAgentStreamSessionDeps,
) -> AsyncIterator[SingleAgentStreamSessionResult]:
    """Render a single-agent stream turn session into chunks plus final content."""

    async for rendered in iter_rendered_stream_turn_events(
        iter_stream_turn_events(
            llm_client=deps.llm_client,
            tools=deps.tools,
            turn_state=deps.turn_state,
            agent_name=deps.agent_name,
            client_turn_id=deps.client_turn_id,
            assemble_messages=deps.assemble_messages,
            execute_tool=deps.execute_tool,
            build_llm_runtime_card=deps.build_llm_runtime_card,
            snapshot_messages=deps.snapshot_messages,
            preview_tool_calls=deps.preview_tool_calls,
            format_prompt_messages=deps.format_prompt_messages,
            tool_result_success=deps.tool_result_success,
            max_turns=deps.max_turns,
            on_tool_round=deps.on_tool_round,
        ),
        chatroom_id=deps.chatroom_id,
        client_turn_id=deps.client_turn_id,
        serialize_payload=deps.serialize_payload,
        store_runtime_card=deps.store_runtime_card,
        public_runtime_card_payload=deps.public_runtime_card_payload,
    ):
        if rendered.turn_complete_content is not None:
            yield SingleAgentStreamSessionResult(final_content=rendered.turn_complete_content)
            continue
        yield SingleAgentStreamSessionResult(chunk=rendered.chunk)
