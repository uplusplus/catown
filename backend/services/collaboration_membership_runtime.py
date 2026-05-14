"""Shared runtime helpers for collaboration membership and room composition."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from agents.identity import agent_name_of, normalize_agent_type


def ensure_chatroom_collaborators(
    *,
    agents: list[Any],
    chatroom_id: int,
    coordinator: Any,
    agent_name_resolver,
) -> list[dict[str, Any]]:
    """Register missing collaborators for one chatroom and return the newly added entries."""

    from agents.collaboration import AgentCollaborator

    registered: list[dict[str, Any]] = []
    for agent in agents:
        agent_id = getattr(agent, "id", None)
        if agent_id is None or agent_id in coordinator.collaborators:
            continue
        agent_name = str(agent_name_resolver(agent))
        coordinator.register_collaborator(
            AgentCollaborator(
                agent_id=agent_id,
                agent_name=agent_name,
                chatroom_id=chatroom_id,
            )
        )
        registered.append(
            {
                "agent_id": agent_id,
                "agent_name": agent_name,
                "chatroom_id": chatroom_id,
            }
        )
    return registered


def get_chatroom_collaborator_summary(
    *,
    db: Session,
    chatroom_id: int,
    coordinator: Any | None = None,
) -> dict[str, Any]:
    """Return collaborator data for one chatroom with DB fallback when runtime state is empty."""

    if coordinator is not None:
        agent_ids = coordinator.chatroom_agents.get(chatroom_id, set())
        if agent_ids:
            collaborators: list[dict[str, Any]] = []
            for agent_id in agent_ids:
                collaborator = coordinator.collaborators.get(agent_id)
                if collaborator is None:
                    continue
                collaborators.append(
                    {
                        "agent_id": agent_id,
                        "agent_name": collaborator.agent_name,
                        "status": "active" if collaborator.is_active else "inactive",
                        "pending_tasks": len(collaborator.assigned_tasks),
                    }
                )
            return {
                "source": "coordinator",
                "chatroom_id": chatroom_id,
                "collaborators": collaborators,
            }

    from models.database import Agent as DBAgent, AgentAssignment, Chatroom

    chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
    if chatroom is None or not chatroom.project_id:
        return {
            "source": "none",
            "chatroom_id": chatroom_id,
            "collaborators": [],
        }

    assignments = db.query(AgentAssignment).filter(
        AgentAssignment.project_id == chatroom.project_id
    ).all()
    assigned_ids = [assignment.agent_id for assignment in assignments]
    room_agents = (
        db.query(DBAgent)
        .filter(DBAgent.id.in_(assigned_ids), DBAgent.is_active == True)
        .all()
        if assigned_ids
        else []
    )
    return {
        "source": "database",
        "chatroom_id": chatroom_id,
        "collaborators": [
            {
                "agent_id": agent.id,
                "agent_name": agent.name,
                "role": agent.role,
                "tools": agent.tools if isinstance(agent.tools, str) else str(agent.tools),
            }
            for agent in room_agents
        ],
    }


def get_invitable_agents_summary(*, db: Session, chatroom_id: int) -> dict[str, Any]:
    """Return system agents that are not currently assigned to one chatroom project."""

    from models.database import Agent as DBAgent, AgentAssignment, Chatroom

    chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
    if chatroom is None or not chatroom.project_id:
        return {
            "chatroom_id": chatroom_id,
            "project_id": None,
            "agents": [],
            "error": "No project associated with this chatroom",
        }

    assignments = db.query(AgentAssignment).filter(
        AgentAssignment.project_id == chatroom.project_id
    ).all()
    room_agent_ids = {assignment.agent_id for assignment in assignments}
    all_agents = db.query(DBAgent).filter(DBAgent.is_active == True).all()
    external_agents = [agent for agent in all_agents if agent.id not in room_agent_ids]

    return {
        "chatroom_id": chatroom_id,
        "project_id": chatroom.project_id,
        "agents": [
            {
                "agent_id": agent.id,
                "agent_name": agent.agent_type or agent.name,
                "display_name": agent_name_of(agent),
                "role": agent.role,
            }
            for agent in external_agents
        ],
        "error": None,
    }


def invite_agent_to_chatroom(
    *,
    db: Session,
    chatroom_id: int,
    agent_name: str,
    coordinator: Any | None = None,
) -> dict[str, Any]:
    """Assign one agent to the chatroom project and register a runtime collaborator when possible."""

    from agents.collaboration import AgentCollaborator
    from models.database import Agent as DBAgent, AgentAssignment, Chatroom

    target_agent_type = normalize_agent_type(agent_name)
    chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
    if chatroom is None or not chatroom.project_id:
        return {"ok": False, "detail": "[Invite] Error: No project associated with this chatroom"}

    target = db.query(DBAgent).filter(
        DBAgent.agent_type == target_agent_type,
        DBAgent.is_active == True,
    ).first()
    if target is None:
        return {"ok": False, "detail": f"[Invite] Error: Agent '{target_agent_type}' not found in the system."}

    existing = db.query(AgentAssignment).filter(
        AgentAssignment.project_id == chatroom.project_id,
        AgentAssignment.agent_id == target.id,
    ).first()
    if existing:
        return {"ok": False, "detail": f"[Invite] Agent '{target_agent_type}' is already in this room."}

    db.add(AgentAssignment(project_id=chatroom.project_id, agent_id=target.id))
    db.commit()

    if coordinator is not None:
        coordinator.register_collaborator(
            AgentCollaborator(
                agent_id=target.id,
                agent_name=target.agent_type or target.name,
                chatroom_id=chatroom_id,
            )
        )

    return {
        "ok": True,
        "agent_id": target.id,
        "agent_name": target.agent_type or target.name,
        "display_name": agent_name_of(target),
        "role": target.role,
        "detail": (
            f"[Invite] Agent '{target_agent_type}' "
            f"({agent_name_of(target)}, role: {target.role}) has joined this room."
        ),
    }
