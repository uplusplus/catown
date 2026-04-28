# -*- coding: utf-8 -*-
"""Shared chat runtime preparation and prompt assembly helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from agents.identity import agent_name_of, agent_type_of
from chatrooms.manager import chatroom_manager
from llm.client import get_llm_client_for_agent
from services.chat_prompt_builder import assemble_chat_messages as shared_assemble_chat_messages
from services.turn_state import TurnContextState, build_turn_state_from_checkpoint_snapshot


@dataclass
class PreparedChatTurnRuntime:
    llm_client: Any
    agent_label: str
    recent_messages: List[Any]
    available_tools: List[str]
    tool_schemas: List[Dict[str, Any]]
    runtime_kwargs: Dict[str, Any]
    turn_state: TurnContextState


def build_tool_prompt(tool_names: List[str]) -> str:
    if not tool_names:
        return ""
    prompt = f"\n\nYou have access to the following tools: {', '.join(tool_names)}"
    if "skill_manager" in tool_names:
        prompt += (
            "\nTool guidance: when the user asks to install/add/download/import/enable/troubleshoot "
            "a skill, 技能, or skill marketplace, call skill_manager. Use action='marketplaces' to "
            "check configured marketplaces and CLI readiness. Use action='install' with marketplace "
            "and source to install a skill, for example marketplace='skillhub-cn' and source='graphify'. "
            "If the tool returns code='command_not_found', explain that the marketplace CLI is missing "
            "and direct the user to install or enable that marketplace CLI from the Skills configuration page."
        )
    return prompt


def assemble_runtime_chat_messages(
    *,
    db: Session,
    agent: Any,
    agent_name: str,
    model_id: str = "",
    chatroom: Any = None,
    project: Any = None,
    agents: Optional[List[Any]] = None,
    recent_messages: Optional[List[Any]] = None,
    user_message: str = "",
    available_tools: Optional[List[str]] = None,
    history_limit: int = 10,
    history_visibility: str = "all",
    target_agent_name: Optional[str] = None,
    prefix_assistant_name: bool = False,
    standalone_note: str = "",
    extra_context: str = "",
    turn_state: Optional[TurnContextState] = None,
    selector_profile: str = "chat_interactive",
    on_compaction: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> List[Dict[str, Any]]:
    tool_guidance = ""
    if available_tools:
        tool_guidance = build_tool_prompt(available_tools)
        if "When you need to use a tool" not in tool_guidance:
            tool_guidance += "\nWhen you need to use a tool, respond with a tool call and the system will execute it."
    return shared_assemble_chat_messages(
        db=db,
        agent=agent,
        agent_name=agent_name,
        model_id=model_id,
        chatroom=chatroom,
        project=project,
        agents=agents,
        recent_messages=recent_messages,
        user_message=user_message,
        available_tools=available_tools,
        tool_guidance=tool_guidance,
        history_limit=history_limit,
        history_visibility=history_visibility,
        target_agent_name=target_agent_name,
        prefix_assistant_name=prefix_assistant_name,
        standalone_note=standalone_note,
        extra_context=extra_context,
        turn_state=turn_state,
        selector_profile=selector_profile,
        on_compaction=on_compaction,
    )


def build_tool_runtime_kwargs(agent: Any, chatroom_id: int, project: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"chatroom_id": chatroom_id}
    if agent is not None and getattr(agent, "id", None) is not None:
        payload["agent_id"] = agent.id
        payload["agent_name"] = agent_name_of(agent)
    if project is not None and getattr(project, "id", None) is not None:
        payload["project_id"] = project.id
    return payload


async def prepare_chat_turn_runtime(
    *,
    agent: Any,
    chatroom_id: int,
    project: Any,
    checkpoint_snapshot: Optional[Dict[str, Any]] = None,
    previous_agent_work: str = "",
    inter_agent_messages: Optional[List[Dict[str, Any]]] = None,
    recent_message_limit: int = 10,
) -> PreparedChatTurnRuntime:
    from tools import tool_registry

    llm_client = get_llm_client_for_agent(agent_type_of(agent))
    recent_messages = await chatroom_manager.get_messages(chatroom_id, limit=max(1, recent_message_limit))
    turn_state = build_turn_state_from_checkpoint_snapshot(
        checkpoint_snapshot,
        previous_agent_work=previous_agent_work or "",
    )
    if inter_agent_messages:
        turn_state.add_inter_agent_messages(inter_agent_messages)
    return PreparedChatTurnRuntime(
        llm_client=llm_client,
        agent_label=agent_name_of(agent),
        recent_messages=recent_messages,
        available_tools=tool_registry.list_tools(),
        tool_schemas=tool_registry.get_schemas(),
        runtime_kwargs=build_tool_runtime_kwargs(agent, chatroom_id, project),
        turn_state=turn_state,
    )
