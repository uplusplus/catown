# -*- coding: utf-8 -*-
"""Session and project creation flows for standalone chats and hidden project chats."""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session

from chatrooms.manager import ChatroomInstance, chatroom_manager
from models.database import Agent, AgentAssignment, Chatroom, Message, Project


class SessionService:
    """Encapsulate chat/project creation rules from the business flow doc."""

    SEED_MESSAGE_LIMIT = 20

    def __init__(self, db: Session):
        self.db = db

    def list_visible_chats(self) -> list[Chatroom]:
        return (
            self.db.query(Chatroom)
            .filter(
                Chatroom.session_type == "standalone",
                Chatroom.is_visible_in_chat_list == True,
            )
            .order_by(Chatroom.created_at.desc(), Chatroom.id.desc())
            .all()
        )

    def get_or_create_default_standalone_chat(self, title: str | None = None) -> Chatroom:
        chatroom = (
            self.db.query(Chatroom)
            .filter(
                Chatroom.session_type == "standalone",
                Chatroom.is_visible_in_chat_list == True,
            )
            .order_by(Chatroom.created_at.asc(), Chatroom.id.asc())
            .first()
        )
        return chatroom or self.create_standalone_chat(title=title)

    def _next_project_display_order(self) -> int:
        latest = self.db.query(Project).order_by(Project.display_order.desc(), Project.id.desc()).first()
        if not latest:
            return 0
        return int(latest.display_order or 0) + 1

    def create_standalone_chat(self, title: str | None = None) -> Chatroom:
        chatroom = Chatroom(
            project_id=None,
            title=(title or "").strip() or "New Chat",
            session_type="standalone",
            is_visible_in_chat_list=True,
        )
        self.db.add(chatroom)
        self.db.commit()
        self.db.refresh(chatroom)
        self._register_chatroom_instance(chatroom, project_name=chatroom.title or "Standalone Chat")
        return chatroom

    def get_or_create_self_bootstrap_project(self) -> tuple[Project, Chatroom, list[Agent]]:
        workspace_path = str(self._self_workspace_root())
        project = (
            self.db.query(Project)
            .filter(Project.workspace_path == workspace_path)
            .order_by(Project.id.asc())
            .first()
        )
        if project:
            project_chat = self.get_project_chat(project.id)
            self._register_chatroom_instance(project_chat, project_name=project.name or "Catown")
            agents = self._project_agents(project.id)
            return project, project_chat, agents

        agent_names = self._default_self_project_agent_names()
        project_name = self._self_workspace_root().name or "catown"
        return self.create_project_directly(
            name=project_name,
            description="Self-bootstrap workspace bound to the current Catown repository.",
            agent_names=agent_names,
            workspace_path=workspace_path,
        )

    def create_hidden_project_chat(
        self,
        project_id: int,
        title: str,
        source_chatroom_id: int | None = None,
    ) -> Chatroom:
        chatroom = Chatroom(
            project_id=project_id,
            title=title.strip() or "Project Chat",
            session_type="project-bound",
            is_visible_in_chat_list=False,
            source_chatroom_id=source_chatroom_id,
        )
        self.db.add(chatroom)
        self.db.flush()
        self._register_chatroom_instance(chatroom, project_name=chatroom.title or "Project Chat")
        return chatroom

    def create_project_subchat(self, project_id: int, title: str | None = None) -> Chatroom:
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        project_chat = self.get_project_chat(project_id)
        existing_count = (
            self.db.query(Chatroom)
            .filter(
                Chatroom.source_chatroom_id == project_chat.id,
                Chatroom.is_visible_in_chat_list == True,
            )
            .count()
        )
        chatroom = Chatroom(
            project_id=None,
            title=(title or "").strip() or f"{project.name} Chat {existing_count + 1}",
            session_type="standalone",
            is_visible_in_chat_list=True,
            source_chatroom_id=project_chat.id,
        )
        self.db.add(chatroom)
        self.db.commit()
        self.db.refresh(chatroom)
        self._register_chatroom_instance(chatroom, project_name=project.name or chatroom.title or "Project Chat")
        return chatroom

    def get_chatroom_or_404(self, chatroom_id: int) -> Chatroom:
        chatroom = self.db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
        if not chatroom:
            raise HTTPException(status_code=404, detail="Chatroom not found")
        return chatroom

    def get_project_chat(self, project_id: int) -> Chatroom:
        project_chat = (
            self.db.query(Chatroom)
            .filter(Chatroom.project_id == project_id, Chatroom.session_type == "project-bound")
            .order_by(Chatroom.id.asc())
            .first()
        )
        if not project_chat:
            raise HTTPException(status_code=404, detail="Project chat not found")
        return project_chat

    def create_project_directly(
        self,
        name: str,
        description: str,
        agent_names: list[str],
        workspace_path: str | None = None,
    ) -> tuple[Project, Chatroom, list[Agent]]:
        project = Project(
            name=name,
            description=description,
            display_order=self._next_project_display_order(),
        )
        self.db.add(project)
        self.db.flush()
        project.workspace_path = self._resolve_workspace_path(project, workspace_path)

        project_chat = self.create_hidden_project_chat(
            project_id=project.id,
            title=name,
            source_chatroom_id=None,
        )
        project.default_chatroom_id = project_chat.id

        assigned_agents = self._assign_agents(project.id, project_chat.id, agent_names)

        self.db.add(project)
        self.db.commit()
        self.db.refresh(project)
        self.db.refresh(project_chat)
        return project, project_chat, assigned_agents

    def create_project_from_chat(
        self,
        source_chatroom_id: int,
        name: str,
        description: str,
        agent_names: list[str],
        workspace_path: str | None = None,
    ) -> tuple[Project, Chatroom, list[Agent]]:
        source_chatroom = self.get_chatroom_or_404(source_chatroom_id)
        if source_chatroom.session_type != "standalone":
            raise HTTPException(status_code=400, detail="Only standalone chats can be converted into projects")

        project = Project(
            name=name,
            description=description,
            display_order=self._next_project_display_order(),
        )
        self.db.add(project)
        self.db.flush()
        project.workspace_path = self._resolve_workspace_path(project, workspace_path)

        project_chat = self.create_hidden_project_chat(
            project_id=project.id,
            title=name,
            source_chatroom_id=source_chatroom.id,
        )
        project.default_chatroom_id = project_chat.id
        self.copy_chat_context(source_chatroom.id, project_chat.id)

        assigned_agents = self._assign_agents(project.id, project_chat.id, agent_names)

        self.db.add(project)
        self.db.commit()
        self.db.refresh(project)
        self.db.refresh(project_chat)
        return project, project_chat, assigned_agents

    def copy_chat_context(self, source_chatroom_id: int, target_chatroom_id: int) -> None:
        source_messages = (
            self.db.query(Message)
            .filter(Message.chatroom_id == source_chatroom_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(self.SEED_MESSAGE_LIMIT)
            .all()
        )
        source_messages = list(reversed(source_messages))

        self.db.add(
            Message(
                chatroom_id=target_chatroom_id,
                agent_id=None,
                content=(
                    f"This project was created from standalone chat #{source_chatroom_id}. "
                    "The following messages were copied as seed context."
                ),
                message_type="system",
                metadata_json="{}",
            )
        )
        for source_message in source_messages:
            self.db.add(
                Message(
                    chatroom_id=target_chatroom_id,
                    agent_id=source_message.agent_id,
                    content=source_message.content,
                    message_type=source_message.message_type,
                    metadata_json=source_message.metadata_json,
                    created_at=source_message.created_at,
                )
            )
        self.db.flush()

    def _assign_agents(self, project_id: int, chatroom_id: int, agent_names: list[str]) -> list[Agent]:
        assigned_agents: list[Agent] = []
        project_chat = chatroom_manager.get_chatroom(chatroom_id)

        for agent_name in agent_names:
            agent = self.db.query(Agent).filter(Agent.name == agent_name).first()
            if not agent:
                continue

            self.db.add(AgentAssignment(project_id=project_id, agent_id=agent.id))
            assigned_agents.append(agent)

            if project_chat:
                project_chat.add_agent(agent.id)

        self.db.flush()
        return assigned_agents

    def _project_agents(self, project_id: int) -> list[Agent]:
        assignments = self.db.query(AgentAssignment).filter(AgentAssignment.project_id == project_id).all()
        agent_ids = [assignment.agent_id for assignment in assignments]
        if not agent_ids:
            return []
        return self.db.query(Agent).filter(Agent.id.in_(agent_ids)).order_by(Agent.id.asc()).all()

    def _register_chatroom_instance(self, chatroom: Chatroom, project_name: str) -> None:
        if chatroom.id in chatroom_manager.chatrooms:
            return
        chatroom_manager.chatrooms[chatroom.id] = ChatroomInstance(
            id=chatroom.id,
            project_id=chatroom.project_id,
            project_name=project_name,
        )

    def _resolve_workspace_path(self, project: Project, workspace_path: str | None) -> str:
        if workspace_path:
            resolved = Path(workspace_path).expanduser()
            resolved.mkdir(parents=True, exist_ok=True)
            return str(resolved)

        workspace_root = Path(
            os.getenv(
                "CATOWN_PROJECTS_ROOT",
                str(Path(__file__).resolve().parent.parent / "data" / "projects"),
            )
        )
        try:
            workspace_root.mkdir(parents=True, exist_ok=True)
        except OSError:
            workspace_root = Path(tempfile.gettempdir()) / "catown-projects"
            workspace_root.mkdir(parents=True, exist_ok=True)

        safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", (project.name or "project").strip()).strip("-") or "project"
        workspace_dir = workspace_root / f"{project.id}-{safe_name}"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        return str(workspace_dir)

    def _self_workspace_root(self) -> Path:
        configured = os.getenv("CATOWN_SELF_WORKSPACE")
        if configured:
            return Path(configured).expanduser().resolve()
        return Path(__file__).resolve().parent.parent.parent

    def _default_self_project_agent_names(self) -> list[str]:
        active_agents = (
            self.db.query(Agent)
            .filter(Agent.is_active == True)
            .order_by(Agent.id.asc())
            .all()
        )
        names = [agent.name for agent in active_agents if agent.name]
        return names or ["assistant"]
