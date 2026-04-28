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
class SingleAgentSessionRunnerResult:
    final_content: str | None = None


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
