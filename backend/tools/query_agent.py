# -*- coding: utf-8 -*-
"""
Query Agent Tool

Allows one agent to synchronously ask another agent a question and get back
an immediate response. This is the core "agent-to-agent query" capability.
"""
from .base import BaseTool
from typing import Optional, Dict, Any
import json
import logging

logger = logging.getLogger("catown.query_agent")


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
        "The target agent will answer based on their role, system prompt, and shared context. "
        "Available agents and their roles are shown when you use list_collaborators."
    )

    def __init__(self, collaboration_coordinator=None):
        self.coordinator = collaboration_coordinator

    async def execute(
        self,
        agent_name: str,
        question: str,
        include_context: bool = True,
        **kwargs
    ) -> str:
        """
        Query another agent synchronously.

        Args:
            agent_name: Name of the agent to query (e.g. 'architect', 'developer')
            question: The question to ask
            include_context: Whether to include current project context (default true)

        Returns:
            The target agent's response text, or an error message.
        """
        current_agent_name = kwargs.get("agent_name", "unknown")
        chatroom_id = kwargs.get("chatroom_id", 0)

        # Prevent self-query
        if agent_name == current_agent_name:
            return f"[query_agent] Error: Cannot query yourself ({agent_name})."

        # 1. Find target agent in DB
        from models.database import get_db, Agent as DBAgent, Chatroom as DBChatroom, Project
        from models.database import Memory

        db = next(get_db())
        try:
            target_db_agent = db.query(DBAgent).filter(
                DBAgent.name == agent_name,
                DBAgent.is_active == True
            ).first()

            if not target_db_agent:
                available = [a.name for a in db.query(DBAgent).filter(DBAgent.is_active == True).all()]
                return (
                    f"[query_agent] Error: Agent '{agent_name}' not found. "
                    f"Available agents: {available}"
                )

            # 2. Get the target agent's LLM client
            from llm.client import get_llm_client_for_agent
            try:
                llm_client = get_llm_client_for_agent(agent_name)
            except RuntimeError as e:
                return f"[query_agent] Error: Cannot get LLM for '{agent_name}': {e}"

            # 3. Build the system prompt for the queried agent
            system_prompt = target_db_agent.system_prompt or (
                f"You are {target_db_agent.name}, a {target_db_agent.role}."
            )

            # Inject project context if available
            if include_context and chatroom_id:
                chatroom = db.query(DBChatroom).filter(DBChatroom.id == chatroom_id).first()
                if chatroom and chatroom.project_id:
                    project = db.query(Project).filter(Project.id == chatroom.project_id).first()
                    if project:
                        system_prompt += f"\n\nCurrent project: {project.name}"
                        if project.description:
                            system_prompt += f"\nProject description: {project.description}"

            # Inject the target agent's own memories (top 5)
            own_memories = (
                db.query(Memory)
                .filter(Memory.agent_id == target_db_agent.id)
                .order_by(Memory.importance.desc(), Memory.created_at.desc())
                .limit(5)
                .all()
            )
            if own_memories:
                system_prompt += "\n\nYour memories:"
                for mem in own_memories:
                    ts = mem.created_at.strftime("%Y-%m-%d %H:%M") if mem.created_at else "?"
                    system_prompt += f"\n- [{ts}] {mem.content[:200]}"

            # Tell the agent who's asking
            system_prompt += (
                f"\n\nYou are being queried by agent '{current_agent_name}'. "
                f"Answer concisely based on your role and expertise. "
                f"Do NOT use any tools — just answer directly from your knowledge."
            )

            # 4. Build messages — include recent chatroom history as context
            messages = [{"role": "system", "content": system_prompt}]

            if chatroom_id:
                from chatrooms.manager import chatroom_manager
                recent = await chatroom_manager.get_messages(chatroom_id, limit=6)
                for msg in recent[-4:]:
                    a_name = msg.agent.name if hasattr(msg, 'agent') and msg.agent else None
                    if msg.message_type == "user" or not a_name:
                        messages.append({"role": "user", "content": msg.content})
                    else:
                        messages.append({"role": "assistant", "content": msg.content})

            # Add the actual question
            messages.append({
                "role": "user",
                "content": f"[Query from {current_agent_name}]: {question}"
            })

            # 5. Call LLM — NO tools to prevent recursion
            logger.info(
                f"[query_agent] {current_agent_name} → {agent_name}: {question[:80]}"
            )

            response = await llm_client.chat(
                messages,
                temperature=0.7,
                max_tokens=1500
            )

            if not response:
                return f"[query_agent] Agent '{agent_name}' returned an empty response."

            # 6. Log the interaction as a pipeline message if in a pipeline context
            try:
                from models.database import PipelineMessage, PipelineRun, Pipeline as PipelineModel
                if chatroom_id:
                    chatroom = db.query(DBChatroom).filter(DBChatroom.id == chatroom_id).first()
                    if chatroom and chatroom.project_id:
                        active_run = (
                            db.query(PipelineRun)
                            .join(PipelineModel, PipelineRun.pipeline_id == PipelineModel.id)
                            .filter(PipelineRun.status == "running")
                            .first()
                        )
                        if active_run:
                            pm = PipelineMessage(
                                run_id=active_run.id,
                                message_type="AGENT_QUESTION",
                                from_agent=current_agent_name,
                                to_agent=agent_name,
                                content=f"Q: {question[:500]}\nA: {response[:500]}"
                            )
                            db.add(pm)
                            db.commit()
            except Exception:
                pass  # Non-critical, don't fail the query

            logger.info(
                f"[query_agent] {agent_name} responded ({len(response)} chars)"
            )

            return f"[Response from {agent_name} ({target_db_agent.role})]:\n{response}"

        except Exception as e:
            logger.error(f"[query_agent] Error: {e}")
            return f"[query_agent] Error querying agent '{agent_name}': {str(e)}"
        finally:
            db.close()

    def _get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "Name of the agent to query (e.g. 'architect', 'developer', 'analyst', 'tester', 'release')"
                },
                "question": {
                    "type": "string",
                    "description": "The question to ask the agent. Be specific for better answers."
                },
                "include_context": {
                    "type": "boolean",
                    "description": "Whether to include current project context in the query (default: true)",
                    "default": True
                }
            },
            "required": ["agent_name", "question"]
        }
