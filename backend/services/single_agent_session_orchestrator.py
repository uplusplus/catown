# -*- coding: utf-8 -*-
"""Higher-level orchestration for single-agent sync/stream session drivers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

from services.single_agent_session_callbacks import (
    SingleAgentStreamCallbackProfile,
    SingleAgentSyncCallbackProfile,
    build_single_agent_stream_callbacks,
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
    SingleAgentSessionRunnerDeps,
    SingleAgentSessionRunnerResult,
    run_single_agent_session,
)
from services.single_agent_stream_session import (
    SingleAgentStreamSessionDeps,
    iter_single_agent_stream_session,
)
from services.stream_transport import render_sse_payload


@dataclass(frozen=True)
class ManagedSingleAgentSyncSessionProfile:
    execute_turn: Callable[[], Awaitable[str | None]]
    callback_profile: SingleAgentSyncCallbackProfile
    on_empty: Callable[[], Awaitable[Any] | Any] | None = None


@dataclass(frozen=True)
class ManagedSingleAgentStreamSessionProfile:
    deps: SingleAgentStreamSessionDeps
    callback_profile: SingleAgentStreamCallbackProfile


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
        SingleAgentSessionRunnerDeps(
            execute_turn=lambda: _consume_sync_session(spec.session),
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
    execute_turn: Callable[[], Awaitable[str | None]],
    callbacks: ManagedSingleAgentSessionCallbacks,
    on_empty: Callable[[], Awaitable[Any] | Any] | None = None,
) -> ManagedSingleAgentSessionSpec:
    """Build the higher-level managed spec for a sync single-agent session."""

    return ManagedSingleAgentSessionSpec(
        session=build_unified_sync_single_agent_session_spec(
            execute_turn=execute_turn,
            on_empty=on_empty,
        ),
        callbacks=callbacks,
    )


def build_managed_single_agent_sync_session_profile(
    *,
    execute_turn: Callable[[], Awaitable[str | None]],
    callback_profile: SingleAgentSyncCallbackProfile,
    on_empty: Callable[[], Awaitable[Any] | Any] | None = None,
) -> ManagedSingleAgentSyncSessionProfile:
    """Build the higher-level sync session profile from runtime components."""

    return ManagedSingleAgentSyncSessionProfile(
        execute_turn=execute_turn,
        callback_profile=callback_profile,
        on_empty=on_empty,
    )


def build_managed_single_agent_sync_session_spec_from_callback_profile(
    *,
    profile: ManagedSingleAgentSyncSessionProfile,
) -> ManagedSingleAgentSessionSpec:
    """Build the managed sync session spec from a higher-level callback profile."""

    return build_managed_single_agent_sync_session_spec(
        execute_turn=profile.execute_turn,
        callbacks=build_single_agent_sync_callbacks(profile.callback_profile),
        on_empty=profile.on_empty,
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
