# -*- coding: utf-8 -*-
"""Shared chat runtime preparation and prompt assembly helpers."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable, Dict, List, Mapping, Optional

from sqlalchemy.orm import Session

from agents.identity import agent_name_of, agent_type_of
from chatrooms.manager import chatroom_manager
from llm.client import get_llm_client_for_agent
from services.chat_prompt_builder import assemble_chat_messages as shared_assemble_chat_messages
from services.turn_state import TurnContextState, build_turn_state_from_checkpoint_snapshot


_TOOL_KEYWORD_HINTS: dict[str, list[str]] = {
    "run_shell": ["pytest", "test", "shell", "command", "terminal", "bash", "run "],
    "execute_code": ["execute code", "python", "javascript", "node", "script"],
    "read_file": ["read file", "open file", "inspect file"],
    "write_file": ["write file", "save file", "update file", "edit file"],
    "search_files": ["search code", "grep", "find text", "search files"],
    "list_files": ["list files", "show files", "workspace tree"],
    "list_agents": ["list agents", "available agents", "who can join", "invite"],
    "list_collaborators": ["list collaborators", "who is in this room", "team members"],
    "delegate_task": ["delegate", "assign", "have tester", "have developer", "ask tester", "ask developer"],
    "query_agent": ["ask architect", "ask tester", "ask developer", "immediate answer"],
    "send_direct_message": ["notify", "tell ", "message ", "ping "],
    "skill_manager": ["skill", "技能", "marketplace", "install skill", "enable skill"],
    "github_manager": ["github", "pull request", "repo", "branch", "tag", "release"],
    "browser": ["browser", "web page", "click", "fill", "navigate"],
    "screenshot": ["screenshot", "capture page", "screen capture"],
}


@dataclass
class PreparedChatTurnRuntime:
    llm_client: Any
    agent_label: str
    recent_messages: List[Any]
    available_tools: List[str]
    tool_schemas: List[Dict[str, Any]]
    tool_policy_pack: Dict[str, Any]
    runtime_kwargs: Dict[str, Any]
    turn_state: TurnContextState


def build_tool_prompt(
    tool_names: List[str],
    *,
    tool_policy_pack: Optional[Mapping[str, Any]] = None,
    user_message: str = "",
) -> str:
    if not tool_names:
        return ""
    policies = {
        str(item.get("name") or "").strip(): item
        for item in list((tool_policy_pack or {}).get("tool_policies") or [])
        if isinstance(item, Mapping) and str(item.get("name") or "").strip()
    }
    hints = []
    guides = []
    fulls = []
    related_tools = _related_tools_for_message(tool_names, user_message)
    for tool_name in tool_names:
        policy = policies.get(tool_name) or {}
        description = str(policy.get("description") or "").strip()
        risk_level = str(policy.get("risk_level") or "").strip()
        approval = policy.get("approval") if isinstance(policy.get("approval"), Mapping) else {}
        approval_kind = str(approval.get("kind") or "").strip()
        hint = tool_name
        if description:
            hint = f"{tool_name}: {description}"
        if risk_level or approval_kind:
            meta = " · ".join(part for part in [risk_level, approval_kind] if part)
            if meta:
                hint = f"{hint} ({meta})"
        hints.append(f"- {hint}")

        if tool_name == "skill_manager":
            guides.append(
                "skill_manager: when the user asks to install/add/download/import/enable/troubleshoot a skill, 技能, or skill marketplace, "
                "call skill_manager. Use action='marketplaces' to check configured marketplaces and CLI readiness. "
                "Use action='install' with marketplace and source to install a skill, for example marketplace='skillhub-cn' and source='graphify'. "
                "If the tool returns code='command_not_found', explain that the marketplace CLI is missing and direct the user to install or enable that marketplace CLI from the Skills configuration page."
            )
        elif tool_name in {"delegate_task", "query_agent", "send_direct_message", "check_task_status", "list_collaborators", "list_agents"}:
            if description:
                guides.append(description)

        if tool_name in related_tools and description:
            fulls.append(f"{tool_name}: {description}")

    parts = [f"\n\nYou have access to the following tools: {', '.join(tool_names)}"]
    if hints:
        parts.append("\n## Tool Hints\n" + "\n".join(hints))
    if guides:
        parts.append("\n## Active Tool Guides\n" + "\n\n".join(guides))
    if fulls:
        parts.append("\n## Relevant Tool Details\n" + "\n\n".join(fulls))
    return "".join(parts)


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
    tool_policy_pack: Optional[Mapping[str, Any]] = None,
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
        tool_guidance = build_tool_prompt(available_tools, tool_policy_pack=tool_policy_pack, user_message=user_message)
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
        tool_policy_pack=tool_registry.get_policy_pack(tool_registry.list_tools()),
        runtime_kwargs=build_tool_runtime_kwargs(agent, chatroom_id, project),
        turn_state=turn_state,
    )


def _related_tools_for_message(tool_names: List[str], user_message: str) -> set[str]:
    normalized_message = re.sub(r"\s+", " ", str(user_message or "").strip().lower())
    if not normalized_message:
        return set()
    related = set()
    for tool_name in tool_names:
        for needle in _TOOL_KEYWORD_HINTS.get(tool_name, []):
            if needle in normalized_message:
                related.add(tool_name)
                break
    return related
