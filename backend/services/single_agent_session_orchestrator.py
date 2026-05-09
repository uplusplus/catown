# -*- coding: utf-8 -*-
"""Higher-level orchestration for single-agent sync/stream session drivers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Literal

from services.single_agent_session_terminal import PostPublishSuccess

from services.single_agent_session_callbacks import (
    build_single_agent_stream_callback_profile,
    build_single_agent_stream_callbacks,
    build_single_agent_sync_callback_profile,
    build_single_agent_sync_callbacks,
)
from services.single_agent_session_contracts import (
    ManagedSingleAgentSessionCallbacks,
    ManagedSingleAgentSessionSpec,
    ManagedSingleAgentStreamTransport,
    UnifiedSingleAgentSessionOutcome,
    UnifiedSingleAgentSessionSpec,
)
from services.single_agent_session_runner import (
    SingleAgentSyncExecutionContext,
    SingleAgentSyncRawExecutionInputs,
    build_single_agent_session_runner_deps_from_execution_context,
    build_single_agent_sync_execution_context,
    build_single_agent_sync_execution_context_from_raw_inputs,
    build_single_agent_sync_raw_execution_inputs,
    run_single_agent_session,
)
from services.single_agent_stream_session import (
    SingleAgentStreamExecutionContext,
    SingleAgentStreamRawExecutionInputs,
    SingleAgentStreamSessionDeps,
    build_single_agent_stream_execution_context,
    build_single_agent_stream_execution_context_from_raw_inputs,
    build_single_agent_stream_loop_callbacks,
    build_single_agent_stream_raw_execution_inputs,
    build_single_agent_stream_session_deps,
    build_single_agent_stream_session_deps_from_execution_context,
    build_single_agent_stream_transport_context,
    SingleAgentStreamLoopCallbacks,
    SingleAgentStreamTransportContext,
    iter_single_agent_stream_session,
)
from services.stream_transport import render_sse_payload


@dataclass(frozen=True)
class SingleAgentSessionRuntimeContext:
    db: Any
    task_run: Any
    chatroom_id: int
    client_turn_id: str | None
    agent_id: int | None
    agent_name: str
    agent_type: str
    user_message: str
    save_message: Callable[..., Awaitable[Any]]
    publish_message: Callable[..., Awaitable[Any]]
    record_turn_completed: Callable[..., Any]
    message_metadata: Callable[..., dict[str, Any]]
    compact_summary: Callable[[Any], str]
    completion_summary: str
    failure_summary: str | Callable[[Exception], str]
    extract_memories: Callable[[int, str, str, str], Awaitable[Any]]
    stream_failure_message_metadata: Callable[[str | None, dict[str, Any] | None], dict[str, Any]] | None = None
    post_publish_success: PostPublishSuccess | None = None
    min_response_length: int = 30


@dataclass(frozen=True)
class SingleAgentRawRuntimeInputs:
    db: Any
    task_run: Any
    chatroom_id: int
    client_turn_id: str | None
    agent_id: int | None
    agent_name: str
    agent_type: str
    user_message: str
    save_message: Callable[..., Awaitable[Any]]
    publish_message: Callable[..., Awaitable[Any]]
    record_turn_completed: Callable[..., Any]
    message_metadata: Callable[..., dict[str, Any]]
    compact_summary: Callable[[Any], str]
    completion_summary: str
    failure_summary: str | Callable[[Exception], str]
    extract_memories: Callable[[int, str, str, str], Awaitable[Any]]
    stream_failure_message_metadata: Callable[[str | None, dict[str, Any] | None], dict[str, Any]] | None = None
    post_publish_success: PostPublishSuccess | None = None
    min_response_length: int = 30


@dataclass(frozen=True)
class SingleAgentRawExecutionInputs:
    mode: Literal["sync", "stream"]
    execution: SingleAgentSyncRawExecutionInputs | SingleAgentStreamRawExecutionInputs


@dataclass(frozen=True)
class SingleAgentRuntimeProfile:
    session: ManagedSingleAgentSessionSpec


@dataclass(frozen=True)
class SingleAgentStreamFailurePolicy:
    failure_agent_name: str | None = None
    failure_agent_id: int | None = None
    detail_builder: Callable[[], str] | None = None
    final_message_saved: bool = False
    empty_response_text: str = "(Agent returned empty response)"


def build_single_agent_stream_failure_policy(
    *,
    failure_agent_name: str | None = None,
    failure_agent_id: int | None = None,
    detail_builder: Callable[[], str] | None = None,
    final_message_saved: bool = False,
    empty_response_text: str = "(Agent returned empty response)",
) -> SingleAgentStreamFailurePolicy:
    """Build the failure-policy bundle for one single-agent stream run."""

    return SingleAgentStreamFailurePolicy(
        failure_agent_name=failure_agent_name,
        failure_agent_id=failure_agent_id,
        detail_builder=detail_builder,
        final_message_saved=final_message_saved,
        empty_response_text=empty_response_text,
    )


async def run_unified_single_agent_sync_session(
    spec: UnifiedSingleAgentSessionSpec,
) -> UnifiedSingleAgentSessionOutcome:
    """Run one sync single-agent session through the higher-level unified facade."""

    final_content = None
    async for item in spec.iterate():
        if item.chunk is not None:
            raise ValueError("Sync single-agent session emitted stream chunks unexpectedly.")
        if item.final_content is not None:
            final_content = item.final_content
    return UnifiedSingleAgentSessionOutcome(final_content=final_content)


async def iter_unified_single_agent_stream_session(
    spec: UnifiedSingleAgentSessionSpec,
) -> AsyncIterator[UnifiedSingleAgentSessionOutcome]:
    """Run one stream single-agent session through the higher-level unified facade."""

    async for item in spec.iterate():
        yield item


async def run_managed_single_agent_sync_session(
    spec: ManagedSingleAgentSessionSpec,
) -> UnifiedSingleAgentSessionOutcome:
    """Run one sync single-agent session through the managed stack surface."""

    result = await run_single_agent_session(
        build_single_agent_session_runner_deps_from_execution_context(
            execution=build_single_agent_sync_execution_context(
                execute_turn=lambda: _consume_sync_session(spec.session),
            ),
            finalize_success=spec.callbacks.finalize_success,
            finalize_failure=spec.callbacks.finalize_failure,
        )
    )
    return UnifiedSingleAgentSessionOutcome(final_content=result.final_content)


async def iter_managed_single_agent_stream_session(
    spec: ManagedSingleAgentSessionSpec,
) -> AsyncIterator[UnifiedSingleAgentSessionOutcome]:
    """Run one stream single-agent session through the managed stack surface."""

    transport = _require_stream_transport(spec)
    final_content = ""
    try:
        async for item in iter_unified_single_agent_stream_session(spec.session):
            if item.awaiting_tool_approval:
                if item.chunk is not None:
                    yield item
                return
            if item.final_content is not None:
                final_content = item.final_content
                continue
            if item.chunk is not None:
                yield item
    except Exception as exc:
        finalized = await spec.callbacks.finalize_failure(exc)
        yield UnifiedSingleAgentSessionOutcome(
            chunk=render_sse_payload(finalized.payload, serialize_payload=transport.serialize_payload),
            payload=dict(finalized.payload or {}),
            error_text=getattr(finalized, "error_text", None),
        )
        return

    finalized = await spec.callbacks.finalize_success(final_content)
    yield UnifiedSingleAgentSessionOutcome(
        final_content=final_content,
        chunk=render_sse_payload(finalized.payload, serialize_payload=transport.serialize_payload),
        payload=dict(finalized.payload or {}),
    )


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


def _require_stream_transport(
    spec: ManagedSingleAgentSessionSpec,
) -> ManagedSingleAgentStreamTransport:
    if spec.stream_transport is None:
        raise ValueError("Managed single-agent stream session requires stream transport.")
    return spec.stream_transport


def build_unified_sync_single_agent_session_spec(
    *,
    execute_turn: Callable[[], Awaitable[str | None]],
    on_empty: Callable[[], Awaitable[Any] | Any] | None = None,
) -> UnifiedSingleAgentSessionSpec:
    """Build the unified spec for a sync single-agent session."""

    async def _iterate() -> AsyncIterator[UnifiedSingleAgentSessionOutcome]:
        final_content = await execute_turn()
        if not final_content:
            if on_empty is not None:
                await _maybe_await(on_empty())
            return
        yield UnifiedSingleAgentSessionOutcome(final_content=final_content)

    return UnifiedSingleAgentSessionSpec(iterate=_iterate)


def build_unified_stream_single_agent_session_spec(
    *,
    deps: SingleAgentStreamSessionDeps,
) -> UnifiedSingleAgentSessionSpec:
    """Build the unified spec for a streaming single-agent session."""

    async def _iterate() -> AsyncIterator[UnifiedSingleAgentSessionOutcome]:
        async for item in iter_single_agent_stream_session(deps):
            yield UnifiedSingleAgentSessionOutcome(
                final_content=item.final_content,
                chunk=item.chunk,
                awaiting_tool_approval=item.awaiting_tool_approval,
            )

    return UnifiedSingleAgentSessionSpec(iterate=_iterate)


def build_managed_single_agent_sync_session_spec(
    *,
    execution: SingleAgentSyncExecutionContext,
    callbacks: ManagedSingleAgentSessionCallbacks,
) -> ManagedSingleAgentSessionSpec:
    """Build the higher-level managed spec for a sync single-agent session."""

    return ManagedSingleAgentSessionSpec(
        session=build_unified_sync_single_agent_session_spec(
            execute_turn=execution.execute_turn,
            on_empty=execution.on_empty,
        ),
        callbacks=callbacks,
    )


def build_managed_single_agent_stream_session_spec(
    *,
    deps: SingleAgentStreamSessionDeps,
    callbacks: ManagedSingleAgentSessionCallbacks,
) -> ManagedSingleAgentSessionSpec:
    """Build the higher-level managed spec for a streaming single-agent session."""

    return ManagedSingleAgentSessionSpec(
        session=build_unified_stream_single_agent_session_spec(deps=deps),
        callbacks=callbacks,
        stream_transport=ManagedSingleAgentStreamTransport(
            serialize_payload=deps.serialize_payload,
        ),
    )


def build_single_agent_session_runtime_context(
    *,
    db: Any,
    task_run: Any,
    chatroom_id: int,
    client_turn_id: str | None,
    agent_id: int | None,
    agent_name: str,
    agent_type: str,
    user_message: str,
    save_message: Callable[..., Awaitable[Any]],
    publish_message: Callable[..., Awaitable[Any]],
    record_turn_completed: Callable[..., Any],
    message_metadata: Callable[..., dict[str, Any]],
    compact_summary: Callable[[Any], str],
    completion_summary: str,
    failure_summary: str | Callable[[Exception], str],
    extract_memories: Callable[[int, str, str, str], Awaitable[Any]],
    stream_failure_message_metadata: Callable[[str | None, dict[str, Any] | None], dict[str, Any]] | None = None,
    post_publish_success: PostPublishSuccess | None = None,
    min_response_length: int = 30,
) -> SingleAgentSessionRuntimeContext:
    """Build the shared runtime context for one managed single-agent session."""

    return SingleAgentSessionRuntimeContext(
        db=db,
        task_run=task_run,
        chatroom_id=chatroom_id,
        client_turn_id=client_turn_id,
        agent_id=agent_id,
        agent_name=agent_name,
        agent_type=agent_type,
        user_message=user_message,
        save_message=save_message,
        publish_message=publish_message,
        record_turn_completed=record_turn_completed,
        message_metadata=message_metadata,
        compact_summary=compact_summary,
        completion_summary=completion_summary,
        failure_summary=failure_summary,
        extract_memories=extract_memories,
        stream_failure_message_metadata=stream_failure_message_metadata,
        post_publish_success=post_publish_success,
        min_response_length=min_response_length,
    )


def build_single_agent_raw_runtime_inputs(
    *,
    db: Any,
    task_run: Any,
    chatroom_id: int,
    client_turn_id: str | None,
    agent_id: int | None,
    agent_name: str,
    agent_type: str,
    user_message: str,
    save_message: Callable[..., Awaitable[Any]],
    publish_message: Callable[..., Awaitable[Any]],
    record_turn_completed: Callable[..., Any],
    message_metadata: Callable[..., dict[str, Any]],
    compact_summary: Callable[[Any], str],
    completion_summary: str,
    failure_summary: str | Callable[[Exception], str],
    extract_memories: Callable[[int, str, str, str], Awaitable[Any]],
    stream_failure_message_metadata: Callable[[str | None, dict[str, Any] | None], dict[str, Any]] | None = None,
    post_publish_success: PostPublishSuccess | None = None,
    min_response_length: int = 30,
) -> SingleAgentRawRuntimeInputs:
    """Build the shared raw runtime input bundle for one single-agent turn."""

    return SingleAgentRawRuntimeInputs(
        db=db,
        task_run=task_run,
        chatroom_id=chatroom_id,
        client_turn_id=client_turn_id,
        agent_id=agent_id,
        agent_name=agent_name,
        agent_type=agent_type,
        user_message=user_message,
        save_message=save_message,
        publish_message=publish_message,
        record_turn_completed=record_turn_completed,
        message_metadata=message_metadata,
        compact_summary=compact_summary,
        completion_summary=completion_summary,
        failure_summary=failure_summary,
        extract_memories=extract_memories,
        stream_failure_message_metadata=stream_failure_message_metadata,
        post_publish_success=post_publish_success,
        min_response_length=min_response_length,
    )


def build_single_agent_session_runtime_context_from_raw_inputs(
    inputs: SingleAgentRawRuntimeInputs,
) -> SingleAgentSessionRuntimeContext:
    """Promote raw runtime inputs into the shared runtime context model."""

    return build_single_agent_session_runtime_context(
        db=inputs.db,
        task_run=inputs.task_run,
        chatroom_id=inputs.chatroom_id,
        client_turn_id=inputs.client_turn_id,
        agent_id=inputs.agent_id,
        agent_name=inputs.agent_name,
        agent_type=inputs.agent_type,
        user_message=inputs.user_message,
        save_message=inputs.save_message,
        publish_message=inputs.publish_message,
        record_turn_completed=inputs.record_turn_completed,
        message_metadata=inputs.message_metadata,
        compact_summary=inputs.compact_summary,
        completion_summary=inputs.completion_summary,
        failure_summary=inputs.failure_summary,
        extract_memories=inputs.extract_memories,
        stream_failure_message_metadata=inputs.stream_failure_message_metadata,
        post_publish_success=inputs.post_publish_success,
        min_response_length=inputs.min_response_length,
    )


def build_single_agent_raw_execution_inputs(
    *,
    mode: Literal["sync", "stream"],
    execution: SingleAgentSyncRawExecutionInputs | SingleAgentStreamRawExecutionInputs,
) -> SingleAgentRawExecutionInputs:
    """Build the shared raw execution input envelope for one single-agent turn."""

    return SingleAgentRawExecutionInputs(mode=mode, execution=execution)


def build_single_agent_sync_raw_execution_envelope(
    *,
    execute_turn: Callable[[], Awaitable[str | None]],
    on_empty: Callable[[], Awaitable[Any] | Any] | None = None,
) -> SingleAgentRawExecutionInputs:
    """Build the top-level raw execution envelope for one sync single-agent turn."""

    return build_single_agent_raw_execution_inputs(
        mode="sync",
        execution=build_single_agent_sync_raw_execution_inputs(
            execute_turn=execute_turn,
            on_empty=on_empty,
        ),
    )


def build_single_agent_stream_raw_execution_envelope(
    *,
    llm_client: Any,
    tools: list[dict[str, Any]] | None,
    turn_state: Any,
    loop_callbacks: SingleAgentStreamLoopCallbacks,
    transport: SingleAgentStreamTransportContext,
    max_turns: int,
) -> SingleAgentRawExecutionInputs:
    """Build the top-level raw execution envelope for one streaming single-agent turn."""

    return build_single_agent_raw_execution_inputs(
        mode="stream",
        execution=build_single_agent_stream_raw_execution_inputs(
            llm_client=llm_client,
            tools=tools,
            turn_state=turn_state,
            loop_callbacks=loop_callbacks,
            transport=transport,
            max_turns=max_turns,
        ),
    )


def build_single_agent_runtime_profile(
    *,
    runtime: SingleAgentSessionRuntimeContext,
    execution: SingleAgentSyncExecutionContext | SingleAgentStreamExecutionContext,
    stream_failure: SingleAgentStreamFailurePolicy | None = None,
) -> SingleAgentRuntimeProfile:
    """Build the top-level single-agent runtime profile from shared runtime and execution context."""

    if isinstance(execution, SingleAgentSyncExecutionContext):
        return SingleAgentRuntimeProfile(
            session=build_managed_single_agent_sync_session_spec(
                execution=execution,
                callbacks=build_single_agent_sync_callbacks(
                    build_single_agent_sync_callback_profile(
                        db=runtime.db,
                        task_run=runtime.task_run,
                        chatroom_id=runtime.chatroom_id,
                        client_turn_id=runtime.client_turn_id,
                        agent_id=runtime.agent_id,
                        agent_name=runtime.agent_name,
                        agent_type=runtime.agent_type,
                        user_message=runtime.user_message,
                        save_message=runtime.save_message,
                        publish_message=runtime.publish_message,
                        record_turn_completed=runtime.record_turn_completed,
                        message_metadata=runtime.message_metadata,
                        compact_summary=runtime.compact_summary,
                        completion_summary=runtime.completion_summary,
                        failure_summary=runtime.failure_summary,
                        extract_memories=runtime.extract_memories,
                        post_publish_success=runtime.post_publish_success,
                        min_response_length=runtime.min_response_length,
                    )
                ),
            )
        )

    if isinstance(execution, SingleAgentStreamExecutionContext):
        resolved_stream_failure = stream_failure or SingleAgentStreamFailurePolicy()
        return SingleAgentRuntimeProfile(
            session=build_managed_single_agent_stream_session_spec(
                deps=build_single_agent_stream_session_deps_from_execution_context(
                    execution=execution,
                    agent_name=runtime.agent_name,
                    client_turn_id=runtime.client_turn_id,
                    chatroom_id=runtime.chatroom_id,
                ),
                callbacks=build_single_agent_stream_callbacks(
                    build_single_agent_stream_callback_profile(
                        db=runtime.db,
                        task_run=runtime.task_run,
                        chatroom_id=runtime.chatroom_id,
                        client_turn_id=runtime.client_turn_id,
                        agent_id=runtime.agent_id,
                        agent_name=runtime.agent_name,
                        agent_type=runtime.agent_type,
                        user_message=runtime.user_message,
                        save_message=runtime.save_message,
                        publish_message=runtime.publish_message,
                        record_turn_completed=runtime.record_turn_completed,
                        message_metadata=runtime.message_metadata,
                        compact_summary=runtime.compact_summary,
                        completion_summary=runtime.completion_summary,
                        failure_summary=runtime.failure_summary,
                        extract_memories=runtime.extract_memories,
                        post_publish_success=runtime.post_publish_success,
                        stream_failure_message_metadata=(
                            runtime.stream_failure_message_metadata or runtime.message_metadata
                        ),
                        failure_agent_name=(
                            resolved_stream_failure.failure_agent_name
                            if resolved_stream_failure.failure_agent_name is not None
                            else runtime.agent_name
                        ),
                        failure_agent_id=(
                            resolved_stream_failure.failure_agent_id
                            if resolved_stream_failure.failure_agent_id is not None
                            else runtime.agent_id
                        ),
                        detail_builder=resolved_stream_failure.detail_builder,
                        final_message_saved=resolved_stream_failure.final_message_saved,
                        empty_response_text=resolved_stream_failure.empty_response_text,
                        min_response_length=runtime.min_response_length,
                    )
                ),
            )
        )

    raise TypeError("Unsupported single-agent execution context.")


def build_single_agent_runtime_profile_from_raw_inputs(
    *,
    runtime_inputs: SingleAgentRawRuntimeInputs,
    execution_inputs: SingleAgentRawExecutionInputs,
    stream_failure: SingleAgentStreamFailurePolicy | None = None,
) -> SingleAgentRuntimeProfile:
    """Build the top-level single-agent runtime profile directly from raw inputs."""

    execution = execution_inputs.execution
    if execution_inputs.mode == "sync":
        return build_single_agent_sync_runtime_profile(
            runtime=build_single_agent_session_runtime_context_from_raw_inputs(runtime_inputs),
            execution=build_single_agent_sync_execution_context_from_raw_inputs(execution),
        )

    if execution_inputs.mode == "stream":
        return build_single_agent_stream_runtime_profile(
            runtime=build_single_agent_session_runtime_context_from_raw_inputs(runtime_inputs),
            execution=build_single_agent_stream_execution_context_from_raw_inputs(execution),
            stream_failure=stream_failure,
        )

    raise TypeError(
        f"Unsupported single-agent raw execution inputs: {type(execution)!r}."
    )


def build_single_agent_sync_runtime_profile(
    *,
    runtime: SingleAgentSessionRuntimeContext,
    execution: SingleAgentSyncExecutionContext,
) -> SingleAgentRuntimeProfile:
    """Build the higher-level sync runtime profile from shared runtime and execution context."""

    return build_single_agent_runtime_profile(
        runtime=runtime,
        execution=execution,
    )


def build_single_agent_stream_runtime_profile(
    *,
    runtime: SingleAgentSessionRuntimeContext,
    execution: SingleAgentStreamExecutionContext,
    stream_failure: SingleAgentStreamFailurePolicy | None = None,
) -> SingleAgentRuntimeProfile:
    """Build the higher-level stream runtime profile from shared runtime and execution context."""

    return build_single_agent_runtime_profile(
        runtime=runtime,
        execution=execution,
        stream_failure=stream_failure,
    )


async def run_managed_single_agent_sync_runtime_profile(
    profile: SingleAgentRuntimeProfile,
) -> UnifiedSingleAgentSessionOutcome:
    """Run one managed sync single-agent session from the higher-level runtime profile."""

    return await run_managed_single_agent_sync_session(profile.session)


async def iter_managed_single_agent_stream_runtime_profile(
    profile: SingleAgentRuntimeProfile,
) -> AsyncIterator[UnifiedSingleAgentSessionOutcome]:
    """Run one managed stream single-agent session from the higher-level runtime profile."""

    async for outcome in iter_managed_single_agent_stream_session(profile.session):
        yield outcome


async def _consume_sync_session(spec: UnifiedSingleAgentSessionSpec) -> str | None:
    final_content = None
    async for item in spec.iterate():
        if item.chunk is not None:
            raise ValueError("Sync single-agent session emitted stream chunks unexpectedly.")
        if item.final_content is not None:
            final_content = item.final_content
    return final_content
