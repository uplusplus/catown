# -*- coding: utf-8 -*-
"""
Query Agent Tool

Allows one agent to synchronously ask another agent a question and get back
an immediate response. This is the core "agent-to-agent query" capability.
"""
from typing import Any
import json
import logging

from sqlalchemy import or_

from .base import BaseTool
from agents.identity import (
    agent_name_of,
    default_agent_name,
    legacy_default_agent_names,
    normalize_agent_type,
)
from config import settings
from services.chat_prompt_builder import build_chat_context_selector
from services.context_builder import (
    assemble_messages,
    build_base_system_prompt,
    build_history_summary_fragment,
    build_operating_developer_context,
    build_recent_history,
    build_runtime_user_fragments,
    build_stage_developer_context,
)
from services.task_state import build_task_state, build_task_state_fragments
from skills import load_skill_registry

logger = logging.getLogger("catown.query_agent")


def _agent_skill_ids(agent: Any) -> list[str]:
    raw_skills = getattr(agent, "skills", None)
    if isinstance(raw_skills, str):
        try:
            parsed = json.loads(raw_skills or "[]")
        except (TypeError, json.JSONDecodeError):
            parsed = []
        return [str(skill) for skill in parsed if skill]
    if isinstance(raw_skills, list):
        return [str(skill) for skill in raw_skills if skill]
    return []


def _memory_context_lines(memories: list[Any]) -> list[str]:
    lines: list[str] = []
    for mem in memories:
        ts = mem.created_at.strftime("%Y-%m-%d %H:%M") if getattr(mem, "created_at", None) else "?"
        lines.append(f"- [{ts}] {str(getattr(mem, 'content', '') or '')[:200]}")
    return lines


