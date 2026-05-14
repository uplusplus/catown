# -*- coding: utf-8 -*-
"""
Consult Agent Tool

Allows one agent to synchronously ask another agent a question and get back
an immediate response. This is the core "agent-to-agent consultation" capability.
"""
from typing import Any
import json
import logging
import time

from .base import BaseTool
from agents.identity import agent_name_of
from config import settings
from services.agent_action_runtime import (
    build_consult_agent_messages,
    build_consult_agent_prompt_profile,
    build_consult_agent_prompt_state,
    run_consult_agent_preflight_action,
    run_consult_agent_response_action,
)

logger = logging.getLogger("catown.consult_agent")


async def _store_consult_runtime_card(chatroom_id: Any, payload: dict[str, Any]) -> None:
    if not chatroom_id:
        return
    from chatrooms.manager import chatroom_manager

    await chatroom_manager.send_message(
        chatroom_id=int(chatroom_id),
        agent_id=None,
        content=payload.get("type", "runtime_card"),
        message_type="runtime_card",
        metadata={"card": payload},
    )


def _consult_step_payload(
    *,
    step_id: str,
    agent_id: Any,
    agent_name: str,
    agent_type: str,
    current_agent_name: str,
    question: str,
) -> dict[str, Any]:
    return {
        "step_id": step_id,
        "position": 0,
        "requested_name": agent_type,
        "agent_id": agent_id,
        "agent_name": agent_name,
        "agent_type": agent_type,
        "dispatch_kind": "consult",
        "wait_for_step_id": None,
        "attached_to_step_id": None,
        "source": "consult_agent",
        "context": {
            "requested_by": current_agent_name,
            "question_preview": question[:200],
        },
        "step_state": {
            "step_id": step_id,
            "position": 0,
            "requested_name": agent_type,
            "agent_id": agent_id,
            "agent_name": agent_name,
            "agent_type": agent_type,
            "dispatch_kind": "consult",
            "wait_for_step_id": None,
            "attached_to_step_id": None,
            "source": "consult_agent",
            "status": "running",
            "released_by_step_id": None,
            "dispatch_count": 1,
            "completion_count": 0,
        },
    }


def _record_consult_subagent_dispatched(
    *,
    db: Any,
    task_run_id: int | None,
    client_turn_id: str | None,
    step_id: str,
    target_db_agent: Any,
    target_agent_type: str,
    current_agent_name: str,
    question: str,
) -> None:
    if task_run_id is None:
        return
    from services.run_ledger import append_task_event, get_task_run

    task_run = get_task_run(db, task_run_id)
    if task_run is None:
        return
    append_task_event(
        db,
        task_run,
        "scheduler_step_dispatched",
        agent_name=getattr(target_db_agent, "name", target_agent_type),
        summary=f"Consultation dispatched to {getattr(target_db_agent, 'name', target_agent_type)}.",
        payload={
            **_consult_step_payload(
                step_id=step_id,
                agent_id=getattr(target_db_agent, "id", None),
                agent_name=getattr(target_db_agent, "name", target_agent_type),
                agent_type=target_agent_type,
                current_agent_name=current_agent_name,
                question=question,
            ),
            "client_turn_id": client_turn_id,
            "task_run_id": task_run_id,
        },
    )


def _record_consult_subagent_completed(
    *,
    db: Any,
    task_run_id: int | None,
    client_turn_id: str | None,
    step_id: str,
    target_db_agent: Any,
    target_agent_type: str,
    current_agent_name: str,
    question: str,
    response: str | None,
) -> None:
    if task_run_id is None:
        return
    from services.run_ledger import append_task_event, get_task_run

    task_run = get_task_run(db, task_run_id)
    if task_run is None:
        return
    payload = _consult_step_payload(
        step_id=step_id,
        agent_id=getattr(target_db_agent, "id", None),
        agent_name=getattr(target_db_agent, "name", target_agent_type),
        agent_type=target_agent_type,
        current_agent_name=current_agent_name,
        question=question,
    )
    payload["step_state"]["status"] = "completed"
    payload["step_state"]["completion_count"] = 1
    payload["response_preview"] = str(response or "")[:500]
    payload["client_turn_id"] = client_turn_id
    payload["task_run_id"] = task_run_id
    append_task_event(
        db,
        task_run,
        "scheduler_step_completed",
        agent_name=getattr(target_db_agent, "name", target_agent_type),
        summary=f"Consultation completed by {getattr(target_db_agent, 'name', target_agent_type)}.",
        payload=payload,
    )


