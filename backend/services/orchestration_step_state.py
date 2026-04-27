# -*- coding: utf-8 -*-
"""Shared in-memory step-state helpers for orchestration executor loops."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class OrchestrationStepOutputState:
    """Mutable output accumulator shared by sync, stream, and recovery orchestration loops."""

    completed_turns: List[Dict[str, str]] = field(default_factory=list)
    results: List[Dict[str, str]] = field(default_factory=list)
    last_blocking_result: str = ""


def record_orchestration_step_output(
    state: OrchestrationStepOutputState,
    *,
    agent_name: str,
    content: str,
    dispatch_kind: str,
    include_result: bool = True,
) -> OrchestrationStepOutputState:
    """Record a completed step output and update the latest blocking result."""

    normalized_content = str(content or "")
    if not normalized_content:
        return state

    entry = {"agent": str(agent_name or "agent"), "content": normalized_content}
    if include_result:
        state.results.append(entry)
    state.completed_turns.append(entry)
    if str(dispatch_kind or "").strip().lower() == "blocking":
        state.last_blocking_result = normalized_content
    return state
