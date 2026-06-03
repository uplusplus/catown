# -*- coding: utf-8 -*-
"""Shared chat prompt assembly helpers."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

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
from services.model_context import (
    ModelContextMetadata,
    context_window_from_provider,
    resolve_model_context_metadata,
)
from services.runtime_event_helpers import should_emit_context_budget_event
from services.task_state import build_task_state, build_task_state_fragments
from skills import load_skill_registry

logger = logging.getLogger("catown.chat_prompt_builder")


_DEFAULT_SELECTOR_PROFILES: dict[str, dict[str, Any]] = {
    "chat_interactive": {
        "allowed_visibilities": None,
        "allowed_scopes": None,
        "max_fragments": 20,
        "max_tokens_cap": 8000,
        "max_tokens_cap_ratio": 0.03,
        "max_tokens_by_role": {
            "developer": 2500,
            "user": 5500,
        },
        "max_tokens_by_role_ratio": {
            "developer": 0.01,
            "user": 0.02,
        },
        "max_tokens_by_scope": {
            "session": 400,
            "run": 4000,
            "stage": 1200,
            "turn": 1200,
            "shared_fact": 600,
            "agent_private": 600,
        },
        "max_tokens_by_scope_ratio": {
            "session": 0.002,
            "run": 0.015,
            "stage": 0.005,
            "turn": 0.005,
            "shared_fact": 0.003,
            "agent_private": 0.003,
        },
    },
    "fallback_chat": {
        "allowed_visibilities": None,
        "allowed_scopes": None,
        "max_fragments": 15,
        "max_tokens_cap": 5000,
        "max_tokens_cap_ratio": 0.025,
        "max_tokens_by_role": {
            "developer": 1800,
            "user": 3200,
        },
        "max_tokens_by_role_ratio": {
            "developer": 0.008,
            "user": 0.017,
        },
        "max_tokens_by_scope": {
            "session": 300,
            "run": 2600,
            "stage": 800,
            "turn": 800,
            "shared_fact": 400,
            "agent_private": 400,
        },
        "max_tokens_by_scope_ratio": {
            "session": 0.002,
            "run": 0.012,
            "stage": 0.004,
            "turn": 0.004,
            "shared_fact": 0.002,
            "agent_private": 0.002,
        },
    },
    "consult_agent": {
        "allowed_visibilities": None,
        "allowed_scopes": None,
        "max_fragments": 16,
        "max_tokens_cap": 4500,
        "max_tokens_cap_ratio": 0.02,
        "max_tokens_by_role": {
            "developer": 1800,
            "user": 2700,
        },
        "max_tokens_by_role_ratio": {
            "developer": 0.008,
            "user": 0.012,
        },
        "max_tokens_by_scope": {
            "session": 300,
            "run": 2400,
            "stage": 700,
            "turn": 700,
            "shared_fact": 400,
            "agent_private": 300,
        },
        "max_tokens_by_scope_ratio": {
            "session": 0.002,
            "run": 0.010,
            "stage": 0.003,
            "turn": 0.003,
            "shared_fact": 0.002,
            "agent_private": 0.002,
        },
    },
}

_SELECTOR_PROFILE_FIELDS = {
    "allowed_visibilities",
    "allowed_scopes",
    "max_fragments",
    "max_tokens_cap",
    "max_tokens_cap_ratio",
    "max_tokens_by_role",
    "max_tokens_by_role_ratio",
    "max_tokens_by_scope",
    "max_tokens_by_scope_ratio",
    "truncate_to_budget",
    "min_tokens_for_truncation",
}


def _task_state_user_message_text(user_message: Any) -> str:
    if isinstance(user_message, str):
        return user_message
    if isinstance(user_message, list):
        text_parts: list[str] = []
        for item in user_message:
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "").strip().lower() != "text":
                continue
            text = item.get("text")
            if text is None:
                continue
            normalized = str(text).strip()
            if normalized:
                text_parts.append(normalized)
        return "\n\n".join(text_parts)
    return str(user_message or "")


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
    lines: List[str] = []
    for agent in agents:
        agent_type = getattr(agent, "agent_type", None) or getattr(agent, "type", agent_name_of(agent))
        line = f"- **{agent_name_of(agent)}** (type: `{agent_type}`, role: {getattr(agent, 'role', 'assistant')})"
        contract = _agent_runtime_contract(agent)
        if contract:
            details = _runtime_contract_line(contract)
            if details:
                line = f"{line} - runtime contract: {details}"
        lines.append(line)
    return lines


def _agent_runtime_contract(agent: Any) -> Dict[str, Any]:
    raw_config = getattr(agent, "config", None)
    if isinstance(raw_config, str):
        try:
            config_data = json.loads(raw_config or "{}")
        except (TypeError, json.JSONDecodeError):
            config_data = {}
    elif isinstance(raw_config, dict):
        config_data = raw_config
    else:
        config_data = {}
    metadata = config_data.get("metadata") if isinstance(config_data, dict) else {}
    if not isinstance(metadata, dict):
        metadata = getattr(agent, "metadata", None) if isinstance(getattr(agent, "metadata", None), dict) else {}
    contract = metadata.get("runtime_contract") if isinstance(metadata, dict) else None
    return contract if isinstance(contract, dict) else {}


def _runtime_contract_line(contract: Dict[str, Any]) -> str:
    parts: List[str] = []
    mode = str(contract.get("mode") or "").strip()
    owns = contract.get("owns")
    dispatch_tools = contract.get("dispatch_tools")
    status_tools = contract.get("status_tools")
    completion_rule = str(contract.get("completion_rule") or "").strip()
    if mode:
        parts.append(f"mode={mode}")
    if isinstance(owns, list) and owns:
        parts.append("owns=" + ",".join(str(item) for item in owns if item))
    if contract.get("must_dispatch_specialized_work") is True:
        parts.append("must_dispatch_specialized_work=true")
    if isinstance(dispatch_tools, list) and dispatch_tools:
        parts.append("dispatch_tools=" + ",".join(str(item) for item in dispatch_tools if item))
    if isinstance(status_tools, list) and status_tools:
        parts.append("status_tools=" + ",".join(str(item) for item in status_tools if item))
    if completion_rule:
        parts.append(f"completion_rule={completion_rule}")
    return "; ".join(parts)


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
    user_message: Any = "",
    available_tools: Optional[List[str]] = None,
    tool_schemas: Optional[List[Dict[str, Any]]] = None,
    tool_schemas_before_filter: Optional[List[Dict[str, Any]]] = None,
    tool_schema_filter: Optional[Dict[str, Any]] = None,
    tool_guidance: str = "",
    history_limit: int = 5,
    history_visibility: str = "all",
    target_agent_name: Optional[str] = None,
    prefix_assistant_name: bool = False,
    standalone_note: str = "",
    runtime_context: str = "",
    extra_context: str = "",
    turn_state: Any = None,
    selector_profile: str = "chat_interactive",
    on_compaction: Optional[Callable[[dict[str, Any]], None]] = None,
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
        summarize_threshold=history_limit * 2,  # ADR-028 Phase 2: three-tier compression
    )
    history_summary = build_history_summary_fragment(
        recent_messages or [],
        keep_last=history_limit,
        visibility=history_visibility,
        target_agent_name=target_agent_name,
        prefix_assistant_name=prefix_assistant_name,
    )
    current_input: List[Dict[str, Any]] = []
    normalized_user = user_message.strip() if isinstance(user_message, str) else ""
    should_append_user_message = False
    if isinstance(user_message, list):
        should_append_user_message = True
    elif normalized_user:
        should_append_user_message = not (
            history
            and history[-1].get("role") == "user"
            and str(history[-1].get("content") or "").strip() == normalized_user
        )
    if should_append_user_message:
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
            user_message=_task_state_user_message_text(user_message),
        )
    )
    if history_summary is not None:
        user_fragments.append(history_summary)
    user_fragments.extend(build_runtime_user_fragments(
        runtime_context=runtime_context,
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

    assembly = assemble_messages(
        base_system_prompt=base_system_prompt,
        developer_fragments=developer_fragments,
        user_fragments=user_fragments,
        history_messages=history,
        current_input_messages=current_input,
        tool_schemas=tool_schemas,
        tool_schemas_before_filter=tool_schemas_before_filter,
        tool_schema_filter=tool_schema_filter,
        selector=selector,
    )
    diagnostics = assembly.selector_diagnostics if isinstance(assembly.selector_diagnostics, dict) else {}
    if should_emit_context_budget_event(diagnostics) and on_compaction is not None:
        on_compaction(diagnostics)
    return assembly.to_messages()


@lru_cache(maxsize=8)
def _load_agent_config_snapshot(config_path: str, modified_ns: int) -> Dict[str, Any]:
    with open(config_path, "r", encoding="utf-8-sig") as handle:
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
    return context_window_from_provider(provider_data, model_id)


def resolve_llm_context_metadata(agent_name: str, model_id: str) -> Optional[ModelContextMetadata]:
    if not model_id:
        return None

    registry = get_registry()
    registered_agent = registry.get(agent_name) if agent_name else None
    registry_model_info: Optional[Dict[str, Any]] = None
    if registered_agent:
        try:
            model_info = registered_agent.get_model_info(model_id)
        except Exception:
            model_info = None
        if isinstance(model_info, dict):
            registry_model_info = model_info

    config_data = _load_agent_config_data()
    agents_data = config_data.get("agents", {}) if isinstance(config_data, dict) else {}
    agent_provider = (agents_data.get(agent_name) or {}).get("provider") if agent_name and isinstance(agents_data, dict) else None
    global_provider = (config_data.get("global_llm") or {}).get("provider") if isinstance(config_data, dict) else None
    peer_providers = [
        agent_data.get("provider")
        for agent_data in agents_data.values()
        if isinstance(agent_data, dict)
    ] if isinstance(agents_data, dict) else []
    return resolve_model_context_metadata(
        model_id=model_id,
        registry_model_info=registry_model_info,
        agent_provider=agent_provider,
        global_provider=global_provider,
        peer_providers=peer_providers,
    )


def resolve_llm_context_window(agent_name: str, model_id: str) -> Optional[int]:
    metadata = resolve_llm_context_metadata(agent_name, model_id)
    return metadata.context_window if metadata else None


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
    model_context = resolve_llm_context_metadata(agent_name, model_id)
    materialized_profile = materialize_selector_profile_config(profile_config, model_context)
    max_tokens_cap = materialized_profile.pop("max_tokens_cap", None)
    reserved_completion_tokens = materialized_profile.pop("reserved_completion_tokens", None)
    selector = ContextSelector.for_context_window(
        context_window=model_context.context_window if model_context else None,
        base_system_prompt=base_system_prompt,
        history_messages=history_messages,
        current_input_messages=current_input_messages,
        reserved_completion_tokens=reserved_completion_tokens,
        max_tokens=max_tokens_cap,
        **materialized_profile,
    )
    return selector


def materialize_selector_profile_config(
    profile_config: Dict[str, Any],
    model_context: Optional[ModelContextMetadata],
) -> Dict[str, Any]:
    materialized = dict(profile_config)
    input_window = _profile_input_window(model_context)
    if input_window is None:
        _drop_ratio_fields(materialized)
        return materialized

    # ADR-028 Phase 3: Minimum cap floor to prevent aggressive ratio on small models
    min_tokens_cap = 3200

    cap_ratio = _ratio(materialized.pop("max_tokens_cap_ratio", None))
    if cap_ratio is not None:
        ratio_cap = max(1, int(input_window * cap_ratio))
        configured_cap = _positive_int(materialized.get("max_tokens_cap"))
        # effective_cap: ratio-derived value, floored at min_tokens_cap
        effective_cap = max(ratio_cap, min_tokens_cap)
        # ADR-028 §9.6 fix: configured_cap is a floor, not a ceiling.
        # Use max() so the ratio-derived value never undercuts an explicit config.
        materialized["max_tokens_cap"] = max(configured_cap, effective_cap) if configured_cap else effective_cap

    _materialize_budget_ratio_map(materialized, "max_tokens_by_role", "max_tokens_by_role_ratio", input_window)
    _materialize_budget_ratio_map(materialized, "max_tokens_by_scope", "max_tokens_by_scope_ratio", input_window)
    if model_context and model_context.output_reserve:
        materialized["reserved_completion_tokens"] = model_context.output_reserve
    return materialized


def _profile_input_window(model_context: Optional[ModelContextMetadata]) -> Optional[int]:
    if model_context is None:
        return None
    return model_context.input_window or max(
        model_context.context_window - (model_context.output_reserve or 0),
        1,
    )


def _materialize_budget_ratio_map(
    materialized: Dict[str, Any],
    budget_key: str,
    ratio_key: str,
    input_window: int,
) -> None:
    ratio_map = materialized.pop(ratio_key, None)
    if not isinstance(ratio_map, dict):
        return
    configured = materialized.get(budget_key)
    configured_map = configured if isinstance(configured, dict) else {}
    budget: dict[str, int] = {}
    for key, raw_ratio in ratio_map.items():
        ratio = _ratio(raw_ratio)
        if ratio is None:
            continue
        ratio_budget = max(1, int(input_window * ratio))
        configured_budget = _positive_int(configured_map.get(key))
        # ADR-028 §9.6 fix: configured budget is a floor, not a ceiling.
        # Use max() so ratio-derived values never undercut explicit config.
        budget[str(key)] = max(configured_budget, ratio_budget) if configured_budget else ratio_budget
    if configured_map:
        for key, value in configured_map.items():
            if str(key) not in budget:
                parsed = _positive_int(value)
                if parsed:
                    budget[str(key)] = parsed
    if budget:
        materialized[budget_key] = budget


def _drop_ratio_fields(config: Dict[str, Any]) -> None:
    for key in ("max_tokens_cap_ratio", "max_tokens_by_role_ratio", "max_tokens_by_scope_ratio"):
        config.pop(key, None)


def _ratio(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return min(parsed, 1.0)


def _positive_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def selector_profile_config(profile: str) -> Dict[str, Any]:
    normalized_profile = str(profile or "chat_interactive").strip() or "chat_interactive"
    profiles = effective_selector_profiles()
    config = profiles.get(normalized_profile) or profiles["chat_interactive"]
    return dict(config)


def list_selector_profiles() -> List[str]:
    return list(effective_selector_profiles().keys())


def default_selector_profiles() -> Dict[str, Dict[str, Any]]:
    return {name: dict(config) for name, config in _DEFAULT_SELECTOR_PROFILES.items()}


def effective_selector_profiles(config_data: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    profiles = default_selector_profiles()
    payload = config_data if config_data is not None else _load_agent_config_data()
    context_data = payload.get("context") if isinstance(payload, dict) else None
    selector_profiles = (
        context_data.get("selector_profiles")
        if isinstance(context_data, dict) and isinstance(context_data.get("selector_profiles"), dict)
        else {}
    )
    for profile_name, override in selector_profiles.items():
        if not isinstance(override, dict):
            continue
        normalized_name = str(profile_name or "").strip()
        if not normalized_name:
            continue
        base = dict(profiles.get(normalized_name) or {})
        for key, value in override.items():
            if key in _SELECTOR_PROFILE_FIELDS:
                base[key] = value
        if base:
            profiles[normalized_name] = _normalize_selector_profile_config(base)
    return profiles


def _normalize_selector_profile_config(config: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(config)
    for key in ("max_fragments", "max_tokens_cap", "min_tokens_for_truncation"):
        value = normalized.get(key)
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            normalized[key] = parsed
    for key in ("max_tokens_by_role", "max_tokens_by_scope"):
        value = normalized.get(key)
        if not isinstance(value, dict):
            continue
        budget: dict[str, int] = {}
        for budget_key, budget_value in value.items():
            try:
                parsed = int(budget_value)
            except (TypeError, ValueError):
                continue
            if parsed > 0:
                budget[str(budget_key)] = parsed
        normalized[key] = budget
    for key in ("max_tokens_cap_ratio",):
        ratio = _ratio(normalized.get(key))
        if ratio is not None:
            normalized[key] = ratio
    for key in ("max_tokens_by_role_ratio", "max_tokens_by_scope_ratio"):
        value = normalized.get(key)
        if not isinstance(value, dict):
            continue
        ratios: dict[str, float] = {}
        for ratio_key, ratio_value in value.items():
            ratio = _ratio(ratio_value)
            if ratio is not None:
                ratios[str(ratio_key)] = ratio
        normalized[key] = ratios
    if "truncate_to_budget" in normalized:
        normalized["truncate_to_budget"] = bool(normalized["truncate_to_budget"])
    return normalized
