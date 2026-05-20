"""Unified runtime facade for collaboration state and subagent lifecycle control."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from models.database import TaskRun
from services.collaboration_dispatch_runtime import find_db_agent_by_type
from services.collaboration_membership_runtime import (
    ensure_chatroom_collaborators,
    get_chatroom_collaborator_summary,
    get_invitable_agents_summary,
    invite_agent_to_chatroom,
)
from services.collaboration_runtime import (
    delegate_collaboration_task,
    send_collaboration_broadcast,
    send_collaboration_direct_message,
    get_chatroom_collaboration_status_payload,
    get_collaboration_status_payload,
    get_collaboration_task_payload,
    get_collaboration_task_status_text,
    get_collaboration_task_payloads,
)
from services.subagent_runtime_control import (
    SubagentRuntimeControlError,
    build_task_run_subagent_projection,
    cancel_task_run_subagent_handle,
    cancel_task_run_with_subagents,
    close_task_run_subagent_handle,
    observe_task_run_subagent,
)


def _default_collaboration_coordinator() -> Any:
    from agents.collaboration import collaboration_coordinator

    return collaboration_coordinator


def _resolve_coordinator(coordinator: Any | None) -> Any:
    """Return the explicit coordinator when provided, else the process default."""

    return coordinator if coordinator is not None else _default_collaboration_coordinator()


def get_runtime_collaboration_status(*, coordinator: Any | None = None) -> dict[str, Any]:
    """Return the top-level collaboration runtime status payload."""

    return get_collaboration_status_payload(coordinator=_resolve_coordinator(coordinator))


def get_runtime_chatroom_collaboration_status(
    chatroom_id: int,
    *,
    coordinator: Any | None = None,
) -> dict[str, Any]:
    """Return the collaboration runtime status for one chatroom."""

    return get_chatroom_collaboration_status_payload(
        coordinator=_resolve_coordinator(coordinator),
        chatroom_id=chatroom_id,
    )


def get_runtime_collaboration_task(
    task_id: str,
    *,
    coordinator: Any | None = None,
) -> dict[str, Any]:
    """Return one normalized collaboration task payload."""

    return get_collaboration_task_payload(task_id, coordinator=_resolve_coordinator(coordinator))


def get_runtime_collaboration_task_status_text(
    task_id: str,
    *,
    coordinator: Any | None = None,
) -> str | None:
    """Return the tool-facing status text for one collaboration task."""

    return get_collaboration_task_status_text(task_id, coordinator=_resolve_coordinator(coordinator))


def list_runtime_collaboration_tasks(
    *,
    chatroom_id: int | None = None,
    coordinator: Any | None = None,
) -> list[dict[str, Any]]:
    """Return normalized collaboration task payloads for one room or for all rooms."""

    return get_collaboration_task_payloads(
        chatroom_id=chatroom_id,
        coordinator=_resolve_coordinator(coordinator),
    )


async def send_runtime_collaboration_broadcast(
    *,
    from_agent_id: int,
    from_agent_name: str,
    chatroom_id: int,
    content: str,
    coordinator: Any | None = None,
) -> str:
    """Broadcast one message through the default collaboration runtime."""

    return await send_collaboration_broadcast(
        coordinator=_resolve_coordinator(coordinator),
        from_agent_id=from_agent_id,
        from_agent_name=from_agent_name,
        chatroom_id=chatroom_id,
        content=content,
    )


async def send_runtime_collaboration_direct_message(
    db: Any,
    *,
    target_agent_name: str,
    from_agent_id: int,
    from_agent_name: str,
    chatroom_id: int,
    content: str,
    coordinator: Any | None = None,
) -> str:
    """Send one direct message through the default collaboration runtime."""

    return await send_collaboration_direct_message(
        coordinator=_resolve_coordinator(coordinator),
        db=db,
        target_agent_name=target_agent_name,
        from_agent_id=from_agent_id,
        from_agent_name=from_agent_name,
        chatroom_id=chatroom_id,
        content=content,
    )


def get_runtime_chatroom_collaborators(
    db: Any,
    *,
    chatroom_id: int,
    coordinator: Any | None = None,
) -> dict[str, Any]:
    """Return one chatroom's collaborator summary from the default runtime."""

    return get_chatroom_collaborator_summary(
        db=db,
        chatroom_id=chatroom_id,
        coordinator=_resolve_coordinator(coordinator),
    )


def get_runtime_invitable_agents(db: Any, *, chatroom_id: int) -> dict[str, Any]:
    """Return agents that can be invited into one chatroom."""

    return get_invitable_agents_summary(db=db, chatroom_id=chatroom_id)


def invite_runtime_agent_to_chatroom(
    db: Any,
    *,
    chatroom_id: int,
    agent_name: str,
    coordinator: Any | None = None,
) -> dict[str, Any]:
    """Invite one agent into a chatroom using the default runtime coordinator."""

    return invite_agent_to_chatroom(
        db=db,
        chatroom_id=chatroom_id,
        agent_name=agent_name,
        coordinator=_resolve_coordinator(coordinator),
    )


def ensure_runtime_chatroom_collaborators(
    *,
    agents: list[Any],
    chatroom_id: int,
    agent_name_resolver,
    coordinator: Any | None = None,
) -> list[dict[str, Any]]:
    """Register missing collaborators in one chatroom using the runtime coordinator."""

    return ensure_chatroom_collaborators(
        agents=agents,
        chatroom_id=chatroom_id,
        coordinator=_resolve_coordinator(coordinator),
        agent_name_resolver=agent_name_resolver,
    )


