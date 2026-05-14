"""Shared runtime helpers for collaboration target resolution and task dispatch."""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from agents.identity import default_agent_name, legacy_default_agent_names, normalize_agent_type


def _coordinator_or_default(coordinator: Any | None) -> Any:
    if coordinator is not None:
        return coordinator
    from agents.collaboration import collaboration_coordinator

    return collaboration_coordinator


def find_db_agent_by_type(db: Session, agent_type: str | None) -> Agent | None:
    """Resolve one DB agent record from an agent type or legacy/default names."""

    from models.database import Agent

    normalized = normalize_agent_type(agent_type)
    candidate_names = {normalized, default_agent_name(normalized)}
    candidate_names.update({value.title() for value in legacy_default_agent_names(normalized)})
    candidate_names.update(legacy_default_agent_names(normalized))
    return (
        db.query(Agent)
        .filter(
            or_(
                Agent.agent_type == normalized,
                Agent.name.in_(sorted(candidate_names)),
            )
        )
        .order_by(Agent.id.asc())
        .first()
    )


def ensure_collaboration_target(
    *,
    db: Session,
    target_agent_name: str,
    chatroom_id: int,
    coordinator: Any | None = None,
) -> tuple[int | None, str]:
    """Find or register the collaboration target for one chatroom."""

    from agents.collaboration import AgentCollaborator

    active_coordinator = _coordinator_or_default(coordinator)
    target_agent_type = normalize_agent_type(target_agent_name)

    for agent_id, collaborator in active_coordinator.collaborators.items():
        if collaborator.agent_name == target_agent_type:
            return agent_id, target_agent_type

    db_agent = find_db_agent_by_type(db, target_agent_type)
    if db_agent is None:
        return None, target_agent_type

    active_coordinator.register_collaborator(
        AgentCollaborator(
            agent_id=db_agent.id,
            agent_name=db_agent.agent_type or db_agent.name,
            chatroom_id=chatroom_id,
        )
    )
    return db_agent.id, target_agent_type


async def route_delegated_task_request(
    *,
    coordinator: Any,
    task: Any,
    from_agent_id: int,
    from_agent_name: str,
    to_agent_id: int,
    to_agent_name: str,
    context: str,
) -> Any:
    """Create and route the collaboration task-request message for a delegated task."""

    from agents.collaboration import CollaborationMessage, CollaborationMessageType, uuid

    message = CollaborationMessage(
        id=str(uuid.uuid4()),
        message_type=CollaborationMessageType.TASK_REQUEST,
        from_agent_id=from_agent_id,
        from_agent_name=from_agent_name,
        to_agent_id=to_agent_id,
        to_agent_name=to_agent_name,
        chatroom_id=task.chatroom_id,
        content=f"**Task: {task.title}**\n\n{task.description}\n\nContext: {context}",
        task_id=task.id,
        metadata={"task": task.dict()},
    )
    await coordinator.route_message(message)
    return message


async def route_broadcast_message(
    *,
    coordinator: Any,
    from_agent_id: int,
    from_agent_name: str,
    chatroom_id: int,
    content: str,
) -> Any:
    """Create and route one collaboration broadcast message."""

    from agents.collaboration import CollaborationMessage, CollaborationMessageType, uuid

    message = CollaborationMessage(
        id=str(uuid.uuid4()),
        message_type=CollaborationMessageType.BROADCAST,
        from_agent_id=from_agent_id,
        from_agent_name=from_agent_name,
        chatroom_id=chatroom_id,
        content=content,
    )
    await coordinator.route_message(message)
    return message


async def route_direct_message(
    *,
    coordinator: Any,
    from_agent_id: int,
    from_agent_name: str,
    to_agent_id: int,
    to_agent_name: str,
    chatroom_id: int,
    content: str,
) -> Any:
    """Create and route one collaboration direct message."""

    from agents.collaboration import CollaborationMessage, CollaborationMessageType, uuid

    message = CollaborationMessage(
        id=str(uuid.uuid4()),
        message_type=CollaborationMessageType.DIRECT,
        from_agent_id=from_agent_id,
        from_agent_name=from_agent_name,
        to_agent_id=to_agent_id,
        to_agent_name=to_agent_name,
        chatroom_id=chatroom_id,
        content=content,
    )
    await coordinator.route_message(message)
    return message
