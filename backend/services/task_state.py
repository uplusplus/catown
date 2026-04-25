# -*- coding: utf-8 -*-
"""Task-state helpers for chat-era context orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.context_builder import (
    ContextFragment,
    ContextScope,
    ContextVisibility,
)


@dataclass(frozen=True)
class TaskState:
    """Structured task memory for interactive chat/query turns."""

    current_request: str = ""
    current_goal: str = ""
    blockers: list[str] = field(default_factory=list)
    working_summary: str = ""
    validation_checks: list[str] = field(default_factory=list)


def build_task_state(
    *,
    project: Any = None,
    user_message: str = "",
    current_request: str | None = None,
) -> TaskState:
    normalized_request = _clean_text(current_request if current_request is not None else user_message)
    current_goal = _clean_text(getattr(project, "current_focus", None))
    blocker = _clean_text(getattr(project, "blocking_reason", None))
    working_summary = _clean_text(getattr(project, "latest_summary", None))

    validation_checks: list[str] = []
    if normalized_request:
        validation_checks.append("Make sure the response directly advances the current request.")
    if blocker:
        validation_checks.append("Do not claim the blocker is resolved unless this turn actually addresses it.")

    blockers = [blocker] if blocker else []
    return TaskState(
        current_request=normalized_request,
        current_goal=current_goal,
        blockers=blockers,
        working_summary=working_summary,
        validation_checks=validation_checks,
    )


def build_task_state_fragments(task_state: TaskState | None) -> list[ContextFragment]:
    if task_state is None:
        return []

    fragments: list[ContextFragment] = []
    overview_lines: list[str] = []
    if task_state.current_request:
        overview_lines.append(f"- Current request: {_trim(task_state.current_request, limit=700)}")
    if task_state.current_goal:
        overview_lines.append(f"- Active goal: {_trim(task_state.current_goal, limit=500)}")
    if task_state.working_summary:
        overview_lines.append(f"- Working summary: {_trim(task_state.working_summary, limit=700)}")
    if task_state.blockers:
        overview_lines.extend(
            f"- Current blocker: {_trim(item, limit=320)}" for item in task_state.blockers if item
        )
    if overview_lines:
        fragments.append(
            ContextFragment(
                role="user",
                content="## Active Task State\n" + "\n".join(overview_lines),
                scope=ContextScope.TURN if task_state.current_request else ContextScope.RUN,
                visibility=ContextVisibility.AGENT,
                source="task_state",
                priority=16,
            )
        )

    if task_state.validation_checks:
        fragments.append(
            ContextFragment(
                role="user",
                content="## Validation Checklist\n" + "\n".join(
                    f"- {_trim(item, limit=260)}" for item in task_state.validation_checks if item
                ),
                scope=ContextScope.TURN,
                visibility=ContextVisibility.AGENT,
                source="task_validation",
                priority=18,
            )
        )

    return fragments


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _trim(value: Any, *, limit: int) -> str:
    text = _clean_text(value)
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."