def _record_consult_subagent_failed(
    *,
    db: Any,
    task_run_id: int | None,
    client_turn_id: str | None,
    step_id: str,
    target_agent_type: str,
    current_agent_name: str,
    question: str,
    error: str,
) -> None:
    if task_run_id is None:
        return
    from services.run_ledger import append_task_event, get_task_run

    task_run = get_task_run(db, task_run_id)
    if task_run is None:
        return
    payload = _consult_step_payload(
        step_id=step_id,
        agent_id=None,
        agent_name=target_agent_type,
        agent_type=target_agent_type,
        current_agent_name=current_agent_name,
        question=question,
    )
    payload["step_state"]["status"] = "failed"
    payload["error"] = error[:1000]
    payload["client_turn_id"] = client_turn_id
    payload["task_run_id"] = task_run_id
    append_task_event(
        db,
        task_run,
        "scheduler_step_failed",
        agent_name=target_agent_type,
        summary=f"Consultation failed for {target_agent_type}.",
        payload=payload,
    )


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


class ConsultAgentTool(BaseTool):
    """
    Synchronously consult another agent and get an immediate response.

    Unlike delegate_task (async fire-and-forget) or send_direct_message (one-way),
    this tool calls the target agent's LLM right now and returns the answer.

    Safety: the consulted agent runs with tools DISABLED to prevent infinite loops
    (Agent A queries Agent B who queries Agent A...).
    """

    name = "consult_agent"
    description = (
        "Consult another agent and get an immediate answer. "
        "Use this when you need another agent's expertise right now and want a synchronous reply instead of a tracked delegated task. "
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
        Consult another agent synchronously.

        Args:
            target_agent: Name of the agent to consult (e.g. 'architect', 'developer')
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
        task_run_id = kwargs.get("task_run_id")
        client_turn_id = kwargs.get("client_turn_id")
        consult_step_id = (
            f"consult-{target_agent_name or 'unknown'}-{int(time.time() * 1000)}"
            if task_run_id is not None
            else None
        )

        preflight = run_consult_agent_preflight_action(
            target_agent_name=target_agent_name,
            current_agent_name=current_agent_name,
            chatroom_id=chatroom_id,
        )
        if not preflight["ok"]:
            return str(preflight["error"])
        target_agent_type = str(preflight["target_agent_type"])
        current_agent_type = str(preflight["current_agent_type"])

        from models.database import (
            get_db,
        )
        from llm.client import get_llm_client_for_agent

        db = next(get_db())
        try:
            target_state = dict(preflight["target_state"])
            target_db_agent = target_state["target_db_agent"]
            chatroom = target_state["chatroom"]
            chatroom_id_value = getattr(chatroom, "id", None) or chatroom_id or 0

            try:
                llm_client = get_llm_client_for_agent(target_agent_type)
            except RuntimeError as exc:
                return f"[consult_agent] Error: Cannot get LLM for '{target_agent_type}': {exc}"

            from chatrooms.manager import chatroom_manager

            prompt_state = await build_consult_agent_prompt_state(
                db=db,
                target_db_agent=target_db_agent,
                chatroom=chatroom,
                current_agent_name=current_agent_name,
                question=question,
                include_context=include_context,
                recent_messages_loader=chatroom_manager.get_messages,
            )
            project = prompt_state["project"]
            own_memories = prompt_state["own_memories"]
            history_messages = prompt_state["history_messages"]
            history_summary = prompt_state["history_summary"]
            current_input_messages = prompt_state["current_input_messages"]
            query_input = prompt_state["query_input"]
            runtime_note = prompt_state["runtime_note"]
            task_fragments = prompt_state["task_fragments"]
            prompt_profile = build_consult_agent_prompt_profile(
                target_db_agent=target_db_agent,
                runtime_note=runtime_note,
                own_memories=own_memories,
                project=project,
                chatroom=chatroom,
                history_summary=history_summary,
                task_fragments=task_fragments,
                include_context=include_context,
                skill_id_resolver=_agent_skill_ids,
                memory_line_resolver=_memory_context_lines,
            )

            messages = build_consult_agent_messages(
                target_db_agent=target_db_agent,
                model_id=getattr(llm_client, "model", ""),
                history_messages=history_messages,
                current_input_messages=current_input_messages,
                developer_fragments=prompt_profile["developer_fragments"],
                user_fragments=prompt_profile["user_fragments"],
                fallback_name_resolver=agent_name_of,
            )

            if consult_step_id is not None:
                _record_consult_subagent_dispatched(
                    db=db,
                    task_run_id=task_run_id if isinstance(task_run_id, int) else None,
                    client_turn_id=str(client_turn_id or "") or None,
                    step_id=consult_step_id,
                    target_db_agent=target_db_agent,
                    target_agent_type=target_agent_type,
                    current_agent_name=current_agent_name,
                    question=question,
                )
                await _store_consult_runtime_card(
                    chatroom_id_value,
                    {
                        "type": "consult_call",
                        "source": "consult_agent",
                        "status": "running",
                        "agent": current_agent_name,
                        "target_agent": target_agent_type,
                        "question_preview": question[:500],
                        "consult_step_id": consult_step_id,
                        "task_run_id": task_run_id if isinstance(task_run_id, int) else None,
                        "client_turn_id": str(client_turn_id or "") or None,
                        "available_actions": ["wait", "cancel"],
                    },
                )

            logger.info(f"[consult_agent] {current_agent_type} -> {target_agent_type}: {question[:80]}")
            response = await llm_client.chat(messages, temperature=0.7, max_tokens=1500)

            if consult_step_id is not None:
                _record_consult_subagent_completed(
                    db=db,
                    task_run_id=task_run_id if isinstance(task_run_id, int) else None,
                    client_turn_id=str(client_turn_id or "") or None,
                    step_id=consult_step_id,
                    target_db_agent=target_db_agent,
                    target_agent_type=target_agent_type,
                    current_agent_name=current_agent_name,
                    question=question,
                    response=response,
                )
                await _store_consult_runtime_card(
                    chatroom_id_value,
                    {
                        "type": "consult_call",
                        "source": "consult_agent",
                        "status": "completed",
                        "agent": current_agent_name,
                        "target_agent": target_agent_type,
                        "question_preview": question[:500],
                        "response_preview": str(response or "")[:500],
                        "consult_step_id": consult_step_id,
                        "task_run_id": task_run_id if isinstance(task_run_id, int) else None,
                        "client_turn_id": str(client_turn_id or "") or None,
                        "available_actions": ["close"],
                    },
                )

            logger.info(f"[consult_agent] {target_agent_type} responded ({len(response)} chars)")
            return run_consult_agent_response_action(
                db=db,
                chatroom=chatroom,
                question=question,
                response=response,
                current_agent_type=current_agent_type,
                target_agent_type=target_agent_type,
                target_agent_role=target_db_agent.role,
                consult_step_id=consult_step_id,
            )
        except Exception as exc:
            if consult_step_id is not None:
                _record_consult_subagent_failed(
                    db=db,
                    task_run_id=task_run_id if isinstance(task_run_id, int) else None,
                    client_turn_id=str(client_turn_id or "") or None,
                    step_id=consult_step_id,
                    target_agent_type=target_agent_type,
                    current_agent_name=current_agent_name,
                    question=question,
                    error=str(exc),
                )
                await _store_consult_runtime_card(
                    chatroom_id if chatroom_id else 0,
                    {
                        "type": "consult_call",
                        "source": "consult_agent",
                        "status": "failed",
                        "agent": current_agent_name,
                        "target_agent": target_agent_type,
                        "question_preview": question[:500],
                        "error": str(exc)[:1000],
                        "consult_step_id": consult_step_id,
                        "task_run_id": task_run_id if isinstance(task_run_id, int) else None,
                        "client_turn_id": str(client_turn_id or "") or None,
                        "available_actions": [],
                    },
                )
            logger.error(f"[consult_agent] Error: {exc}")
            return f"[consult_agent] Error consulting agent '{target_agent_type}': {exc}"
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
