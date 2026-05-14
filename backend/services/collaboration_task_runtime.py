"""Shared runtime helpers for delegated collaboration task state."""

from __future__ import annotations

from typing import Any


class CollaborationTaskRuntimeError(Exception):
    """Service-layer error for invalid collaboration task runtime operations."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _coordinator_or_default(coordinator: Any | None) -> Any:
    if coordinator is not None:
        return coordinator
    from agents.collaboration import collaboration_coordinator

    return collaboration_coordinator


def resolve_collaboration_task(
    task_id: str,
    *,
    coordinator: Any | None = None,
    register: bool = True,
) -> Any | None:
    """Resolve one delegated task from registry or persisted runtime state."""

    from agents.collaboration import (
        normalize_delegated_task_id,
        rebuild_collaboration_task_from_runtime,
        refresh_collaboration_task_from_runtime,
    )

    normalized_task_id = normalize_delegated_task_id(task_id)
    active_coordinator = _coordinator_or_default(coordinator)

    task = active_coordinator.get_task_status(normalized_task_id)
    if task is None:
        task = rebuild_collaboration_task_from_runtime(normalized_task_id)

    task = refresh_collaboration_task_from_runtime(task) or task
    if task is not None and register:
        active_coordinator.task_registry[task.id] = task
    return task


def require_collaboration_task(task_id: str, *, coordinator: Any | None = None) -> Any:
    """Resolve one delegated task or raise a 404-like service error."""

    task = resolve_collaboration_task(task_id, coordinator=coordinator)
    if task is None:
        raise CollaborationTaskRuntimeError(404, "Task not found")
    return task


def build_collaboration_task_payload(task: Any | None) -> dict[str, Any]:
    """Convert one delegated task into the normalized API payload."""

    from agents.collaboration import collaboration_task_response_payload

    return collaboration_task_response_payload(task)


def require_collaboration_task_payload(
    task_id: str,
    *,
    coordinator: Any | None = None,
) -> dict[str, Any]:
    """Resolve one delegated task and return its normalized payload."""

    return build_collaboration_task_payload(
        require_collaboration_task(task_id, coordinator=coordinator)
    )


def list_collaboration_task_payloads(
    *,
    chatroom_id: int | None = None,
    coordinator: Any | None = None,
) -> list[dict[str, Any]]:
    """Return normalized payloads for all tracked delegated tasks."""

    from agents.collaboration import refresh_collaboration_task_from_runtime

    active_coordinator = _coordinator_or_default(coordinator)
    payloads: list[dict[str, Any]] = []

    for task in list(active_coordinator.task_registry.values()):
        refreshed = refresh_collaboration_task_from_runtime(task) or task
        active_coordinator.task_registry[refreshed.id] = refreshed
        if chatroom_id is not None and refreshed.chatroom_id != chatroom_id:
            continue
        payloads.append(build_collaboration_task_payload(refreshed))

    return payloads


def render_collaboration_task_status_text(task: Any | None) -> str:
    """Render the delegated task payload into the tool-facing text format."""

    payload = build_collaboration_task_payload(task)
    if not payload:
        return ""

    status = str(payload.get("status") or "")
    status_emoji = {
        "pending": "[pending]",
        "in_progress": "[running]",
        "stalled": "[stalled]",
        "completed": "[done]",
        "failed": "[failed]",
        "delegated": "[delegated]",
    }.get(status, "[unknown]")

    lines = [
        f"[Check Task] Task: {payload.get('title') or '(untitled)'}",
        f"Status: {status_emoji} {status}",
        f"Assigned to: Agent #{payload.get('assigned_to')}",
    ]

    result = payload.get("result")
    if result:
        result_text = str(result)
        if len(result_text) > 500:
            result_text = f"{result_text[:500]}..."
        lines.append(f"Result: {result_text}")

    details = payload.get("result_details") if isinstance(payload.get("result_details"), dict) else {}
    if details.get("command"):
        lines.append(f"Command: {details['command']}")
    if details.get("phase"):
        lines.append(f"Phase: {details['phase']}")
    if details.get("duration_ms") is not None:
        lines.append(f"Duration: {details['duration_ms']}ms")
    if details.get("tail_output"):
        tail = str(details["tail_output"])
        if len(tail) > 800:
            tail = f"{tail[:800]}..."
        lines.append(f"Tail output: {tail}")
    if details.get("blocked") is True:
        lines.append(
            f"Blocked: {details.get('blocked_reason') or 'approval required'}"
        )
    if details.get("passed_count") is not None or details.get("failed_count") is not None:
        passed = details.get("passed_count")
        failed = details.get("failed_count")
        lines.append(
            f"Test summary: {passed if passed is not None else '?'} passed / "
            f"{failed if failed is not None else '?'} failed"
        )
    if details.get("next_step"):
        lines.append(f"Next step: {details['next_step']}")

    completed_at = payload.get("completed_at")
    if completed_at:
        lines.append(f"Completed at: {completed_at}")

    return "\n".join(lines)
