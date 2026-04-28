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


@dataclass(frozen=True)
class UnifiedSingleAgentSyncSessionSpec:
    execute_turn: Callable[[], Awaitable[str | None]]
    finalize_success: Callable[[str], Awaitable[Any]]
    finalize_failure: Callable[[Exception], Awaitable[Any] | Any] | None = None
    on_empty: Callable[[], Awaitable[Any] | Any] | None = None


@dataclass(frozen=True)
class UnifiedSingleAgentStreamSessionSpec:
    deps: SingleAgentStreamSessionDeps


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
    spec: UnifiedSingleAgentSyncSessionSpec,
) -> SingleAgentSessionRunnerResult:
    """Run one sync single-agent session through the higher-level unified facade."""

    return await run_single_agent_session(
        SingleAgentSessionRunnerDeps(
            execute_turn=spec.execute_turn,
            finalize_success=spec.finalize_success,
            finalize_failure=spec.finalize_failure,
            on_empty=spec.on_empty,
        )
    )


async def iter_unified_single_agent_stream_session(
    spec: UnifiedSingleAgentStreamSessionSpec,
) -> AsyncIterator[SingleAgentStreamSessionResult]:
    """Run one stream single-agent session through the higher-level unified facade."""

    async for item in iter_single_agent_stream_session(spec.deps):
        yield item
