# -*- coding: utf-8 -*-
"""Shared chat prompt assembly helpers."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from agents.identity import agent_name_of
from agents.registry import get_registry
from config import settings
from models.database import Chatroom
from services.context_builder import (
    ContextSelector,
    assemble_messages,
    build_base_system_prompt,
    build_history_summary_fragment,
    build_operating_developer_context,
    build_recent_history,
    build_runtime_user_fragments,
    build_stage_developer_context,
    build_turn_state_developer_fragments,
    build_turn_state_user_fragments,
)
from services.task_state import build_task_state, build_task_state_fragments
from skills import load_skill_registry

logger = logging.getLogger("catown.chat_prompt_builder")


_SELECTOR_PROFILES: dict[str, dict[str, Any]] = {
    "chat_interactive": {
        "allowed_visibilities": None,
        "allowed_scopes": None,
        "max_fragments": 12,
        "max_tokens_cap": 3200,
    },
    "fallback_chat": {
        "allowed_visibilities": None,
        "allowed_scopes": None,
        "max_fragments": 10,
        "max_tokens_cap": 2600,
    },
    "query_agent": {
        "allowed_visibilities": None,
        "allowed_scopes": None,
        "max_fragments": 11,
        "max_tokens_cap": 2200,
    },
}


def agent_base_system_prompt(agent: Any, fallback_name: str, fallback_role: str = "assistant") -> str:
    if agent is None:
        return f"You are {fallback_name}, a helpful AI collaborator."
    return build_base_system_prompt(
        agent,
        fallback_name=fallback_name,
        fallback_role=fallback_role or getattr(agent, "role", "assistant"),
    )


def agent_skill_ids(agent: Any) -> List[str]:
    if agent is None:
        return []
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


def team_member_lines(agents: List[Any]) -> List[str]:
    return [
        f"- **{agent_name_of(agent)}** (type: `{getattr(agent, 'type', agent_name_of(agent))}`, role: {getattr(agent, 'role', 'assistant')})"
        for agent in agents
    ]


def memory_context_lines(db: Session, target_agent: Any, agents: List[Any]) -> List[str]:
    if target_agent is None or not getattr(target_agent, "id", None):
        return []

    from models.database import Memory

    lines: List[str] = []
    own_memories = (
        db.query(Memory)
        .filter(Memory.agent_id == target_agent.id)
        .order_by(Memory.importance.desc(), Memory.created_at.desc())
        .limit(8)
        .all()
    )
    for mem in own_memories:
        ts = mem.created_at.strftime("%Y-%m-%d %H:%M") if mem.created_at else "unknown"
        lines.append(f"- [{ts}] [importance={mem.importance}] {mem.content[:200]}")

    other_agent_ids = [agent.id for agent in agents if agent.id != target_agent.id]
    if other_agent_ids:
        shared_memories = (
            db.query(Memory)
            .filter(Memory.agent_id.in_(other_agent_ids), Memory.importance >= 7)
            .order_by(Memory.importance.desc(), Memory.created_at.desc())
            .limit(5)
            .all()
        )
        for mem in shared_memories:
            ts = mem.created_at.strftime("%Y-%m-%d %H:%M") if mem.created_at else "unknown"
            source_agent = next((agent for agent in agents if agent.id == mem.agent_id), None)
            source_name = agent_name_of(source_agent) if source_agent else "unknown"
            lines.append(f"- [{ts}] [{source_name}] {mem.content[:200]}")

    return lines


def assemble_chat_messages(
    *,
    db: Session,
    agent: Any,
    agent_name: str,
    model_id: str = "",
    chatroom: Any,
    project: Any,
    agents: Optional[List[Any]] = None,
    recent_messages: Optional[List[Any]] = None,
    user_message: str = "",
    available_tools: Optional[List[str]] = None,
    tool_guidance: str = "",
    history_limit: int = 10,
    history_visibility: str = "all",
    target_agent_name: Optional[str] = None,
    prefix_assistant_name: bool = False,
    standalone_note: str = "",
    extra_context: str = "",
    turn_state: Any = None,
    selector_profile: str = "chat_interactive",
) -> List[Dict[str, Any]]:
    agents = agents or []
    base_system_prompt = agent_base_system_prompt(
        agent,
        agent_name,
        getattr(agent, "role", "assistant") if agent else "assistant",
    )
    source_chatroom = None
    if chatroom and getattr(chatroom, "source_chatroom_id", None):
        source_chatroom = (
            db.query(Chatroom)
            .filter(Chatroom.id == chatroom.source_chatroom_id)
            .first()
        )

    history = build_recent_history(
        recent_messages or [],
        limit=history_limit,
        visibility=history_visibility,
        target_agent_name=target_agent_name,
        prefix_assistant_name=prefix_assistant_name,
    )
    history_summary = build_history_summary_fragment(
        recent_messages or [],
        keep_last=history_limit,
        visibility=history_visibility,
        target_agent_name=target_agent_name,
        prefix_assistant_name=prefix_assistant_name,
    )
    current_input: List[Dict[str, Any]] = []
    normalized_user = (user_message or "").strip()
    if normalized_user and not (
        history
        and history[-1].get("role") == "user"
        and str(history[-1].get("content") or "").strip() == normalized_user
    ):
        current_input.append({"role": "user", "content": user_message})
    if turn_state is not None:
        current_input.extend(turn_state.protocol_messages())

    developer_fragments = [
        build_operating_developer_context(
            agent_name=agent_name,
            agent_role=getattr(agent, "role", "") if agent else "assistant",
        ),
        build_stage_developer_context(
            tools=available_tools or [],
            skills_config=load_skill_registry(settings.SKILLS_DIR),
            agent_skills=agent_skill_ids(agent),
            tool_guidance=tool_guidance,
        ),
    ]
    developer_fragments.extend(build_turn_state_developer_fragments(turn_state))

    user_fragments = build_task_state_fragments(
        build_task_state(
            project=project,
            user_message=user_message,
        )
    )
    if history_summary is not None:
        user_fragments.append(history_summary)
    user_fragments.extend(build_runtime_user_fragments(
        project=project,
        chatroom=chatroom,
        source_chatroom=source_chatroom,
        standalone_note=standalone_note,
        team_members=team_member_lines(agents),
        memories=memory_context_lines(db, agent, agents),
        extra_context=extra_context,
    ))
    user_fragments.extend(build_turn_state_user_fragments(turn_state))

    selector = build_chat_context_selector(
        profile=selector_profile,
        agent_name=agent_name,
        model_id=model_id,
        base_system_prompt=base_system_prompt,
        history_messages=history,
        current_input_messages=current_input,
    )

    return assemble_messages(
        base_system_prompt=base_system_prompt,
        developer_fragments=developer_fragments,
        user_fragments=user_fragments,
        history_messages=history,
        current_input_messages=current_input,
        selector=selector,
    ).to_messages()


@lru_cache(maxsize=8)
def _load_agent_config_snapshot(config_path: str, modified_ns: int) -> Dict[str, Any]:
    with open(config_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _load_agent_config_data() -> Dict[str, Any]:
    config_path = Path(settings.AGENT_CONFIG_FILE)
    if not config_path.exists():
        return {}
    try:
        stat = config_path.stat()
        return _load_agent_config_snapshot(str(config_path.resolve()), stat.st_mtime_ns)
    except Exception as exc:
        logger.warning(f"Failed to load agent config for runtime card metadata: {exc}")
        return {}


def _context_window_from_provider(provider_data: Any, model_id: str) -> Optional[int]:
    if not isinstance(provider_data, dict):
        return None
    models = provider_data.get("models", [])
    if not isinstance(models, list):
        return None
    for model in models:
        if not isinstance(model, dict) or model.get("id") != model_id:
            continue
        context_window = model.get("contextWindow")
        if isinstance(context_window, (int, float)) and context_window > 0:
            return int(context_window)
    return None


def resolve_llm_context_window(agent_name: str, model_id: str) -> Optional[int]:
    if not model_id:
        return None

    registry = get_registry()
    registered_agent = registry.get(agent_name) if agent_name else None
    if registered_agent:
        try:
            model_info = registered_agent.get_model_info(model_id)
        except Exception:
            model_info = None
        if isinstance(model_info, dict):
            context_window = model_info.get("context_window")
            if isinstance(context_window, (int, float)) and context_window > 0:
                return int(context_window)

    config_data = _load_agent_config_data()
    if not config_data:
        return None

    agents_data = config_data.get("agents", {})
    if agent_name:
        context_window = _context_window_from_provider((agents_data.get(agent_name) or {}).get("provider"), model_id)
        if context_window:
            return context_window

    context_window = _context_window_from_provider((config_data.get("global_llm") or {}).get("provider"), model_id)
    if context_window:
        return context_window

    for agent_data in agents_data.values():
        context_window = _context_window_from_provider(agent_data.get("provider"), model_id)
        if context_window:
            return context_window

    return None


def build_chat_context_selector(
    *,
    profile: str = "chat_interactive",
    agent_name: str,
    model_id: str,
    base_system_prompt: str,
    history_messages: Optional[List[Dict[str, Any]]] = None,
    current_input_messages: Optional[List[Dict[str, Any]]] = None,
) -> ContextSelector:
    profile_config = selector_profile_config(profile)
    max_tokens_cap = profile_config.pop("max_tokens_cap", None)
    selector = ContextSelector.for_context_window(
        context_window=resolve_llm_context_window(agent_name, model_id),
        base_system_prompt=base_system_prompt,
        history_messages=history_messages,
        current_input_messages=current_input_messages,
        max_tokens=max_tokens_cap,
        **profile_config,
    )
    return selector


def selector_profile_config(profile: str) -> Dict[str, Any]:
    normalized_profile = str(profile or "chat_interactive").strip() or "chat_interactive"
    config = _SELECTOR_PROFILES.get(normalized_profile) or _SELECTOR_PROFILES["chat_interactive"]
    return dict(config)


def list_selector_profiles() -> List[str]:
    return list(_SELECTOR_PROFILES.keys())