class QueryAgentTool(BaseTool):
    """
    Synchronously query another agent and get an immediate response.

    Unlike delegate_task (async fire-and-forget) or send_direct_message (one-way),
    this tool calls the target agent's LLM right now and returns the answer.

    Safety: the queried agent runs with tools DISABLED to prevent infinite loops
    (Agent A queries Agent B who queries Agent A...).
    """

    name = "query_agent"
    description = (
        "Ask another agent a question and get an immediate answer. "
        "Use this when you need another agent's expertise to continue your work. "
        "The target agent will answer based on their role, structured context, and shared room state. "
        "Available agents and their roles are shown when you use list_collaborators."
    )

    def __init__(self, collaboration_coordinator=None):
        self.coordinator = collaboration_coordinator

    async def execute(
        self,
        target_agent: str,
        question: str,
        include_context: bool = True,
        **kwargs,
    ) -> str:
        """
        Query another agent synchronously.

        Args:
            target_agent: Name of the agent to query (e.g. 'architect', 'developer')
            question: The question to ask
            include_context: Whether to include current project context (default true)

        Returns:
            The target agent's response text, or an error message.
        """
        runtime_agent_name = str(kwargs.get("agent_name", "") or "").strip()
        legacy_target_agent = runtime_agent_name if not target_agent else ""
        target_agent_name = str(target_agent or legacy_target_agent).strip()
        current_agent_name = str(
            kwargs.get("caller_agent_name")
            or (runtime_agent_name if target_agent else "")
            or "unknown"
        ).strip() or "unknown"
        chatroom_id = kwargs.get("chatroom_id", 0)
        target_agent_type = normalize_agent_type(target_agent_name)
        current_agent_type = normalize_agent_type(current_agent_name)

        if not target_agent_type:
            return "[query_agent] Error: target_agent is required."
        if target_agent_type == current_agent_type:
            return f"[query_agent] Error: Cannot query yourself ({target_agent_type})."

        from models.database import (
            Agent as DBAgent,
            AgentAssignment,
            Chatroom as DBChatroom,
            Memory,
            Pipeline as PipelineModel,
            PipelineMessage,
            PipelineRun,
            Project,
            get_db,
        )
        from llm.client import get_llm_client_for_agent

        db = next(get_db())
        try:
            candidate_names = {target_agent_type, default_agent_name(target_agent_type)}
            candidate_names.update({value.title() for value in legacy_default_agent_names(target_agent_type)})
            candidate_names.update(legacy_default_agent_names(target_agent_type))
            target_db_agent = db.query(DBAgent).filter(
                or_(
                    DBAgent.agent_type == target_agent_type,
                    DBAgent.name.in_(sorted(candidate_names)),
                ),
                DBAgent.is_active == True,
            ).first()

            chatroom = db.query(DBChatroom).filter(DBChatroom.id == chatroom_id).first() if chatroom_id else None
            if not target_db_agent:
                room_agents = []
                if chatroom and chatroom.project_id:
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
                available = [agent.agent_type or agent.name for agent in room_agents]
                return (
                    f"[query_agent] Error: Agent '{target_agent_type}' not found in this room. "
                    f"Agents in this room: {available}"
                )

            if chatroom and chatroom.project_id:
                assignment = db.query(AgentAssignment).filter(
                    AgentAssignment.project_id == chatroom.project_id,
                    AgentAssignment.agent_id == target_db_agent.id,
                ).first()
                if not assignment:
                    return (
                        f"[query_agent] Error: Agent '{target_agent_type}' is not in this room. "
                        f"Use @mention to invite them first."
                    )

            try:
                llm_client = get_llm_client_for_agent(target_agent_type)
            except RuntimeError as exc:
                return f"[query_agent] Error: Cannot get LLM for '{target_agent_type}': {exc}"

            project = None
            if include_context and chatroom and chatroom.project_id:
                project = db.query(Project).filter(Project.id == chatroom.project_id).first()

            own_memories = (
                db.query(Memory)
                .filter(Memory.agent_id == target_db_agent.id)
                .order_by(Memory.importance.desc(), Memory.created_at.desc())
                .limit(5)
                .all()
            )

            recent = []
            if chatroom_id:
                from chatrooms.manager import chatroom_manager

                recent = await chatroom_manager.get_messages(chatroom_id, limit=6)

            history_messages = build_recent_history(recent, limit=4)
            history_summary = build_history_summary_fragment(recent, keep_last=4)
            query_input = f"[Query from {current_agent_name}]: {question}"
            current_input_messages = []
            if not (
                history_messages
                and history_messages[-1].get("role") == "user"
                and str(history_messages[-1].get("content") or "").strip() == query_input
            ):
                current_input_messages.append({"role": "user", "content": query_input})

            runtime_note = (
                "## Query Context\n"
                f"- Queried by agent: {current_agent_name}\n"
                "- Tools are disabled for this synchronous query.\n"
                "- Answer directly from your role, memory, and the shared room context."
            )
            base_system_prompt = build_base_system_prompt(
                target_db_agent,
                fallback_name=agent_name_of(target_db_agent),
                fallback_role=target_db_agent.role,
            )
            messages = assemble_messages(
                base_system_prompt=base_system_prompt,
                developer_fragments=[
                    build_operating_developer_context(
                        agent_name=agent_name_of(target_db_agent),
                        agent_role=target_db_agent.role,
                    ),
                    build_stage_developer_context(
                        tools=[],
                        skills_config=load_skill_registry(settings.SKILLS_DIR),
                        agent_skills=_agent_skill_ids(target_db_agent),
                        tool_guidance=(
                            "Tools are disabled for this query. "
                            "Respond directly from your expertise and the visible context."
                        ),
                    ),
                ],
                user_fragments=[
                    *build_task_state_fragments(
                        build_task_state(
                            project=project if include_context else None,
                            current_request=query_input,
                        )
                    ),
                    *([history_summary] if history_summary is not None else []),
                    *build_runtime_user_fragments(
                        project=project if include_context else None,
                        chatroom=chatroom if include_context else None,
                        runtime_context=runtime_note,
                        memories=_memory_context_lines(own_memories),
                    ),
                ],
                history_messages=history_messages,
                current_input_messages=current_input_messages,
                selector=build_chat_context_selector(
                    profile="query_agent",
                    agent_name=agent_name_of(target_db_agent),
                    model_id=getattr(llm_client, "model", ""),
                    base_system_prompt=base_system_prompt,
                    history_messages=history_messages,
                    current_input_messages=current_input_messages,
                ),
            ).to_messages()

            logger.info(f"[query_agent] {current_agent_type} -> {target_agent_type}: {question[:80]}")
            response = await llm_client.chat(messages, temperature=0.7, max_tokens=1500)

            if not response:
                return f"[query_agent] Agent '{target_agent_type}' returned an empty response."

            try:
                if chatroom and chatroom.project_id:
                    active_run = (
                        db.query(PipelineRun)
                        .join(PipelineModel, PipelineRun.pipeline_id == PipelineModel.id)
                        .filter(PipelineRun.status == "running")
                        .first()
                    )
                    if active_run:
                        db.add(
                            PipelineMessage(
                                run_id=active_run.id,
                                message_type="AGENT_QUESTION",
                                from_agent=current_agent_type,
                                to_agent=target_agent_type,
                                content=f"Q: {question[:500]}\nA: {response[:500]}",
                            )
                        )
                        db.commit()
            except Exception:
                pass

            logger.info(f"[query_agent] {target_agent_type} responded ({len(response)} chars)")
            return f"[Response from {target_agent_type} ({target_db_agent.role})]:\n{response}"
        except Exception as exc:
            logger.error(f"[query_agent] Error: {exc}")
            return f"[query_agent] Error querying agent '{target_agent_type}': {exc}"
        finally:
            db.close()

    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "target_agent": {
                    "type": "string",
                    "description": "Name of the agent to query (e.g. 'architect', 'developer', 'analyst', 'tester', 'release')",
                },
                "question": {
                    "type": "string",
                    "description": "The question to ask the agent. Be specific for better answers.",
                },
                "include_context": {
                    "type": "boolean",
                    "description": "Whether to include current project context in the query (default: true)",
                    "default": True,
                },
            },
            "required": ["target_agent", "question"],
        }
