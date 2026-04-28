# -*- coding: utf-8 -*-
"""Higher-level orchestration for single-agent sync/stream session drivers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

from services.single_agent_session_runner import (
    SingleAgentSessionRunnerDeps,
    SingleAgentSessionRunnerResult,
    run_single_agent_session,
)
from services.single_agent_stream_session import (
    SingleAgentStreamSessionDeps,
    SingleAgentStreamSessionResult,
    iter_single_agent_stream_session,
)
from services.stream_transport import render_sse_payload


@dataclass(frozen=True)
class SyncSingleAgentSessionSpec:
    deps: SingleAgentSessionRunnerDeps


@dataclass(frozen=True)
class StreamSingleAgentSessionSpec:
    deps: SingleAgentStreamSessionDeps


@dataclass(frozen=True)
class UnifiedSingleAgentSessionOutcome:
    final_content: str | None = None
    chunk: str | None = None
    payload: dict[str, Any] | None = None
    error_text: str | None = None


@dataclass(frozen=True)
class UnifiedSingleAgentSessionSpec:
    mode: str
    execute_turn: Callable[[], Awaitable[str | None]] | None = None
    stream_deps: SingleAgentStreamSessionDeps | None = None
    on_empty: Callable[[], Awaitable[Any] | Any] | None = None


@dataclass(frozen=True)
class ManagedSingleAgentSessionCallbacks:
    finalize_success: Callable[[str], Awaitable[Any]]
    finalize_failure: Callable[[Exception], Awaitable[Any]]
    serialize_payload: Callable[[Any], str] | None = None


@dataclass(frozen=True)
class ManagedSingleAgentSessionSpec:
    session: UnifiedSingleAgentSessionSpec
    callbacks: ManagedSingleAgentSessionCallbacks


async def run_sync_single_agent_session(
    spec: SyncSingleAgentSessionSpec,
) -> SingleAgentSessionRunnerResult:
    """Run one sync single-agent session using the shared orchestrator surface."""

    return await run_single_agent_session(spec.deps)


async def iter_stream_single_agent_session(
    spec: StreamSingleAgentSessionSpec,
) -> AsyncIterator[SingleAgentStreamSessionResult]:
    """Run one stream single-agent session using the shared orchestrator surface."""

    async for item in iter_single_agent_stream_session(spec.deps):
        yield item


async def run_unified_single_agent_sync_session(
    spec: UnifiedSingleAgentSessionSpec,
) -> UnifiedSingleAgentSessionOutcome:
    """Run one sync single-agent session through the higher-level unified facade."""
    if spec.mode != "sync" or spec.execute_turn is None:
        raise ValueError("Unified sync session requires mode='sync' and execute_turn.")
    final_content = await spec.execute_turn()
    if not final_content and spec.on_empty is not None:
        await _maybe_await(spec.on_empty())
    return UnifiedSingleAgentSessionOutcome(final_content=final_content or None)


async def iter_unified_single_agent_stream_session(
    spec: UnifiedSingleAgentSessionSpec,
) -> AsyncIterator[UnifiedSingleAgentSessionOutcome]:
    """Run one stream single-agent session through the higher-level unified facade."""
    if spec.mode != "stream" or spec.stream_deps is None:
        raise ValueError("Unified stream session requires mode='stream' and stream_deps.")
    async for item in iter_single_agent_stream_session(spec.stream_deps):
        yield UnifiedSingleAgentSessionOutcome(
            final_content=item.final_content,
            chunk=item.chunk,
        )


async def run_managed_single_agent_sync_session(
    spec: ManagedSingleAgentSessionSpec,
) -> UnifiedSingleAgentSessionOutcome:
    """Run one sync single-agent session through the managed stack surface."""
    result = await run_single_agent_session(
        SingleAgentSessionRunnerDeps(
            execute_turn=spec.session.execute_turn,
            finalize_success=spec.callbacks.finalize_success,
            finalize_failure=spec.callbacks.finalize_failure,
            on_empty=spec.session.on_empty,
        )
    )
    return UnifiedSingleAgentSessionOutcome(final_content=result.final_content)


async def iter_managed_single_agent_stream_session(
    spec: ManagedSingleAgentSessionSpec,
) -> AsyncIterator[UnifiedSingleAgentSessionOutcome]:
    """Run one stream single-agent session through the managed stack surface."""

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
            chunk=render_sse_payload(finalized.payload, serialize_payload=spec.callbacks.serialize_payload or str),
            payload=dict(finalized.payload or {}),
            error_text=getattr(finalized, "error_text", None),
        )
        return

    finalized = await spec.callbacks.finalize_success(final_content)
    yield UnifiedSingleAgentSessionOutcome(
        final_content=final_content,
        chunk=render_sse_payload(finalized.payload, serialize_payload=spec.callbacks.serialize_payload or str),
        payload=dict(finalized.payload or {}),
    )


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value
