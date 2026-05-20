"""Shared runtime helpers for delegated collaboration task creation."""

from __future__ import annotations

from typing import Any


def _coordinator_or_default(coordinator: Any | None) -> Any:
    if coordinator is not None:
        return coordinator
    from agents.collaboration import collaboration_coordinator

    return collaboration_coordinator


def build_delegated_task_metadata(
    *,
    task_id: str,
    task_title: str,
    task_description: str,
    context: str,
    delegator: str,
    target_agent_name: str,
    parent_task_run_id: int | None = None,
    parent_client_turn_id: str | None = None,
) -> dict[str, Any]:
    """Build the normalized metadata stored alongside a delegated task."""

    metadata = {
        "task_id": task_id,
        "task_title": task_title,
        "task_description": task_description,
        "context": context,
        "delegator": delegator,
        "target_agent_name": target_agent_name,
    }
    if parent_task_run_id is not None:
        metadata["parent_task_run_id"] = parent_task_run_id
    if parent_client_turn_id:
        metadata["parent_client_turn_id"] = parent_client_turn_id
    return metadata


def create_delegated_collaboration_task(
    *,
    task_title: str,
    task_description: str,
    chatroom_id: int,
    assigned_to_agent_id: int,
    created_by_agent_id: int,
    context: str = "",
    delegator: str = "",
    parent_task_run_id: int | None = None,
    parent_client_turn_id: str | None = None,
):
    """Create one normalized delegated collaboration task."""

    from agents.collaboration import CollaborationTask, TaskStatus, uuid

    metadata = {}
    if context or delegator:
        metadata = {
            "context": context,
            "delegator": delegator,
        }
    if parent_task_run_id is not None:
        metadata["parent_task_run_id"] = parent_task_run_id
    if parent_client_turn_id:
        metadata["parent_client_turn_id"] = parent_client_turn_id

    return CollaborationTask(
        id=str(uuid.uuid4()),
        title=task_title,
        description=task_description,
        status=TaskStatus.DELEGATED,
        created_by_agent_id=created_by_agent_id,
        assigned_to_agent_id=assigned_to_agent_id,
        chatroom_id=chatroom_id,
        metadata=metadata,
    )


def register_delegated_collaboration_task(task: Any, *, coordinator: Any | None = None) -> Any:
    """Store a delegated collaboration task in the active registry."""

    active_coordinator = _coordinator_or_default(coordinator)
    active_coordinator.task_registry[task.id] = task
    return task
