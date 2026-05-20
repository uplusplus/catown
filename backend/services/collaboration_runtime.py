"""High-level collaboration runtime facade."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy.orm import Session

from services.collaboration_delegate_execution import (
    kick_off_delegated_task_execution,
    mark_delegated_task_run_interrupted,
)
from services.collaboration_delegate_runtime import (
    build_delegated_task_metadata,
    create_delegated_collaboration_task,
    register_delegated_collaboration_task,
)
from services.collaboration_dispatch_runtime import (
    ensure_collaboration_target,
    route_broadcast_message,
    route_delegated_task_request,
    route_direct_message,
)
from services.collaboration_task_runtime import (
    list_collaboration_task_payloads,
    render_collaboration_task_status_text,
    require_collaboration_task_payload,
    resolve_collaboration_task,
)
from services.run_ledger import append_task_event


def _available_collaborator_names(coordinator: Any) -> list[str]:
    return [collab.agent_name for collab in coordinator.collaborators.values()]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_collaboration_status_payload(*, coordinator: Any) -> dict[str, Any]:
    """Return a top-level collaboration status payload."""

    return {
        "active_collaborators": len(coordinator.collaborators),
        "chatrooms": len(coordinator.chatroom_agents),
        "pending_tasks": coordinator.pending_task_count(),
    }


def get_chatroom_collaboration_status_payload(*, coordinator: Any, chatroom_id: int) -> dict[str, Any]:
    """Return the collaboration status payload for one chatroom."""

    return coordinator.get_chatroom_status(chatroom_id)


async def delegate_collaboration_task(
    *,
    coordinator: Any,
    db: Session,
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
    mark_interrupted_fn: Callable[..., None] | None = None,
    parent_task_run: Any | None = None,
) -> tuple[Any | None, str]:
    """Resolve the target, register the task, route the task request, and optionally kick off execution."""

    target_agent_id, target_agent_type = ensure_collaboration_target(
        db=db,
        target_agent_name=target_agent_name,
        chatroom_id=chatroom_id,
        coordinator=coordinator,
    )
    if not target_agent_id:
        available = _available_collaborator_names(coordinator)
        return None, (
            f"[Delegate Task] Error: Agent '{target_agent_type}' not found. "
            f"Available agents: {available}"
        )

    task = create_delegated_collaboration_task(
        task_title=task_title,
        task_description=task_description,
        chatroom_id=chatroom_id,
        assigned_to_agent_id=target_agent_id,
        created_by_agent_id=created_by_agent_id,
        context=context,
        delegator=current_agent_name,
        parent_task_run_id=getattr(parent_task_run, "id", None) if parent_task_run is not None else None,
        parent_client_turn_id=getattr(parent_task_run, "client_turn_id", None) if parent_task_run is not None else None,
    )
    register_delegated_collaboration_task(task, coordinator=coordinator)
    await route_delegated_task_request(
        coordinator=coordinator,
        task=task,
        from_agent_id=created_by_agent_id,
        from_agent_name=current_agent_name,
        to_agent_id=target_agent_id,
        to_agent_name=target_agent_type,
        context=context,
    )

    client_turn_id = f"delegate-{task.id}"
    _record_delegated_task_dispatched(
        db,
        parent_task_run,
        task=task,
        child_client_turn_id=client_turn_id,
        from_agent=current_agent_name,
        to_agent=target_agent_type,
        task_title=task_title,
        task_description=task_description,
        context=context,
    )

    if start_execution:
        if not all(
            [
                store_runtime_card_fn,
                send_message_fn,
                publish_saved_chat_message_fn,
                trigger_agent_response_fn,
                create_task_fn,
            ]
        ):
            raise ValueError("Execution dependencies are required when start_execution=True")
        await kick_off_delegated_task_execution(
            task=task,
            coordinator=coordinator,
            target_agent_type=target_agent_type,
            task_title=task_title,
            task_description=task_description,
            context=context,
            current_agent_name=current_agent_name,
            task_metadata=build_delegated_task_metadata(
                task_id=task.id,
                task_title=task_title,
                task_description=task_description,
                context=context,
                delegator=current_agent_name,
                target_agent_name=target_agent_type,
                parent_task_run_id=getattr(parent_task_run, "id", None) if parent_task_run is not None else None,
                parent_client_turn_id=getattr(parent_task_run, "client_turn_id", None) if parent_task_run is not None else None,
            ),
            store_runtime_card_fn=store_runtime_card_fn,
            send_message_fn=send_message_fn,
            publish_saved_chat_message_fn=publish_saved_chat_message_fn,
            trigger_agent_response_fn=trigger_agent_response_fn,
            create_task_fn=create_task_fn,
            mark_interrupted_fn=mark_interrupted_fn or mark_delegated_task_run_interrupted,
            parent_task_run_id=getattr(parent_task_run, "id", None) if parent_task_run is not None else None,
        )

    return task, (
        f"[Delegate Task] Task '{task_title}' delegated to {target_agent_type}. "
        f"Task ID: {task.id}"
    )


def _record_delegated_task_dispatched(
    db: Session,
    parent_task_run: Any | None,
    *,
    task: Any,
    child_client_turn_id: str,
    from_agent: str,
    to_agent: str,
    task_title: str,
    task_description: str,
    context: str,
) -> None:
    """Record the durable parent-run fact for one delegated task dispatch."""

    if parent_task_run is None:
        return
    append_task_event(
        db,
        parent_task_run,
        "delegated_task_dispatched",
        agent_name=from_agent,
        summary=f"{from_agent} delegated '{task_title}' to {to_agent}.",
        payload={
            "dispatch_kind": "delegate_task",
            "occurred_at": _utc_now_iso(),
            "parent_task_run_id": getattr(parent_task_run, "id", None),
            "parent_client_turn_id": getattr(parent_task_run, "client_turn_id", None),
            "task_id": getattr(task, "id", None),
            "child_client_turn_id": child_client_turn_id,
            "from_agent": from_agent,
            "to_agent": to_agent,
            "target_agent_name": to_agent,
            "assigned_to_agent_id": getattr(task, "assigned_to_agent_id", None),
            "task_title": task_title,
            "task_description": task_description,
            "context": context,
        },
    )


async def send_collaboration_broadcast(
    *,
    coordinator: Any,
    from_agent_id: int,
    from_agent_name: str,
    chatroom_id: int,
    content: str,
) -> str:
    """Route one collaboration broadcast and return the tool-facing status line."""

    await route_broadcast_message(
        coordinator=coordinator,
        from_agent_id=from_agent_id,
        from_agent_name=from_agent_name,
        chatroom_id=chatroom_id,
        content=content,
    )
    agent_ids = coordinator.chatroom_agents.get(chatroom_id, set())
    recipient_count = len([aid for aid in agent_ids if aid != from_agent_id])
    return f"[Broadcast] Message sent to {recipient_count} other agent(s) in chatroom"


async def send_collaboration_direct_message(
    *,
    coordinator: Any,
    db: Session,
    target_agent_name: str,
    from_agent_id: int,
    from_agent_name: str,
    chatroom_id: int,
    content: str,
) -> str:
    """Resolve the target, route one direct message, and return the tool-facing status line."""

    target_agent_id, target_agent_type = ensure_collaboration_target(
        db=db,
        target_agent_name=target_agent_name,
        chatroom_id=chatroom_id,
        coordinator=coordinator,
    )
    if not target_agent_id:
        return f"[Direct Message] Error: Agent '{target_agent_type}' not found"

    await route_direct_message(
        coordinator=coordinator,
        from_agent_id=from_agent_id,
        from_agent_name=from_agent_name,
        to_agent_id=target_agent_id,
        to_agent_name=target_agent_type,
        chatroom_id=chatroom_id,
        content=content,
    )
    return f"[Direct Message] Sent to {target_agent_type}"


def get_collaboration_task_payload(task_id: str, *, coordinator: Any | None = None) -> dict[str, Any]:
    """Return one normalized delegated task payload."""

    return require_collaboration_task_payload(task_id, coordinator=coordinator)


def get_collaboration_task_status_text(task_id: str, *, coordinator: Any | None = None) -> str | None:
    """Resolve one delegated task and render its tool-facing status text."""

    task = resolve_collaboration_task(task_id, coordinator=coordinator)
    if task is None:
        return None
    return render_collaboration_task_status_text(task)


def get_collaboration_task_payloads(
    *,
    chatroom_id: int | None = None,
    coordinator: Any | None = None,
) -> list[dict[str, Any]]:
    """Return normalized delegated task payloads."""

    return list_collaboration_task_payloads(chatroom_id=chatroom_id, coordinator=coordinator)
