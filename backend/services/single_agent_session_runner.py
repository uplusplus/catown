# -*- coding: utf-8 -*-
"""Shared non-stream single-agent session runner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class SingleAgentSessionRunnerDeps:
    execute_turn: Callable[[], Awaitable[str | None]]
    finalize_success: Callable[[str], Awaitable[Any]]
    finalize_failure: Callable[[Exception], Awaitable[Any] | Any] | None = None
    on_empty: Callable[[], Awaitable[Any] | Any] | None = None


@dataclass(frozen=True)
class SingleAgentSyncExecutionContext:
    execute_turn: Callable[[], Awaitable[str | None]]
    on_empty: Callable[[], Awaitable[Any] | Any] | None = None


@dataclass(frozen=True)
class SingleAgentSyncRawExecutionInputs:
    execute_turn: Callable[[], Awaitable[str | None]]
    on_empty: Callable[[], Awaitable[Any] | Any] | None = None


@dataclass(frozen=True)
class SingleAgentSessionRunnerResult:
    final_content: str | None = None


def build_single_agent_sync_raw_execution_inputs(
    *,
    execute_turn: Callable[[], Awaitable[str | None]],
    on_empty: Callable[[], Awaitable[Any] | Any] | None = None,
) -> SingleAgentSyncRawExecutionInputs:
    """Build the raw sync-side execution input bundle for one single-agent turn."""

    return SingleAgentSyncRawExecutionInputs(
        execute_turn=execute_turn,
        on_empty=on_empty,
    )


def build_single_agent_sync_execution_context(
    *,
    execute_turn: Callable[[], Awaitable[str | None]],
    on_empty: Callable[[], Awaitable[Any] | Any] | None = None,
) -> SingleAgentSyncExecutionContext:
    """Build the sync-side execution context for one single-agent turn."""

    return SingleAgentSyncExecutionContext(
        execute_turn=execute_turn,
        on_empty=on_empty,
    )


def build_single_agent_sync_execution_context_from_raw_inputs(
    inputs: SingleAgentSyncRawExecutionInputs,
) -> SingleAgentSyncExecutionContext:
    """Promote raw sync execution inputs into the sync execution context model."""

    return build_single_agent_sync_execution_context(
        execute_turn=inputs.execute_turn,
        on_empty=inputs.on_empty,
    )


def build_single_agent_session_runner_deps_from_execution_context(
    *,
    execution: SingleAgentSyncExecutionContext,
    finalize_success: Callable[[str], Awaitable[Any]],
    finalize_failure: Callable[[Exception], Awaitable[Any] | Any] | None = None,
) -> SingleAgentSessionRunnerDeps:
    """Build runner deps from the higher-level sync execution context."""

    return SingleAgentSessionRunnerDeps(
        execute_turn=execution.execute_turn,
        finalize_success=finalize_success,
        finalize_failure=finalize_failure,
        on_empty=execution.on_empty,
    )


async def run_single_agent_session(
    deps: SingleAgentSessionRunnerDeps,
) -> SingleAgentSessionRunnerResult:
    """Execute one non-stream single-agent session and finalize it."""

    try:
        final_content = await deps.execute_turn()
    except Exception as exc:
        if deps.finalize_failure is not None:
            await _maybe_await(deps.finalize_failure(exc))
            return SingleAgentSessionRunnerResult(final_content=None)
        raise

    if not final_content:
        if deps.on_empty is not None:
            await _maybe_await(deps.on_empty())
        return SingleAgentSessionRunnerResult(final_content=None)

    await deps.finalize_success(final_content)
    return SingleAgentSessionRunnerResult(final_content=final_content)


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value
