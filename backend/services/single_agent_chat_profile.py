# -*- coding: utf-8 -*-
"""Convenience builders for single-agent chat runtime profiles."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from services.single_agent_session_orchestrator import (
    SingleAgentRawRuntimeInputs,
    SingleAgentRuntimeProfile,
    SingleAgentStreamFailurePolicy,
    SingleAgentStreamLoopCallbacks,
    SingleAgentStreamTransportContext,
    build_single_agent_runtime_profile_from_raw_inputs,
    build_single_agent_stream_raw_execution_envelope,
    build_single_agent_sync_raw_execution_envelope,
)


def build_single_agent_sync_chat_profile(
    *,
    runtime_inputs: SingleAgentRawRuntimeInputs,
    execute_turn: Callable[[], Awaitable[str | None]],
    on_empty: Callable[[], Awaitable[Any] | Any] | None = None,
) -> SingleAgentRuntimeProfile:
    """Build a managed sync runtime profile for one single-agent chat turn."""

    return build_single_agent_runtime_profile_from_raw_inputs(
        runtime_inputs=runtime_inputs,
        execution_inputs=build_single_agent_sync_raw_execution_envelope(
            execute_turn=execute_turn,
            on_empty=on_empty,
        ),
    )


def build_single_agent_stream_chat_profile(
    *,
    runtime_inputs: SingleAgentRawRuntimeInputs,
    llm_client: Any,
    tools: list[dict[str, Any]] | None,
    turn_state: Any,
    loop_callbacks: SingleAgentStreamLoopCallbacks,
    transport: SingleAgentStreamTransportContext,
    max_turns: int,
    stream_failure: SingleAgentStreamFailurePolicy | None = None,
) -> SingleAgentRuntimeProfile:
    """Build a managed stream runtime profile for one single-agent chat turn."""

    return build_single_agent_runtime_profile_from_raw_inputs(
        runtime_inputs=runtime_inputs,
        execution_inputs=build_single_agent_stream_raw_execution_envelope(
            llm_client=llm_client,
            tools=tools,
            turn_state=turn_state,
            loop_callbacks=loop_callbacks,
            transport=transport,
            max_turns=max_turns,
        ),
        stream_failure=stream_failure,
    )
