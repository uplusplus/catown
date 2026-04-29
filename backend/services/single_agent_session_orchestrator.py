# -*- coding: utf-8 -*-
"""Higher-level orchestration for single-agent sync/stream session drivers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

from services.single_agent_session_callbacks import (
    SingleAgentStreamCallbackProfile,
    SingleAgentSyncCallbackProfile,
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
    build_single_agent_session_runner_deps_from_execution_context,
    build_single_agent_sync_execution_context,
    SingleAgentSyncExecutionContext,
    SingleAgentSessionRunnerDeps,
    SingleAgentSessionRunnerResult,
    run_single_agent_session,
)
from services.single_agent_stream_session import (
    SingleAgentStreamExecutionContext,
    build_single_agent_stream_session_deps_from_execution_context,
    SingleAgentStreamSessionDeps,
    build_single_agent_stream_session_deps,
    iter_single_agent_stream_session,
)
from services.stream_transport import render_sse_payload


@dataclass(frozen=True)
class ManagedSingleAgentSyncSessionProfile:
    execution: SingleAgentSyncExecutionContext
    callback_profile: SingleAgentSyncCallbackProfile


@dataclass(frozen=True)
class ManagedSingleAgentStreamSessionProfile:
    deps: SingleAgentStreamSessionDeps
    callback_profile: SingleAgentStreamCallbackProfile


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
    min_response_length: int = 30


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


def build_managed_single_agent_sync_session_profile(
    *,
    execution: SingleAgentSyncExecutionContext,
    callback_profile: SingleAgentSyncCallbackProfile,
) -> ManagedSingleAgentSyncSessionProfile:
    """Build the higher-level sync session profile from runtime components."""

    return ManagedSingleAgentSyncSessionProfile(
        execution=execution,
        callback_profile=callback_profile,
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
        min_response_length=min_response_length,
    )


def build_managed_single_agent_sync_session_profile_from_runtime(
    *,
    runtime: SingleAgentSessionRuntimeContext,
    execution: SingleAgentSyncExecutionContext,
) -> ManagedSingleAgentSyncSessionProfile:
    """Build the higher-level sync session profile directly from runtime inputs."""

    return build_managed_single_agent_sync_session_profile(
        execution=execution,
        callback_profile=build_single_agent_sync_callback_profile(
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
            min_response_length=runtime.min_response_length,
        ),
    )


def build_managed_single_agent_sync_session_spec_from_callback_profile(
    *,
    profile: ManagedSingleAgentSyncSessionProfile,
) -> ManagedSingleAgentSessionSpec:
    """Build the managed sync session spec from a higher-level callback profile."""

    return build_managed_single_agent_sync_session_spec(
        execution=profile.execution,
        callbacks=build_single_agent_sync_callbacks(profile.callback_profile),
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


def build_managed_single_agent_stream_session_profile(
    *,
    deps: SingleAgentStreamSessionDeps,
    callback_profile: SingleAgentStreamCallbackProfile,
) -> ManagedSingleAgentStreamSessionProfile:
    """Build the higher-level stream session profile from runtime components."""

    return ManagedSingleAgentStreamSessionProfile(
        deps=deps,
        callback_profile=callback_profile,
    )


def build_managed_single_agent_stream_session_profile_from_runtime(
    *,
    runtime: SingleAgentSessionRuntimeContext,
    execution: SingleAgentStreamExecutionContext,
    failure_agent_name: str | None = None,
    failure_agent_id: int | None = None,
    detail_builder: Callable[[], str] | None = None,
    final_message_saved: bool = False,
    empty_response_text: str = "(Agent returned empty response)",
) -> ManagedSingleAgentStreamSessionProfile:
    """Build the higher-level stream session profile directly from runtime inputs."""

    return build_managed_single_agent_stream_session_profile(
        deps=build_single_agent_stream_session_deps_from_execution_context(
            execution=execution,
            agent_name=runtime.agent_name,
            client_turn_id=runtime.client_turn_id,
            chatroom_id=runtime.chatroom_id,
        ),
        callback_profile=build_single_agent_stream_callback_profile(
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
            stream_failure_message_metadata=(
                runtime.stream_failure_message_metadata or runtime.message_metadata
            ),
            failure_agent_name=(
                failure_agent_name if failure_agent_name is not None else runtime.agent_name
            ),
            failure_agent_id=(
                failure_agent_id if failure_agent_id is not None else runtime.agent_id
            ),
            detail_builder=detail_builder,
            final_message_saved=final_message_saved,
            empty_response_text=empty_response_text,
            min_response_length=runtime.min_response_length,
        ),
    )


def build_managed_single_agent_stream_session_spec_from_callback_profile(
    *,
    profile: ManagedSingleAgentStreamSessionProfile,
) -> ManagedSingleAgentSessionSpec:
    """Build the managed stream session spec from a higher-level callback profile."""

    return build_managed_single_agent_stream_session_spec(
        deps=profile.deps,
        callbacks=build_single_agent_stream_callbacks(profile.callback_profile),
    )


async def run_managed_single_agent_sync_session_profile(
    profile: ManagedSingleAgentSyncSessionProfile,
) -> UnifiedSingleAgentSessionOutcome:
    """Run one managed sync single-agent session from the higher-level profile."""

    return await run_managed_single_agent_sync_session(
        build_managed_single_agent_sync_session_spec_from_callback_profile(
            profile=profile,
        )
    )


async def iter_managed_single_agent_stream_session_profile(
    profile: ManagedSingleAgentStreamSessionProfile,
) -> AsyncIterator[UnifiedSingleAgentSessionOutcome]:
    """Run one managed stream single-agent session from the higher-level profile."""

    async for outcome in iter_managed_single_agent_stream_session(
        build_managed_single_agent_stream_session_spec_from_callback_profile(
            profile=profile,
        )
    ):
        yield outcome


async def _consume_sync_session(spec: UnifiedSingleAgentSessionSpec) -> str | None:
    final_content = None
    async for item in spec.iterate():
        if item.chunk is not None:
            raise ValueError("Sync single-agent session emitted stream chunks unexpectedly.")
        if item.final_content is not None:
            final_content = item.final_content
    return final_content