def resolve_runtime_query_target(
    db: Any,
    *,
    target_agent_name: str,
    chatroom_id: int = 0,
) -> dict[str, Any]:
    """Resolve one query target plus its room availability for synchronous agent queries."""

    from agents.identity import normalize_agent_type
    from models.database import AgentAssignment, Chatroom

    target_agent_type = normalize_agent_type(target_agent_name)
    target_db_agent = find_db_agent_by_type(db, target_agent_type)

    chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first() if chatroom_id else None
    room_agent_names: list[str] = []
    is_assigned = False

    if chatroom and chatroom.project_id:
        assignments = db.query(AgentAssignment).filter(
            AgentAssignment.project_id == chatroom.project_id
        ).all()
        assigned_ids = {assignment.agent_id for assignment in assignments}
        if assigned_ids:
            from models.database import Agent as DBAgent

            room_agents = (
                db.query(DBAgent)
                .filter(DBAgent.id.in_(assigned_ids), DBAgent.is_active == True)
                .all()
            )
            room_agent_names = [agent.agent_type or agent.name for agent in room_agents]
            if target_db_agent is not None:
                is_assigned = target_db_agent.id in assigned_ids

    return {
        "target_agent_type": target_agent_type,
        "target_db_agent": target_db_agent,
        "chatroom": chatroom,
        "room_agent_names": room_agent_names,
        "is_assigned": is_assigned,
    }


async def delegate_runtime_collaboration_task(
    db: Any,
    *,
    target_agent_name: str,
    task_title: str,
    task_description: str,
    chatroom_id: int,
    created_by_agent_id: int,
    current_agent_name: str,
    context: str = "",
    start_execution: bool = False,
    store_runtime_card_fn: Callable[[int, dict[str, Any]], Awaitable[Any]] | None = None,
    send_message_fn: Callable[..., Awaitable[Any]] | None = None,
    publish_saved_chat_message_fn: Callable[..., Awaitable[Any]] | None = None,
    trigger_agent_response_fn: Callable[..., Awaitable[Any]] | None = None,
    create_task_fn: Callable[[Awaitable[Any]], Any] | None = None,
    coordinator: Any | None = None,
    parent_task_run: TaskRun | None = None,
) -> tuple[Any | None, str]:
    """Create and register one delegated collaboration task using the default runtime coordinator."""

    return await delegate_collaboration_task(
        coordinator=_resolve_coordinator(coordinator),
        db=db,
        target_agent_name=target_agent_name,
        task_title=task_title,
        task_description=task_description,
        chatroom_id=chatroom_id,
        created_by_agent_id=created_by_agent_id,
        current_agent_name=current_agent_name,
        context=context,
        start_execution=start_execution,
        store_runtime_card_fn=store_runtime_card_fn,
        send_message_fn=send_message_fn,
        publish_saved_chat_message_fn=publish_saved_chat_message_fn,
        trigger_agent_response_fn=trigger_agent_response_fn,
        create_task_fn=create_task_fn,
        parent_task_run=parent_task_run,
    )


def require_task_run(db: Any, task_run_id: int) -> Any:
    """Load one task run or raise a route-friendly lifecycle error."""

    task_run = db.query(TaskRun).filter(TaskRun.id == task_run_id).first()
    if task_run is None:
        raise SubagentRuntimeControlError(404, "Task run not found")
    return task_run


def list_runtime_task_run_subagents(db: Any, task_run_id: int) -> dict[str, Any]:
    """Return the subagent projection for one task run."""

    return build_task_run_subagent_projection(require_task_run(db, task_run_id))


def observe_runtime_task_run_subagent(
    db: Any,
    task_run_id: int,
    *,
    step_id: str,
    since_event_index: int | None = None,
) -> dict[str, Any]:
    """Observe one subagent handle from the current task run state."""

    task_run = require_task_run(db, task_run_id)
    return observe_task_run_subagent(
        task_run,
        step_id,
        since_event_index=since_event_index,
    )


def cancel_runtime_task_run_subagent(
    db: Any,
    task_run_id: int,
    *,
    step_id: str,
    cancelled_by: str,
    note: str,
) -> dict[str, Any]:
    """Cancel one projected subagent handle."""

    task_run = require_task_run(db, task_run_id)
    return cancel_task_run_subagent_handle(
        db,
        task_run,
        step_id=step_id,
        cancelled_by=cancelled_by,
        note=note,
    )


def close_runtime_task_run_subagent(
    db: Any,
    task_run_id: int,
    *,
    step_id: str,
    closed_by: str,
    note: str,
) -> dict[str, Any]:
    """Close one terminal subagent handle."""

    task_run = require_task_run(db, task_run_id)
    return close_task_run_subagent_handle(
        db,
        task_run,
        step_id=step_id,
        closed_by=closed_by,
        note=note,
    )


def cancel_runtime_task_run(
    db: Any,
    task_run_id: int,
    *,
    cancelled_by: str,
    note: str,
) -> dict[str, Any]:
    """Cancel one running task run and terminalize active subagent states."""

    task_run = require_task_run(db, task_run_id)
    return cancel_task_run_with_subagents(
        db,
        task_run,
        cancelled_by=cancelled_by,
        note=note,
    )
