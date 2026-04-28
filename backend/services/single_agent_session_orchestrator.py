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
