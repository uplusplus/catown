# -*- coding: utf-8 -*-
"""
API 路由 - 主要端点
"""
import logging
import re
import json
import os
import asyncio
import socket
import shutil
import subprocess
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator

from agents.identity import (
    DEFAULT_AGENT_TYPE,
    agent_name_of,
    default_agent_name,
    find_agent_by_type,
    is_legacy_default_agent_name,
    legacy_default_agent_names,
    normalize_agent_type,
)
from models.database import (
    get_db,
    Agent,
    Project,
    Chatroom,
    AgentAssignment,
    Message,
    TaskRun,
    TaskRunEvent,
    ApprovalQueueItem,
    SessionLocal,
    Base,
)
from pipeline.engine import pipeline_engine
from agents.registry import get_registry
from agents.core import Agent as AgentInstance
from chatrooms.manager import chatroom_manager
from llm.client import get_llm_client_for_agent, get_default_llm_client, clear_client_cache
from config import settings
from monitoring import monitor_network_buffer
from skills import import_skill_from_marketplace, list_marketplaces, load_skill_registry, set_marketplace_enabled
from services.monitor_projection import (
    resolve_chatroom_project as monitor_resolve_chatroom_project,
    serialize_monitor_message_item,
    serialize_monitor_runtime_item,
)
from services.context_builder import (
    ContextSelector,
    assemble_messages,
    build_base_system_prompt,
    build_operating_developer_context,
    build_recent_history,
    build_runtime_user_fragments,
    build_stage_developer_context,
    build_turn_state_developer_fragments,
    build_turn_state_user_fragments,
)
from services.chat_prompt_builder import (
    agent_base_system_prompt as shared_agent_base_system_prompt,
    assemble_chat_messages as shared_assemble_chat_messages,
    build_chat_context_selector as shared_build_chat_context_selector,
    memory_context_lines as shared_memory_context_lines,
    team_member_lines as shared_team_member_lines,
)
from services.orchestration_scheduler import (
    DEFAULT_SIDECAR_AGENT_TYPES,
    OrchestrationRuntimeQueue,
    build_orchestration_schedule,
)
from services.turn_state import TurnContextState, build_tool_result_record, normalize_tool_call
from services.session_service import SessionService
from services.run_ledger import (
    append_task_event,
    complete_task_run,
    create_task_run,
    get_task_run,
    serialize_task_run_detail,
    serialize_task_run_summary,
    update_task_run,
)
from services.runner_lifecycle import (
    complete_agent_turn as record_agent_turn_completed,
    record_tool_round as record_runner_tool_round,
    start_agent_turn as record_agent_turn_started,
)
from services.approval_queue import (
    get_approval_queue_item,
    list_approval_queue_items,
    resolve_approval_queue_item,
    serialize_approval_queue_item,
)
from services.tool_governance import tool_result_succeeded as shared_tool_result_succeeded
from services.runner_policy import (
    compile_orchestration_run_policy,
    compile_single_agent_run_policy,
    find_stage_policy,
)
from services.stream_turn_executor import iter_stream_turn_events
from services.nonstream_turn_executor import execute_non_stream_turn_loop

logger = logging.getLogger("catown.api")

MAX_TOOL_ITERATIONS = 50
RECOVERABLE_ORCHESTRATION_RUN_KINDS = {
    "multi_agent_orchestration",
    "multi_agent_orchestration_stream",
}
RECOVERY_LEASE_SECONDS = max(60, int(os.getenv("CATOWN_RECOVERY_LEASE_SECONDS", "900")))
RECOVERY_INSTANCE_ID = (
    os.getenv("CATOWN_INSTANCE_ID")
    or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:12]}"
)


@dataclass
class TaskRunRecoveryResult:
    task_run_id: int
    resumed: bool
    reason: str
    status: Optional[str] = None
    detail: Optional[str] = None
    owner: Optional[str] = None
    lease_expires_at: Optional[datetime] = None


def _build_tool_prompt(tool_names: List[str]) -> str:
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


class LLMConfigModel(BaseModel):
    """LLM 配置验证模型"""
    api_key: str
    base_url: Optional[str] = "https://api.openai.com/v1"
    model: str = "gpt-3.5-turbo"
    temperature: float = 0.7
    max_tokens: int = 2000

    @field_validator('api_key')
    @classmethod
    def api_key_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError('api_key cannot be empty')
        return v

    @field_validator('base_url')
    @classmethod
    def validate_url(cls, v):
        if v and not v.startswith(('http://', 'https://')):
            raise ValueError('base_url must start with http:// or https://')
        return v.rstrip('/') if v else v

    @field_validator('temperature')
    @classmethod
    def validate_temperature(cls, v):
        if not 0 <= v <= 2:
            raise ValueError('temperature must be between 0 and 2')
        return v

    @field_validator('max_tokens')
    @classmethod
    def validate_max_tokens(cls, v):
        if not 1 <= v <= 100000:
            raise ValueError('max_tokens must be between 1 and 100000')
        return v


class OrchestrationConfigModel(BaseModel):
    """Runtime orchestration config validation model."""

    sidecar_agent_types: List[str] = Field(default_factory=list)

    @field_validator("sidecar_agent_types", mode="before")
    @classmethod
    def coerce_sidecar_agent_types(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, (set, tuple)):
            return list(value)
        return value

    @field_validator("sidecar_agent_types")
    @classmethod
    def normalize_sidecar_agent_types(cls, values: List[str]) -> List[str]:
        normalized = sorted({normalize_agent_type(str(value or "").strip()) for value in values if str(value or "").strip()})
        return normalized


router = APIRouter()


def _agent_type(agent: Optional[Agent]) -> str:
    return normalize_agent_type(agent.agent_type if agent else None)


def _find_db_agent_by_type(db: Session, agent_type: Optional[str]) -> Optional[Agent]:
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


# ==================== Agent 响应处理 ====================

def _find_mentioned_agent_name(message: str) -> Optional[str]:
    """Return the first @mentioned agent name in a message, if any."""
    mentioned_names = re.findall(r'@(\w+)', message or "")
    return normalize_agent_type(mentioned_names[0]) if mentioned_names else None


def _resolve_standalone_target_agent(db: Session, user_message: str) -> Optional[Agent]:
    """
    Resolve which agent should answer in a standalone chat.

    Prefer the first @mentioned agent; otherwise fall back to valet.
    """
    target_agent_name = _find_mentioned_agent_name(user_message)
    if target_agent_name:
        mentioned_agent = _find_db_agent_by_type(db, target_agent_name)
        if mentioned_agent:
            return mentioned_agent

    return _find_db_agent_by_type(db, DEFAULT_AGENT_TYPE)


def _list_global_agents(db: Session) -> List[Agent]:
    """List all available agents for standalone chat routing."""
    return db.query(Agent).order_by(Agent.agent_type.asc(), Agent.id.asc()).all()


def _snapshot_llm_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Create a JSON-safe snapshot of the exact messages sent to the LLM."""
    normalized: List[Dict[str, Any]] = []
    for item in messages or []:
        if not isinstance(item, dict):
            normalized.append({"value": str(item)})
            continue
        try:
            normalized.append(json.loads(json.dumps(item, ensure_ascii=False)))
        except TypeError:
            fallback: Dict[str, Any] = {}
            for key, value in item.items():
                try:
                    fallback[key] = json.loads(json.dumps(value, ensure_ascii=False))
                except TypeError:
                    fallback[key] = str(value)
            normalized.append(fallback)
    return normalized


def _chat_message_agent_name(message: Any) -> Optional[str]:
    agent_name = getattr(message, "agent_name", None)
    return agent_name if isinstance(agent_name, str) and agent_name else None


def _append_recent_llm_history(
    messages: List[Dict[str, Any]],
    recent_messages: List[Any],
    *,
    limit: int,
    visibility: str = "all",
    target_agent_name: Optional[str] = None,
    prefix_assistant_name: bool = False,
) -> None:
    for msg in recent_messages[-limit:]:
        agent_name = _chat_message_agent_name(msg)
        if getattr(msg, "message_type", "") == "user" or not agent_name:
            messages.append({"role": "user", "content": msg.content})
            continue

        if visibility == "target" and target_agent_name and agent_name != target_agent_name:
            continue

        assistant_content = f"[{agent_name}]: {msg.content}" if prefix_assistant_name else msg.content
        messages.append({"role": "assistant", "content": assistant_content})


def _append_current_user_message(messages: List[Dict[str, Any]], user_message: str) -> None:
    normalized_user = (user_message or "").strip()
    if not normalized_user:
        return

    if messages:
        last_message = messages[-1]
        if (
            isinstance(last_message, dict)
            and last_message.get("role") == "user"
            and str(last_message.get("content") or "").strip() == normalized_user
        ):
            return

    messages.append({"role": "user", "content": user_message})


def _format_json_block(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _tool_result_succeeded(result_text: str) -> bool:
    return shared_tool_result_succeeded(result_text)


def _message_client_turn_id(message_like: Any) -> Optional[str]:
    metadata: Dict[str, Any] = {}
    if hasattr(message_like, "metadata") and isinstance(getattr(message_like, "metadata"), dict):
        metadata = getattr(message_like, "metadata")
    elif hasattr(message_like, "metadata_json"):
        try:
            metadata = json.loads(getattr(message_like, "metadata_json") or "{}")
        except json.JSONDecodeError:
            metadata = {}

    client_turn_id = metadata.get("client_turn_id")
    return client_turn_id if isinstance(client_turn_id, str) and client_turn_id else None


def _preview_tool_calls(raw_tool_calls: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    previews: List[Dict[str, Any]] = []
    for index, tool_call in enumerate(raw_tool_calls or []):
        function = tool_call.get("function") if isinstance(tool_call, dict) else {}
        if not isinstance(function, dict):
            function = {}
        arguments = str(function.get("arguments") or "")
        previews.append(
            {
                "index": index,
                "id": tool_call.get("id") if isinstance(tool_call, dict) else None,
                "name": function.get("name") or "tool",
                "args_preview": arguments[:120],
            }
        )
    return previews


def _agent_base_system_prompt(agent: Optional[Agent], fallback_name: str, fallback_role: str = "assistant") -> str:
    return shared_agent_base_system_prompt(agent, fallback_name, fallback_role)


def _agent_skill_ids(agent: Optional[Agent]) -> List[str]:
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


def _team_member_lines(agents: List[Agent]) -> List[str]:
    return shared_team_member_lines(agents)


def _memory_context_lines(db: Session, target_agent: Optional[Agent], agents: List[Agent]) -> List[str]:
    return shared_memory_context_lines(db, target_agent, agents)


def _assemble_chat_messages(
    *,
    db: Session,
    agent: Optional[Agent],
    agent_name: str,
    model_id: str = "",
    chatroom: Optional[Chatroom],
    project: Optional[Project],
    agents: Optional[List[Agent]] = None,
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
) -> List[Dict[str, Any]]:
    tool_guidance = ""
    if available_tools:
        tool_guidance = _build_tool_prompt(available_tools)
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
    )


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


def _normalize_sidecar_agent_types(sidecar_agent_types: Any) -> List[str]:
    if sidecar_agent_types is None:
        return []
    if isinstance(sidecar_agent_types, str):
        raw_values = [sidecar_agent_types]
    elif isinstance(sidecar_agent_types, (list, tuple, set)):
        raw_values = list(sidecar_agent_types)
    else:
        raw_values = []
    return sorted({normalize_agent_type(str(value or "").strip()) for value in raw_values if str(value or "").strip()})


def _effective_orchestration_config(config_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = config_data if config_data is not None else _load_agent_config_data()
    orchestration_data = payload.get("orchestration") if isinstance(payload, dict) else None
    if isinstance(orchestration_data, dict) and "sidecar_agent_types" in orchestration_data:
        sidecar_agent_types = _normalize_sidecar_agent_types(orchestration_data.get("sidecar_agent_types"))
    else:
        sidecar_agent_types = sorted(DEFAULT_SIDECAR_AGENT_TYPES)
    return {"sidecar_agent_types": sidecar_agent_types}


def _configured_sidecar_agent_types(config_data: Optional[Dict[str, Any]] = None) -> set[str] | None:
    payload = config_data if config_data is not None else _load_agent_config_data()
    orchestration_data = payload.get("orchestration") if isinstance(payload, dict) else None
    if not isinstance(orchestration_data, dict) or "sidecar_agent_types" not in orchestration_data:
        return None
    return set(_normalize_sidecar_agent_types(orchestration_data.get("sidecar_agent_types")))


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


def _resolve_llm_context_window(agent_name: str, model_id: str) -> Optional[int]:
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


def _build_chat_context_selector(
    *,
    profile: str = "chat_interactive",
    agent_name: str,
    model_id: str,
    base_system_prompt: str,
    history_messages: Optional[List[Dict[str, Any]]] = None,
    current_input_messages: Optional[List[Dict[str, Any]]] = None,
) -> ContextSelector:
    return shared_build_chat_context_selector(
        profile=profile,
        agent_name=agent_name,
        model_id=model_id,
        base_system_prompt=base_system_prompt,
        history_messages=history_messages,
        current_input_messages=current_input_messages,
    )


def _build_llm_card_payload(
    *,
    agent_name: str,
    llm_client: Any,
    turn: int,
    duration_ms: int,
    system_prompt: str,
    prompt_messages: List[Dict[str, Any]],
    response_content: str,
    tool_call_previews: List[Dict[str, Any]],
    raw_tool_calls: Optional[List[Dict[str, Any]]] = None,
    usage: Optional[Dict[str, Any]] = None,
    finish_reason: Optional[str] = None,
    timings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    usage = usage or {}
    model = getattr(llm_client, "model", "")
    tokens_in = int(usage.get("prompt_tokens", 0) or 0)
    tokens_out = int(usage.get("completion_tokens", 0) or 0)
    tokens_total = int(usage.get("total_tokens", 0) or (tokens_in + tokens_out))
    context_window = _resolve_llm_context_window(agent_name, model)
    context_usage_ratio = None
    if context_window and tokens_in > 0:
        context_usage_ratio = round(tokens_in / context_window, 6)
    raw_response = {
        "role": "assistant",
        "content": response_content or "",
        "tool_calls": raw_tool_calls or [],
        "usage": usage,
        "finish_reason": finish_reason,
        "timings": timings or {},
    }
    return {
        "agent": agent_name,
        "model": model,
        "turn": turn,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "tokens_total": tokens_total,
        "context_window": context_window,
        "context_usage_ratio": context_usage_ratio,
        "duration_ms": duration_ms,
        "system_prompt": system_prompt or "",
        "prompt_messages": _format_json_block(prompt_messages),
        "response": response_content or "",
        "raw_response": _format_json_block(raw_response),
        "tool_calls": tool_call_previews or _preview_tool_calls(raw_tool_calls),
        "timings": timings or {},
    }

async def _trigger_standalone_assistant_response(
    db: Session,
    chatroom_id: int,
    user_message: str,
    client_turn_id: Optional[str] = None,
    task_run: Optional[TaskRun] = None,
    extra_context: str = "",
):
    """Generate a plain assistant reply for standalone chats."""
    chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
    if not chatroom:
        logger.debug("[ No chatroom found for standalone response")
        return

    assistant = _resolve_standalone_target_agent(db, user_message)

    if assistant:
        llm_client = get_llm_client_for_agent(_agent_type(assistant))
        assistant_name = _agent_type(assistant)
        assistant_id = assistant.id
    else:
        llm_client = get_default_llm_client()
        assistant_name = DEFAULT_AGENT_TYPE
        assistant_id = None
    standalone_policy = _build_single_agent_runner_policy(
        run_kind="standalone_assistant",
        agent_name=assistant_name,
        project_id=None,
        tool_names=[],
        streaming=False,
        standalone=True,
    )

    record_agent_turn_started(
        db,
        task_run,
        agent_name=assistant_name,
        summary=f"{assistant_name} started a standalone assistant turn.",
        payload={
            "client_turn_id": client_turn_id,
            "stage_policy": standalone_policy.stages[0].to_payload() if standalone_policy.stages else None,
        },
    )

    recent_messages = await chatroom_manager.get_messages(chatroom_id, limit=20)
    context_messages = _assemble_chat_messages(
        db=db,
        agent=assistant,
        agent_name=assistant_name,
        model_id=getattr(llm_client, "model", ""),
        chatroom=chatroom,
        project=None,
        agents=[assistant] if assistant else [],
        recent_messages=recent_messages,
        user_message=user_message,
        history_limit=10,
        standalone_note="This is a standalone chat. Reply directly, be concise, and help the user explore before creating a project if needed.",
        extra_context=extra_context,
    )

    response_content = await llm_client.chat(context_messages, temperature=0.7, max_tokens=1200)
    if not response_content:
        logger.debug("[ Standalone assistant returned empty response")
        return

    agent_response = await chatroom_manager.send_message(
        chatroom_id=chatroom_id,
        agent_id=assistant_id,
        content=response_content,
        message_type="text",
        metadata=_message_metadata_with_turn(client_turn_id),
        agent_name=assistant_name,
    )
    await _publish_saved_chat_message(
        db,
        chatroom_id,
        message_id=agent_response.id,
        content=response_content,
        agent_name=assistant_name,
        message_type="text",
        created_at=agent_response.created_at,
        metadata=_message_metadata_with_turn(client_turn_id),
    )
    record_agent_turn_completed(
        db,
        task_run,
        agent_name=assistant_name,
        message_id=agent_response.id,
        response_content=response_content,
        summary=f"{assistant_name} completed the standalone turn.",
    )
    complete_task_run(
        db,
        task_run,
        summary=_compact_runtime_text(response_content, limit=280),
    )

    if assistant_id and len(response_content) > 30:
        asyncio.create_task(_extract_memories(
            agent_id=assistant_id,
            agent_name=assistant_name,
            user_message=user_message,
            agent_response=response_content,
        ))


async def _stream_standalone_assistant_response(
    db: Session,
    chatroom_id: int,
    user_message: str,
    sse_json,
    client_turn_id: Optional[str] = None,
    task_run: Optional[TaskRun] = None,
):
    """Stream a plain assistant reply for standalone chats."""
    chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
    if not chatroom:
        yield f"data: {sse_json.dumps({'type': 'error', 'error': 'Chatroom not found'})}\n\n"
        return

    assistant = _resolve_standalone_target_agent(db, user_message)

    if assistant:
        llm_client = get_llm_client_for_agent(_agent_type(assistant))
        assistant_name = _agent_type(assistant)
        assistant_label = agent_name_of(assistant)
        assistant_id = assistant.id
    else:
        llm_client = get_default_llm_client()
        assistant_name = DEFAULT_AGENT_TYPE
        assistant_label = default_agent_name(DEFAULT_AGENT_TYPE)
        assistant_id = None
    standalone_stream_policy = _build_single_agent_runner_policy(
        run_kind="standalone_assistant_stream",
        agent_name=assistant_name,
        project_id=None,
        tool_names=[],
        streaming=True,
        standalone=True,
    )

    record_agent_turn_started(
        db,
        task_run,
        agent_name=assistant_name,
        summary=f"{assistant_name} started a standalone streaming turn.",
        payload={
            "client_turn_id": client_turn_id,
            "stage_policy": (
                standalone_stream_policy.stages[0].to_payload()
                if standalone_stream_policy.stages
                else None
            ),
        },
    )

    recent_messages = await chatroom_manager.get_messages(chatroom_id, limit=20)
    final_content = ""

    def _assemble_standalone_stream_messages(current_turn_state: TurnContextState) -> List[Dict[str, Any]]:
        return _assemble_chat_messages(
            db=db,
            agent=assistant,
            agent_name=assistant_name,
            model_id=getattr(llm_client, "model", ""),
            chatroom=chatroom,
            project=None,
            agents=[assistant] if assistant else [],
            recent_messages=recent_messages,
            user_message=user_message,
            history_limit=10,
            standalone_note="This is a standalone chat. Reply directly, be concise, and help the user explore before creating a project if needed.",
            turn_state=current_turn_state,
        )

    async def _execute_standalone_stream_tool(tool_name, tool_args, tool_args_str, tool_call_id, tool_index, turn_index):
        raise RuntimeError(f"Standalone assistant stream cannot execute tool '{tool_name}'")

    def _build_standalone_stream_llm_card(frame, response_content, raw_tool_calls, tool_call_previews, raw_event):
        return _build_llm_card_payload(
            agent_name=assistant_label,
            llm_client=llm_client,
            turn=frame.turn_index,
            duration_ms=int((raw_event.get("timings", {}) or {}).get("completed_ms") or ((time.time() - frame.llm_started_at) * 1000)),
            system_prompt=frame.system_prompt,
            prompt_messages=frame.prompt_snapshot,
            response_content=response_content,
            tool_call_previews=tool_call_previews,
            raw_tool_calls=raw_tool_calls,
            usage=raw_event.get("usage"),
            finish_reason=raw_event.get("finish_reason"),
            timings=raw_event.get("timings"),
        )

    try:
        async for event in iter_stream_turn_events(
            llm_client=llm_client,
            tools=None,
            turn_state=TurnContextState(),
            agent_name=assistant_name,
            client_turn_id=client_turn_id,
            assemble_messages=_assemble_standalone_stream_messages,
            execute_tool=_execute_standalone_stream_tool,
            build_llm_runtime_card=_build_standalone_stream_llm_card,
            snapshot_messages=_snapshot_llm_messages,
            preview_tool_calls=_preview_tool_calls,
            format_prompt_messages=_format_json_block,
            tool_result_success=_tool_result_succeeded,
            max_turns=1,
        ):
            if event["type"] == "runtime_card":
                payload = dict(event["payload"])
                payload["type"] = event["card_type"]
                payload["source"] = "chatroom"
                if client_turn_id:
                    payload["client_turn_id"] = client_turn_id
                await _store_runtime_card(chatroom_id, payload)
                yield f"data: {sse_json.dumps(_public_runtime_card_payload(payload), ensure_ascii=False)}\n\n"
                continue

            if event["type"] == "turn_complete":
                final_content = event.get("content") or ""
                continue

            yield f"data: {sse_json.dumps(event, ensure_ascii=False)}\n\n"
    except Exception as exc:
        append_task_event(
            db,
            task_run,
            "task_run_failed",
            agent_name=assistant_name,
            summary=f"Standalone stream failed: {exc}",
            payload={"error": str(exc)},
        )
        complete_task_run(db, task_run, status="failed", summary=str(exc))
        saved = await _persist_stream_failure(
            db,
            chatroom_id=chatroom_id,
            client_turn_id=client_turn_id,
            error_message=str(exc),
            agent_name=assistant_name,
            agent_id=assistant_id,
            detail=traceback.format_exc(),
        )
        yield f"data: {sse_json.dumps({'type': 'done', 'agent_name': assistant_name, 'message_id': saved.id, 'client_turn_id': client_turn_id}, ensure_ascii=False)}\n\n"
        return

    if not final_content:
        final_content = "(Agent returned empty response)"

    agent_response = await chatroom_manager.send_message(
        chatroom_id=chatroom_id,
        agent_id=assistant_id,
        content=final_content,
        message_type="text",
        metadata=_message_metadata_with_turn(client_turn_id),
        agent_name=assistant_name,
    )
    await _publish_saved_chat_message(
        db,
        chatroom_id,
        message_id=agent_response.id,
        content=final_content,
        agent_name=assistant_name,
        message_type="text",
        created_at=agent_response.created_at,
        metadata=_message_metadata_with_turn(client_turn_id),
    )
    record_agent_turn_completed(
        db,
        task_run,
        agent_name=assistant_name,
        message_id=agent_response.id,
        response_content=final_content,
        summary=f"{assistant_name} completed the standalone streaming turn.",
    )
    complete_task_run(
        db,
        task_run,
        summary=_compact_runtime_text(final_content, limit=280),
    )

    yield f"data: {sse_json.dumps({'type': 'done', 'agent_name': assistant_name, 'message_id': agent_response.id, 'client_turn_id': client_turn_id})}\n\n"

    if assistant_id and len(final_content) > 30:
        asyncio.create_task(_extract_memories(
            agent_id=assistant_id,
            agent_name=assistant_name,
            user_message=user_message,
            agent_response=final_content,
        ))


async def trigger_agent_response(
    chatroom_id: int,
    user_message: str,
    client_turn_id: Optional[str] = None,
    task_run_id: Optional[int] = None,
    extra_context: str = "",
):
    """触发 Agent 处理消息并生成响应（统一执行路径 + 工具结果回传 LLM）"""
    from models.database import get_db
    from tools import tool_registry
    from tools.file_operations import reset_active_workspace, set_active_workspace
    import json
    
    db = next(get_db())
    workspace_token = None
    task_run = None
    try:
        logger.debug(f"[ trigger_agent_response called: chatroom_id={chatroom_id}, message={user_message[:50]}...")
        
        # 1. 获取聊天室关联的项目
        chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
        if not chatroom:
            logger.debug(f"[ No chatroom found")
            return

        project = _resolve_chatroom_project(db, chatroom)
        task_run = get_task_run(db, task_run_id) if task_run_id else None
        if task_run is None:
            task_run = create_task_run(
                db,
                chatroom_id=chatroom_id,
                project_id=project.id if project else None,
                origin_message_id=None,
                client_turn_id=client_turn_id,
                run_kind="chat_turn",
                user_request=user_message,
                initiator="user",
            )
        workspace_token = set_active_workspace(project.workspace_path if project and project.workspace_path else None)
        if not project:
            mentioned_names = [normalize_agent_type(name) for name in re.findall(r'@(\w+)', user_message)] if '@' in user_message else []
            if len(mentioned_names) > 1:
                update_task_run(db, task_run, run_kind="multi_agent_orchestration")
                append_task_event(
                    db,
                    task_run,
                    "runtime_mode_selected",
                    summary="Selected standalone multi-agent orchestration mode.",
                    payload={"agents": mentioned_names, "project_id": None},
                )
                logger.info(f"[Collab] Standalone multi-agent orchestration triggered: {mentioned_names}")
                await _run_multi_agent_orchestration(
                    chatroom_id=chatroom_id,
                    project=None,
                    agents=_list_global_agents(db),
                    agent_names=mentioned_names,
                    user_message=user_message,
                    db=db,
                    client_turn_id=client_turn_id,
                    task_run=task_run,
                    extra_context=extra_context,
                )
                return
            standalone_target = _resolve_standalone_target_agent(db, user_message)
            standalone_agent_name = _agent_type(standalone_target) if standalone_target else DEFAULT_AGENT_TYPE
            update_task_run(db, task_run, run_kind="standalone_assistant", target_agent_name=standalone_agent_name)
            standalone_policy = _build_single_agent_runner_policy(
                run_kind="standalone_assistant",
                agent_name=standalone_agent_name,
                project_id=None,
                tool_names=[],
                streaming=False,
                standalone=True,
            )
            append_task_event(
                db,
                task_run,
                "runtime_mode_selected",
                summary="Selected standalone assistant mode.",
                payload={
                    "project_id": None,
                    "runner_policy": standalone_policy.to_payload(),
                },
            )
            await _trigger_standalone_assistant_response(
                db,
                chatroom_id,
                user_message,
                client_turn_id,
                task_run=task_run,
                extra_context=extra_context,
            )
            return
        
        logger.debug(f"[ Found project: {project.name}")

        # 2. 解析 @ 提及，检测多 Agent 协作
        mentioned_names = []
        if '@' in user_message:
            mentioned_names = [normalize_agent_type(name) for name in re.findall(r'@(\w+)', user_message)]

        # 3. 获取项目关联的 Agents（必须在多 Agent 检查之前）
        assignments = db.query(AgentAssignment).filter(
            AgentAssignment.project_id == project.id
        ).all()
        agent_ids = [a.agent_id for a in assignments]
        agents = db.query(Agent).filter(Agent.id.in_(agent_ids)).all()

        logger.debug(f"[ Found {len(agents)} agents for project")

        # 多 Agent 协作：多个 @mention 或包含协作关键词
        if len(mentioned_names) > 1:
            update_task_run(db, task_run, run_kind="multi_agent_orchestration")
            append_task_event(
                db,
                task_run,
                "runtime_mode_selected",
                summary="Selected project multi-agent orchestration mode.",
                payload={"agents": mentioned_names, "project_id": project.id},
            )
            logger.info(f"[Collab] Multi-agent orchestration triggered: {mentioned_names}")
            await _run_multi_agent_orchestration(
                chatroom_id=chatroom_id,
                project=project,
                agents=agents,
                agent_names=mentioned_names,
                user_message=user_message,
                db=db,
                client_turn_id=client_turn_id,
                task_run=task_run,
                extra_context=extra_context,
            )
            return

        target_agent_name = mentioned_names[0] if mentioned_names else None
        logger.debug(f"[ Target agent name: {target_agent_name}")

        # 4. 确定响应的 Agent
        target_agent = None
        if target_agent_name:
            target_agent = find_agent_by_type(agents, target_agent_name)

            # @mentioned agent 不在项目中 → 从全局注册表查找并自动分配
            if not target_agent:
                global_agent = _find_db_agent_by_type(db, target_agent_name)
                if global_agent:
                    logger.info(f"[Agent] Auto-assigning '{target_agent_name}' to project '{project.name}'")
                    assignment = AgentAssignment(project_id=project.id, agent_id=global_agent.id)
                    db.add(assignment)
                    db.commit()
                    target_agent = global_agent
                    agents.append(global_agent)

        if not target_agent:
            target_agent = find_agent_by_type(agents, DEFAULT_AGENT_TYPE) or (agents[0] if agents else None)

        if not target_agent:
            logger.debug(f"[ No target agent found")
            complete_task_run(db, task_run, status="failed", summary="No target agent resolved.")
            return

        logger.debug(f"[ Selected agent: {target_agent.name} (role: {target_agent.role})")
        available_tools = tool_registry.list_tools()
        single_agent_policy = _build_single_agent_runner_policy(
            run_kind="project_single_agent",
            agent_name=agent_name_of(target_agent),
            project_id=project.id,
            tool_names=available_tools,
            streaming=False,
            standalone=False,
        )
        update_task_run(
            db,
            task_run,
            run_kind="project_single_agent",
            target_agent_name=agent_name_of(target_agent),
        )
        append_task_event(
            db,
            task_run,
            "runtime_mode_selected",
            agent_name=agent_name_of(target_agent),
            summary="Selected project single-agent execution mode.",
            payload={
                "project_id": project.id,
                "target_agent_name": agent_name_of(target_agent),
                "runner_policy": single_agent_policy.to_payload(),
            },
        )

        # 注册 Agent 为协作者（如果尚未注册）
        from agents.collaboration import collaboration_coordinator, AgentCollaborator
        # 同时注册项目中所有 agent 为协作者（让 list_collaborators 能看到它们）
        for agent in agents:
            if agent.id not in collaboration_coordinator.collaborators:
                collaborator = AgentCollaborator(
                    agent_id=agent.id,
                    agent_name=_agent_type(agent),
                    chatroom_id=chatroom_id
                )
                collaboration_coordinator.register_collaborator(collaborator)
                logger.info(f"[Collab] Auto-registered collaborator: {_agent_type(agent)}")
        
        # 5. 获取该 Agent 的 LLM 客户端
        llm_client = get_llm_client_for_agent(_agent_type(target_agent))
        logger.debug(f"[ LLM client obtained for {_agent_type(target_agent)}: {llm_client.base_url}")

        visibility = chatroom.message_visibility or "all"
        recent_messages = await chatroom_manager.get_messages(chatroom_id, limit=20)
        turn_state = TurnContextState()
        tool_schemas = tool_registry.get_schemas()
        runtime_kwargs = _tool_runtime_kwargs(target_agent, chatroom_id, project)
        record_agent_turn_started(
            db,
            task_run,
            agent_name=agent_name_of(target_agent),
            summary=f"{agent_name_of(target_agent)} started working on the request.",
            payload={
                "target_agent_name": agent_name_of(target_agent),
                "client_turn_id": client_turn_id,
                "stage_policy": single_agent_policy.stages[0].to_payload() if single_agent_policy.stages else None,
            },
        )

        def _assemble_project_single_agent_messages(current_turn_state: TurnContextState) -> List[Dict[str, Any]]:
            return _assemble_chat_messages(
                db=db,
                agent=target_agent,
                agent_name=agent_name_of(target_agent),
                model_id=getattr(llm_client, "model", ""),
                chatroom=chatroom,
                project=project,
                agents=agents,
                recent_messages=recent_messages,
                user_message=user_message,
                available_tools=available_tools,
                history_limit=10,
                history_visibility="all" if visibility == "all" else "target",
                target_agent_name=agent_name_of(target_agent),
                prefix_assistant_name=visibility == "all",
                extra_context=extra_context,
                turn_state=current_turn_state,
            )

        logger.debug(
            f"[ Context messages: {len(_assemble_project_single_agent_messages(turn_state))} messages"
        )
        logger.info(
            f"[LLM] Calling LLM for agent: {_agent_type(target_agent)} with {len(tool_schemas)} tools available"
        )

        async def _execute_project_single_agent_tool(frame, tool_call):
            tool_name = tool_call["function"]["name"]
            tool_args_str = tool_call["function"].get("arguments", "{}")
            tool_args = json.loads(tool_args_str or "{}")
            logger.debug(f"[Tool] Executing: {tool_name} with args: {tool_args}")
            try:
                tool_result = await tool_registry.execute(
                    tool_name,
                    **tool_args,
                    **runtime_kwargs,
                )
                result_str = str(tool_result) if tool_result is not None else "(no output)"
                tool_success = True
                logger.debug(f"[Tool] Result: {result_str[:150]}...")
            except Exception as te:
                result_str = f"Error executing {tool_name}: {str(te)}"
                tool_success = False
                logger.debug(f"[Tool] Error: {te}")
            return build_tool_result_record(
                tool_call_id=tool_call.get("id"),
                tool_name=tool_name,
                arguments=tool_args_str,
                result=result_str,
                success=tool_success,
            )

        async def _on_project_single_agent_tool_round(frame, tool_results, current_turn_state):
            logger.info(f"[LLM] Loop iteration {frame.turn_index + 1}")
            logger.info(
                f"[LLM] Response received: {frame.content[:100] if frame.content else 'None'}..."
            )
            logger.info(f"[LLM] Tool calls: {frame.normalized_tool_calls}")
            record_runner_tool_round(
                db,
                task_run,
                agent_name=agent_name_of(target_agent),
                turn=frame.turn_index + 1,
                tool_names=[tool_call["function"]["name"] for tool_call in frame.normalized_tool_calls],
                tool_results=tool_results,
                summary=f"{agent_name_of(target_agent)} completed a tool round.",
            )

        response_content = await execute_non_stream_turn_loop(
            llm_client=llm_client,
            tools=tool_schemas,
            turn_state=turn_state,
            assemble_messages=_assemble_project_single_agent_messages,
            execute_tool_call=_execute_project_single_agent_tool,
            max_turns=MAX_TOOL_ITERATIONS,
            on_tool_round=_on_project_single_agent_tool_round,
        )

        if not response_content:
            logger.error(f"[ LLM returned empty response after all tool iterations")
            return
        
        # 9. 发送 Agent 响应
        agent_response = await chatroom_manager.send_message(
            chatroom_id=chatroom_id,
            agent_id=target_agent.id,
            content=response_content,
            message_type="text",
            metadata=_message_metadata_with_turn(client_turn_id),
            agent_name=agent_name_of(target_agent)
        )
        
        logger.debug(f"[ Agent response saved: id={agent_response.id}")
        
        await _publish_saved_chat_message(
            db,
            chatroom_id,
            message_id=agent_response.id,
            content=response_content,
            agent_name=agent_name_of(target_agent),
            message_type="text",
            created_at=agent_response.created_at,
            metadata=_message_metadata_with_turn(client_turn_id),
        )
        record_agent_turn_completed(
            db,
            task_run,
            agent_name=agent_name_of(target_agent),
            message_id=agent_response.id,
            response_content=response_content,
            summary=f"{agent_name_of(target_agent)} completed the turn.",
        )
        complete_task_run(
            db,
            task_run,
            summary=_compact_runtime_text(response_content, limit=280),
        )
        
        logger.info(f"[Agent] {_agent_type(target_agent)} responded to message successfully")

        # 11. 异步提取记忆（不阻塞响应）
        if len(response_content) > 30:
            asyncio.create_task(_extract_memories(
                agent_id=target_agent.id,
                agent_type=_agent_type(target_agent),
                user_message=user_message,
                agent_response=response_content
            ))

    except Exception as e:
        logger.error(f"[ Agent response failed: {str(e)}")
        append_task_event(
            db,
            task_run,
            "task_run_failed",
            summary=f"Agent response failed: {e}",
            payload={"error": str(e)},
        )
        complete_task_run(db, task_run, status="failed", summary=str(e))
        import traceback
        traceback.print_exc()
    finally:
        if workspace_token is not None:
            reset_active_workspace(workspace_token)
        db.close()


async def _extract_memories(agent_id: int, agent_type: str, user_message: str, agent_response: str):
    """
    用 LLM 从对话中提取关键信息，存为 Agent 记忆

    提取内容：事实、决策、用户偏好、重要上下文
    跳过条件：简单问候、确认类回复
    """
    try:
        from models.database import get_db as _get_db, Memory
        from llm.client import get_llm_client_for_agent, get_default_llm_client, clear_client_cache

        llm = get_llm_client_for_agent(normalize_agent_type(agent_type))

        extraction_messages = [
            {
                "role": "system",
                "content": (
                    "You are a memory extraction system. Analyze the conversation and extract "
                    "important information worth remembering. Return a JSON array of objects with fields: "
                    "'content' (the memory text, concise), 'type' (one of: fact, preference, decision, context), "
                    "'importance' (1-10).\n\n"
                    "Rules:\n"
                    "- Extract factual information, user preferences, decisions made, and important context\n"
                    "- Skip greetings, small talk, simple confirmations, and generic Q&A\n"
                    "- Each memory should be self-contained and meaningful\n"
                    "- Max 3 memories per extraction\n"
                    "- If nothing worth remembering, return an empty array []\n"
                    "- Return ONLY the JSON array, no explanation"
                )
            },
            {
                "role": "user",
                "content": f"User: {user_message[:500]}\n\nAgent {agent_name}: {agent_response[:800]}"
            }
        ]

        result = await llm.chat(extraction_messages, temperature=0.3, max_tokens=500)

        if not result:
            return

        # 解析 JSON
        import json as _json
        result = result.strip()
        # 提取 JSON 数组
        if result.startswith("```"):
            result = result.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        memories = _json.loads(result)
        if not isinstance(memories, list) or not memories:
            return

        # 保存记忆
        db = next(_get_db())
        try:
            for mem in memories[:3]:  # 最多 3 条
                content = mem.get("content", "").strip()
                if not content or len(content) < 10:
                    continue
                mem_type = mem.get("type", "context")
                importance = min(max(int(mem.get("importance", 5)), 1), 10)

                db_memory = Memory(
                    agent_id=agent_id,
                    memory_type=mem_type,
                    content=content,
                    importance=importance
                )
                db.add(db_memory)

            db.commit()
            logger.info(f"[Memory] Extracted {len(memories)} memories for {agent_type}")
        except Exception as e:
            db.rollback()
            logger.debug(f"[Memory] Save failed: {e}")
        finally:
            db.close()

    except Exception as e:
        logger.debug(f"[Memory] Extraction failed: {e}")


def _tool_runtime_kwargs(agent: Optional[Agent], chatroom_id: int, project: Optional[Project]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"chatroom_id": chatroom_id}
    if agent is not None and getattr(agent, "id", None) is not None:
        payload["agent_id"] = agent.id
        payload["agent_name"] = agent_name_of(agent)
    if project is not None and getattr(project, "id", None) is not None:
        payload["project_id"] = project.id
    return payload


def _compact_runtime_text(value: Any, *, limit: int = 600) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def _build_orchestration_previous_work(turns: List[Dict[str, str]]) -> str:
    if not turns:
        return ""
    lines = []
    for item in turns:
        agent_label = str(item.get("agent") or "agent")
        preview = _compact_runtime_text(item.get("content") or "", limit=280)
        if preview:
            lines.append(f"- {agent_label}: {preview}")
    if not lines:
        return ""
    return "Completed orchestration turns:\n" + "\n".join(lines)


def _build_orchestration_handoff(from_agent_name: str, content: str) -> Dict[str, str]:
    return {
        "from_agent": from_agent_name,
        "content": _compact_runtime_text(content, limit=1200),
        "message_type": "handoff",
    }


def _scheduler_event_payload(
    queue: OrchestrationRuntimeQueue,
    step,
    *,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = step.to_payload()
    payload["step_state"] = queue.runtime_state_payload_for_step(step.step_id)
    payload["runtime"] = queue.runtime_snapshot_payload()
    if extra:
        payload.update(extra)
    return payload


def _scheduler_plan_payload(
    queue: OrchestrationRuntimeQueue,
    *,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = queue.plan.to_payload()
    payload["runtime"] = queue.runtime_snapshot_payload()
    if extra:
        payload.update(extra)
    return payload


def _build_single_agent_runner_policy(
    *,
    run_kind: str,
    agent_name: str,
    project_id: Optional[int],
    tool_names: Optional[List[str]] = None,
    streaming: bool = False,
    standalone: bool = False,
):
    from tools import tool_registry as runtime_tool_registry

    return compile_single_agent_run_policy(
        mode=run_kind,
        source="chat_runtime",
        agent_name=agent_name,
        project_id=project_id,
        tool_names=tool_names or [],
        tool_policy_pack=runtime_tool_registry.get_policy_pack(tool_names or []),
        streaming=streaming,
        standalone=standalone,
    )


def _build_orchestration_runner_policy(
    *,
    plan,
    project_id: Optional[int],
    tool_names: Optional[List[str]] = None,
    streaming: bool = False,
):
    from tools import tool_registry as runtime_tool_registry

    return compile_orchestration_run_policy(
        mode=plan.mode,
        source="orchestration_scheduler",
        project_id=project_id,
        steps=plan.steps,
        sidecar_agent_types=list(plan.sidecar_agent_types or []),
        tool_names=tool_names or [],
        tool_policy_pack=runtime_tool_registry.get_policy_pack(tool_names or []),
        streaming=streaming,
    )


def _task_run_event_payload(event: Optional[TaskRunEvent]) -> Dict[str, Any]:
    if event is None:
        return {}
    raw_payload = getattr(event, "payload_json", None)
    if not raw_payload:
        return {}
    try:
        loaded = json.loads(raw_payload)
        return loaded if isinstance(loaded, dict) else {}
    except json.JSONDecodeError:
        return {}


def _json_column_payload(raw_payload: Optional[str]) -> Dict[str, Any]:
    if not raw_payload:
        return {}
    try:
        loaded = json.loads(raw_payload)
        return loaded if isinstance(loaded, dict) else {}
    except json.JSONDecodeError:
        return {}


def _next_recovery_lease_expiry(now: Optional[datetime] = None) -> datetime:
    return (now or datetime.now()) + timedelta(seconds=RECOVERY_LEASE_SECONDS)


def _format_recovery_lease_detail(owner: Optional[str], lease_expires_at: Optional[datetime]) -> str:
    if owner and lease_expires_at:
        return f"Task run is already being recovered by {owner} until {lease_expires_at.isoformat()}."
    if owner:
        return f"Task run is already being recovered by {owner}."
    return "Task run is already being recovered by another Catown instance."


def _claim_task_run_recovery_lease(
    db: Session,
    task_run_id: int,
) -> tuple[Optional[TaskRun], TaskRunRecoveryResult]:
    now = datetime.now()
    lease_expires_at = _next_recovery_lease_expiry(now)
    updated = (
        db.query(TaskRun)
        .filter(
            TaskRun.id == task_run_id,
            TaskRun.status == "running",
            TaskRun.run_kind.in_(sorted(RECOVERABLE_ORCHESTRATION_RUN_KINDS)),
            or_(
                TaskRun.recovery_owner.is_(None),
                TaskRun.recovery_lease_expires_at.is_(None),
                TaskRun.recovery_lease_expires_at < now,
            ),
        )
        .update(
            {
                TaskRun.recovery_owner: RECOVERY_INSTANCE_ID,
                TaskRun.recovery_claimed_at: now,
                TaskRun.recovery_lease_expires_at: lease_expires_at,
            },
            synchronize_session=False,
        )
    )
    db.commit()

    task_run = db.query(TaskRun).filter(TaskRun.id == task_run_id).first()
    if updated:
        if task_run is not None:
            db.refresh(task_run)
        return task_run, TaskRunRecoveryResult(
            task_run_id=task_run_id,
            resumed=False,
            reason="claimed",
            status=task_run.status if task_run is not None else "running",
            owner=RECOVERY_INSTANCE_ID,
            lease_expires_at=lease_expires_at,
        )

    if task_run is None:
        return None, TaskRunRecoveryResult(
            task_run_id=task_run_id,
            resumed=False,
            reason="not_found",
            status=None,
            detail="Task run not found.",
        )
    status = (task_run.status or "").lower()
    if status != "running":
        return task_run, TaskRunRecoveryResult(
            task_run_id=task_run_id,
            resumed=False,
            reason="not_running",
            status=task_run.status,
            detail="Only running task runs can be resumed.",
        )
    if (task_run.run_kind or "") not in RECOVERABLE_ORCHESTRATION_RUN_KINDS:
        return task_run, TaskRunRecoveryResult(
            task_run_id=task_run_id,
            resumed=False,
            reason="not_recoverable",
            status=task_run.status,
            detail="Only recoverable orchestration runs can be resumed.",
        )
    return task_run, TaskRunRecoveryResult(
        task_run_id=task_run_id,
        resumed=False,
        reason="leased",
        status=task_run.status,
        detail=_format_recovery_lease_detail(task_run.recovery_owner, task_run.recovery_lease_expires_at),
        owner=task_run.recovery_owner,
        lease_expires_at=task_run.recovery_lease_expires_at,
    )


def _renew_task_run_recovery_lease(db: Session, task_run_id: int) -> Optional[datetime]:
    lease_expires_at = _next_recovery_lease_expiry()
    updated = (
        db.query(TaskRun)
        .filter(
            TaskRun.id == task_run_id,
            TaskRun.recovery_owner == RECOVERY_INSTANCE_ID,
            TaskRun.status == "running",
        )
        .update(
            {TaskRun.recovery_lease_expires_at: lease_expires_at},
            synchronize_session=False,
        )
    )
    db.commit()
    return lease_expires_at if updated else None


def _recover_orchestration_agent_names(task_run: TaskRun) -> List[str]:
    schedule_event = next((event for event in task_run.events if event.event_type == "scheduler_plan_created"), None)
    schedule_payload = _task_run_event_payload(schedule_event)
    raw_steps = schedule_payload.get("steps", [])
    if isinstance(raw_steps, list):
        recovered_names = []
        for step in raw_steps:
            if not isinstance(step, dict):
                continue
            raw_requested_name = str(step.get("requested_name") or "").strip()
            if not raw_requested_name:
                continue
            requested_name = normalize_agent_type(raw_requested_name)
            if requested_name:
                recovered_names.append(requested_name)
        if recovered_names:
            return recovered_names

    orchestration_event = next((event for event in task_run.events if event.event_type == "orchestration_started"), None)
    orchestration_payload = _task_run_event_payload(orchestration_event)
    resolved_agents = orchestration_payload.get("resolved_agents", [])
    if isinstance(resolved_agents, list):
        recovered_names = [normalize_agent_type(name) for name in resolved_agents if str(name or "").strip()]
        if recovered_names:
            return recovered_names

    mentioned_names = [normalize_agent_type(name) for name in re.findall(r'@(\w+)', task_run.user_request or "")]
    return [name for name in mentioned_names if name]


def _rebuild_orchestration_recovery_state(
    db: Session,
    *,
    task_run: TaskRun,
    queue: OrchestrationRuntimeQueue,
) -> tuple[List[Dict[str, str]], Dict[str, List[Dict[str, str]]], str, List[str]]:
    messages_by_id: Dict[int, Message] = {}
    completed_turns: List[Dict[str, str]] = []
    pending_handoffs: Dict[str, List[Dict[str, str]]] = {}
    completed_step_ids: List[str] = []
    completed_step_id_set: set[str] = set()
    last_blocking_result = ""
    step_by_agent_name = {step.agent_name.lower(): step for step in queue.plan.steps}

    message_ids = [
        event.message_id
        for event in task_run.events
        if event.event_type == "agent_turn_completed" and event.message_id
    ]
    if message_ids:
        for message in db.query(Message).filter(Message.id.in_(message_ids)).all():
            messages_by_id[message.id] = message

    for event in task_run.events:
        if event.event_type != "agent_turn_completed":
            continue
        agent_name = (event.agent_name or "").strip()
        if not agent_name:
            continue
        step = step_by_agent_name.get(agent_name.lower())
        if step is None or step.step_id in completed_step_id_set:
            continue

        message = messages_by_id.get(event.message_id or 0)
        content = (message.content if message is not None else "") or ""
        if not content:
            payload = _task_run_event_payload(event)
            content = str(payload.get("response_preview") or "").strip()

        completed_turns.append({"agent": agent_name, "content": content})
        completed_step_ids.append(step.step_id)
        completed_step_id_set.add(step.step_id)
        ready_steps = queue.mark_completed(step.step_id)

        if step.dispatch_kind == "blocking" and content:
            last_blocking_result = content

        if not content:
            continue
        handoff = _build_orchestration_handoff(agent_name, content)
        for next_step in ready_steps:
            if next_step.step_id in completed_step_id_set:
                continue
            pending_handoffs.setdefault(next_step.step_id, []).append(handoff)

    ready_step_ids = set(queue.runtime_snapshot().ready_step_ids)
    pending_handoffs = {
        step_id: handoffs
        for step_id, handoffs in pending_handoffs.items()
        if step_id in ready_step_ids and handoffs
    }
    return completed_turns, pending_handoffs, last_blocking_result, completed_step_ids


def _resolve_orchestration_targets(
    db: Session,
    project: Optional[Project],
    agents: List[Agent],
    agent_names: List[str],
) -> List[tuple[str, Optional[Agent]]]:
    targets: List[tuple[str, Optional[Agent]]] = []
    seen_ids: set[int] = set()

    for name in agent_names:
        requested_name = normalize_agent_type(name)
        agent = find_agent_by_type(agents, requested_name)
        if not agent:
            global_agent = _find_db_agent_by_type(db, requested_name)
            if global_agent and project:
                assignment = (
                    db.query(AgentAssignment)
                    .filter(
                        AgentAssignment.project_id == project.id,
                        AgentAssignment.agent_id == global_agent.id,
                    )
                    .first()
                )
                if assignment is None:
                    db.add(AgentAssignment(project_id=project.id, agent_id=global_agent.id))
                    db.commit()
                if global_agent not in agents:
                    agents.append(global_agent)
            if global_agent:
                agent = global_agent

        if agent is None:
            targets.append((requested_name, None))
            continue
        if getattr(agent, "id", None) in seen_ids:
            continue
        seen_ids.add(agent.id)
        targets.append((requested_name, agent))

    return targets


def _ensure_collaboration_context(agents: List[Agent], chatroom_id: int) -> None:
    from agents.collaboration import collaboration_coordinator, AgentCollaborator

    for agent in agents:
        if getattr(agent, "id", None) in collaboration_coordinator.collaborators:
            continue
        collaboration_coordinator.register_collaborator(
            AgentCollaborator(
                agent_id=agent.id,
                agent_name=_agent_type(agent),
                chatroom_id=chatroom_id,
            )
        )


async def _iter_agent_turn_events(
    *,
    agent: Agent,
    chatroom_id: int,
    chatroom: Chatroom,
    project: Optional[Project],
    agents: List[Agent],
    user_message: str,
    db: Session,
    client_turn_id: Optional[str] = None,
    previous_agent_work: str = "",
    inter_agent_messages: Optional[List[Dict[str, Any]]] = None,
    history_limit: int = 4,
    standalone_note: str = "",
    task_run: Optional[TaskRun] = None,
):
    from tools import tool_registry

    _ensure_collaboration_context(agents, chatroom_id)

    llm_client = get_llm_client_for_agent(_agent_type(agent))
    agent_label = agent_name_of(agent)
    available_tools = tool_registry.list_tools()
    recent_messages = await chatroom_manager.get_messages(chatroom_id, limit=max(history_limit + 2, 6))
    turn_state = TurnContextState(previous_agent_work=previous_agent_work or "")
    turn_state.add_inter_agent_messages(inter_agent_messages or [])
    tool_schemas = tool_registry.get_schemas()
    runtime_kwargs = _tool_runtime_kwargs(agent, chatroom_id, project)
    record_agent_turn_started(
        db,
        task_run,
        agent_name=agent_label,
        summary=f"{agent_label} started an orchestrated streaming turn.",
        payload={
            "client_turn_id": client_turn_id,
            "inter_agent_message_count": len(inter_agent_messages or []),
        },
    )

    def _assemble_stream_messages(current_turn_state: TurnContextState) -> List[Dict[str, Any]]:
        return _assemble_chat_messages(
            db=db,
            agent=agent,
            agent_name=agent_label,
            model_id=getattr(llm_client, "model", ""),
            chatroom=chatroom,
            project=project,
            agents=agents,
            recent_messages=recent_messages,
            user_message=user_message,
            available_tools=available_tools,
            history_limit=history_limit,
            standalone_note=standalone_note,
            turn_state=current_turn_state,
        )

    async def _execute_stream_tool(tool_name, tool_args, tool_args_str, tool_call_id, tool_index, turn_index):
        return await tool_registry.execute(tool_name, **tool_args, **runtime_kwargs)

    async def _on_stream_tool_round(frame, normalized_tool_calls, tool_results, current_turn_state):
        record_runner_tool_round(
            db,
            task_run,
            agent_name=agent_label,
            turn=frame.turn_index,
            tool_names=[tool_call["function"]["name"] for tool_call in normalized_tool_calls],
            tool_results=tool_results,
            summary=f"{agent_label} completed a streaming tool round.",
        )

    def _build_stream_llm_card(frame, response_content, raw_tool_calls, tool_call_previews, raw_event):
        return _build_llm_card_payload(
            agent_name=agent_label,
            llm_client=llm_client,
            turn=frame.turn_index,
            duration_ms=int((raw_event.get("timings", {}) or {}).get("completed_ms") or ((time.time() - frame.llm_started_at) * 1000)),
            system_prompt=frame.system_prompt,
            prompt_messages=frame.prompt_snapshot,
            response_content=response_content,
            tool_call_previews=tool_call_previews,
            raw_tool_calls=raw_tool_calls,
            usage=raw_event.get("usage"),
            finish_reason=raw_event.get("finish_reason"),
            timings=raw_event.get("timings"),
        )

    async for event in iter_stream_turn_events(
        llm_client=llm_client,
        tools=tool_schemas,
        turn_state=turn_state,
        agent_name=agent_label,
        client_turn_id=client_turn_id,
        assemble_messages=_assemble_stream_messages,
        execute_tool=_execute_stream_tool,
        build_llm_runtime_card=_build_stream_llm_card,
        snapshot_messages=_snapshot_llm_messages,
        preview_tool_calls=_preview_tool_calls,
        format_prompt_messages=_format_json_block,
        tool_result_success=_tool_result_succeeded,
        max_turns=MAX_TOOL_ITERATIONS,
        on_tool_round=_on_stream_tool_round,
    ):
        if event["type"] == "turn_complete":
            event["agent"] = agent
        yield event


async def _run_single_agent_turn(
    agent,
    chatroom_id,
    project,
    agents,
    user_message,
    extra_context,
    db,
    client_turn_id: Optional[str] = None,
    inter_agent_messages: Optional[List[Dict[str, Any]]] = None,
    task_run: Optional[TaskRun] = None,
):
    """
    执行单个 Agent 的一次响应（供多 Agent 编排调用）

    Returns: (response_content, agent_response_msg) 或 (None, None)
    """
    from tools import tool_registry

    llm_client = get_llm_client_for_agent(_agent_type(agent))
    current_chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()

    _ensure_collaboration_context(agents, chatroom_id)

    available_tools = tool_registry.list_tools()
    recent_msgs = await chatroom_manager.get_messages(chatroom_id, limit=6)
    turn_state = TurnContextState(previous_agent_work=extra_context or "")
    turn_state.add_inter_agent_messages(inter_agent_messages or [])
    tool_schemas = tool_registry.get_schemas()
    runtime_kwargs = _tool_runtime_kwargs(agent, chatroom_id, project)
    record_agent_turn_started(
        db,
        task_run,
        agent_name=agent_name_of(agent),
        summary=f"{agent_name_of(agent)} started an orchestrated turn.",
        payload={
            "client_turn_id": client_turn_id,
            "inter_agent_message_count": len(inter_agent_messages or []),
        },
    )

    def _assemble_orchestration_turn_messages(current_turn_state: TurnContextState) -> List[Dict[str, Any]]:
        return _assemble_chat_messages(
            db=db,
            agent=agent,
            agent_name=agent_name_of(agent),
            model_id=getattr(llm_client, "model", ""),
            chatroom=current_chatroom,
            project=project,
            agents=agents,
            recent_messages=recent_msgs,
            user_message=user_message,
            available_tools=available_tools,
            history_limit=4,
            standalone_note=(
            "This is a standalone chat. Reply directly, stay concise, "
            "and coordinate with mentioned teammates when it helps."
        )
        if not project
        else "",
            turn_state=current_turn_state,
        )

    async def _execute_orchestration_tool(frame, tool_call):
        tool_name = tool_call["function"]["name"]
        tool_args_str = tool_call["function"].get("arguments", "{}")
        try:
            tool_args = json.loads(tool_args_str or "{}")
            tool_result = await tool_registry.execute(
                tool_name,
                **tool_args,
                **runtime_kwargs,
            )
            result_str = str(tool_result) if tool_result else "(no output)"
            tool_success = True
        except Exception as te:
            result_str = f"Error: {te}"
            tool_success = False
        return build_tool_result_record(
            tool_call_id=tool_call.get("id"),
            tool_name=tool_name,
            arguments=tool_args_str,
            result=result_str,
            success=tool_success,
        )

    async def _on_orchestration_tool_round(frame, tool_results, current_turn_state):
        record_runner_tool_round(
            db,
            task_run,
            agent_name=agent_name_of(agent),
            turn=frame.turn_index + 1,
            tool_names=[tool_call["function"]["name"] for tool_call in frame.normalized_tool_calls],
            tool_results=tool_results,
            summary=f"{agent_name_of(agent)} completed a tool round.",
        )

    response_content = await execute_non_stream_turn_loop(
        llm_client=llm_client,
        tools=tool_schemas,
        turn_state=turn_state,
        assemble_messages=_assemble_orchestration_turn_messages,
        execute_tool_call=_execute_orchestration_tool,
        max_turns=MAX_TOOL_ITERATIONS,
        on_tool_round=_on_orchestration_tool_round,
    )

    if not response_content:
        return None, None

    # 保存到数据库
    agent_msg = await chatroom_manager.send_message(
        chatroom_id=chatroom_id, agent_id=agent.id,
        content=response_content,
        message_type="text",
        metadata=_message_metadata_with_turn(client_turn_id),
        agent_name=agent_name_of(agent),
    )
    record_agent_turn_completed(
        db,
        task_run,
        agent_name=agent_name_of(agent),
        message_id=agent_msg.id,
        response_content=response_content,
        summary=f"{agent_name_of(agent)} completed the orchestrated turn.",
    )

    # 异步提取记忆
    if len(response_content) > 30:
        asyncio.create_task(_extract_memories(agent.id, _agent_type(agent), user_message, response_content))

    return response_content, agent_msg


async def _run_multi_agent_orchestration(
    chatroom_id,
    project,
    agents,
    agent_names,
    user_message,
    db,
    client_turn_id: Optional[str] = None,
    task_run: Optional[TaskRun] = None,
    extra_context: str = "",
):
    """
    多 Agent 协作编排

    以 turn/inbox/handoff 驱动执行，而不是固定 stage pipeline。
    """
    from tools import tool_registry

    targets = _resolve_orchestration_targets(db, project, agents, agent_names)
    resolved_agents = [agent for _, agent in targets if agent is not None]
    available_tools = tool_registry.list_tools()

    if not resolved_agents:
        logger.warning("[Collab] No valid agents found for multi-agent orchestration")
        append_task_event(
            db,
            task_run,
            "task_run_failed",
            summary="No valid agents resolved for orchestration.",
            payload={"requested_agents": agent_names},
        )
        complete_task_run(db, task_run, status="failed", summary="No valid agents resolved.")
        return

    logger.info(f"[Collab] Orchestration: {' -> '.join(_agent_type(a) for a in resolved_agents)}")
    completed_turns: List[Dict[str, str]] = []
    pending_handoffs: Dict[str, List[Dict[str, str]]] = {}
    results = []
    last_blocking_result = ""
    plan = build_orchestration_schedule(
        [(requested_name, agent) for requested_name, agent in targets if agent is not None],
        sidecar_agent_types=_configured_sidecar_agent_types(),
    )
    orchestration_policy = _build_orchestration_runner_policy(
        plan=plan,
        project_id=project.id if project else None,
        tool_names=available_tools,
        streaming=False,
    )
    queue = OrchestrationRuntimeQueue(plan)
    agents_by_id = {getattr(agent, "id", None): agent for agent in resolved_agents}

    append_task_event(
        db,
        task_run,
        "orchestration_started",
        summary="Multi-agent orchestration started.",
        payload={
            "requested_agents": agent_names,
            "resolved_agents": [agent_name_of(agent) for agent in resolved_agents],
            "project_id": project.id if project else None,
            "runner_policy": orchestration_policy.to_payload(),
        },
    )

    append_task_event(
        db,
        task_run,
        "scheduler_plan_created",
        summary=(
            "Built a blocking-chain orchestration schedule with sidecars."
            if plan.mode == "blocking_chain_with_sidecars"
            else "Built a linear blocking orchestration schedule."
        ),
        payload=_scheduler_plan_payload(
            queue,
            extra={"runner_policy": orchestration_policy.to_payload()},
        ),
    )

    while True:
        step = queue.pop_ready()
        if step is None:
            break

        agent = agents_by_id.get(step.agent_id)
        if agent is None:
            continue

        agent_label = agent_name_of(agent)
        step_policy = find_stage_policy(orchestration_policy, step.step_id)
        logger.info(f"[Collab] Step {step.position}/{len(plan.steps)}: {_agent_type(agent)}")
        append_task_event(
            db,
            task_run,
            "scheduler_step_dispatched",
            agent_name=agent_label,
            summary=f"Scheduler dispatched {step.dispatch_kind} work to {agent_label}.",
            payload=_scheduler_event_payload(
                queue,
                step,
                extra={
                    "stage_policy": step_policy.to_payload() if step_policy is not None else None,
                },
            ),
        )

        content, msg = await _run_single_agent_turn(
            agent=agent,
            chatroom_id=chatroom_id,
            project=project,
            agents=agents,
            user_message=user_message,
            extra_context=f"{_build_orchestration_previous_work(completed_turns)}\n{extra_context}".strip(),
            inter_agent_messages=pending_handoffs.pop(step.step_id, []),
            db=db,
            client_turn_id=client_turn_id,
            task_run=task_run,
        )

        if content:
            await _publish_saved_chat_message(
                db,
                chatroom_id,
                message_id=msg.id,
                content=content,
                agent_name=agent_name_of(agent),
                message_type="text",
                created_at=msg.created_at,
                metadata=_message_metadata_with_turn(client_turn_id),
            )

            results.append({"agent": agent_label, "content": content})
            completed_turns.append({"agent": agent_label, "content": content})
            if step.dispatch_kind == "blocking":
                last_blocking_result = content

        ready_steps = queue.mark_completed(step.step_id)
        append_task_event(
            db,
            task_run,
            "scheduler_step_completed",
            agent_name=agent_label,
            summary=(
                f"Scheduler marked {agent_label} complete and released {len(ready_steps)} waiting step(s)."
                if ready_steps
                else f"Scheduler marked {agent_label} complete."
            ),
            payload=_scheduler_event_payload(
                queue,
                step,
                extra={
                    "stage_policy": step_policy.to_payload() if step_policy is not None else None,
                    "released_step_ids": [next_step.step_id for next_step in ready_steps],
                    "released_step_count": len(ready_steps),
                    "completed_with_output": bool(content),
                },
            ),
        )
        for next_step in ready_steps:
            next_step_policy = find_stage_policy(orchestration_policy, next_step.step_id)
            append_task_event(
                db,
                task_run,
                "scheduler_step_resumed",
                agent_name=next_step.agent_name,
                summary=f"Scheduler resumed {next_step.agent_name} after {agent_label}.",
                payload=_scheduler_event_payload(
                    queue,
                    next_step,
                    extra={
                        "stage_policy": (
                            next_step_policy.to_payload() if next_step_policy is not None else None
                        ),
                        "resumed_by_step_id": step.step_id,
                        "resumed_by_agent": agent_label,
                    },
                ),
            )
        if content:
            handoff = _build_orchestration_handoff(agent_label, content)
            for next_step in ready_steps:
                pending_handoffs.setdefault(next_step.step_id, []).append(handoff)
                append_task_event(
                    db,
                    task_run,
                    "handoff_created",
                    agent_name=agent_label,
                    summary=f"Handoff created for {next_step.agent_name}.",
                    payload={
                        "from_agent": agent_label,
                        "to_agent": next_step.agent_name,
                        "from_step_id": step.step_id,
                        "to_step_id": next_step.step_id,
                        "dispatch_kind": next_step.dispatch_kind,
                        "attached_to_step_id": next_step.attached_to_step_id,
                        "content_preview": handoff.get("content"),
                    },
                )
        elif step.dispatch_kind == "blocking":
            logger.warning(f"[Collab] {agent.name} returned empty response")

    logger.info(f"[Collab] Orchestration complete: {len(results)}/{len(resolved_agents)} agents responded")
    complete_task_run(
        db,
        task_run,
        summary=_compact_runtime_text(
            last_blocking_result or (results[-1]["content"] if results else "Orchestration completed without agent output."),
            limit=280,
        ),
    )


async def _run_multi_agent_pipeline(
    chatroom_id, project, agents, agent_names, user_message, db, client_turn_id: Optional[str] = None
):
    """Backward-compatible wrapper for the old helper name."""
    await _run_multi_agent_orchestration(
        chatroom_id=chatroom_id,
        project=project,
        agents=agents,
        agent_names=agent_names,
        user_message=user_message,
        db=db,
        client_turn_id=client_turn_id,
    )


async def _resume_interrupted_orchestration_task_run(
    task_run_id: int,
    *,
    trigger: str = "startup",
) -> TaskRunRecoveryResult:
    from tools import tool_registry

    db = SessionLocal()
    try:
        task_run, claim_result = _claim_task_run_recovery_lease(db, task_run_id)
        if claim_result.reason != "claimed":
            return claim_result
        lease_expires_at = claim_result.lease_expires_at
        if task_run is None:
            return TaskRunRecoveryResult(
                task_run_id=task_run_id,
                resumed=False,
                reason="not_found",
                status=None,
                detail="Task run not found.",
            )

        chatroom = db.query(Chatroom).filter(Chatroom.id == task_run.chatroom_id).first()
        if chatroom is None:
            append_task_event(
                db,
                task_run,
                "task_run_recovery_failed",
                summary="Recovery aborted because the chatroom no longer exists.",
                payload={"task_run_id": task_run.id},
            )
            complete_task_run(db, task_run, status="failed", summary="Recovery failed: chatroom missing.")
            return TaskRunRecoveryResult(
                task_run_id=task_run_id,
                resumed=False,
                reason="chatroom_missing",
                status="failed",
                detail="Recovery failed: chatroom missing.",
                owner=RECOVERY_INSTANCE_ID,
                lease_expires_at=lease_expires_at,
            )

        project = _resolve_chatroom_project(db, chatroom)
        agents = _serialize_project_agents(db, project.id) if project else _list_global_agents(db)
        agent_names = _recover_orchestration_agent_names(task_run)
        available_tools = tool_registry.list_tools()
        targets = _resolve_orchestration_targets(db, project, agents, agent_names)
        resolved_agents = [agent for _, agent in targets if agent is not None]
        if not resolved_agents:
            append_task_event(
                db,
                task_run,
                "task_run_recovery_failed",
                summary="Recovery aborted because no valid orchestration agents could be resolved.",
                payload={"requested_agents": agent_names},
            )
            complete_task_run(db, task_run, status="failed", summary="Recovery failed: no valid agents resolved.")
            return TaskRunRecoveryResult(
                task_run_id=task_run_id,
                resumed=False,
                reason="no_valid_agents",
                status="failed",
                detail="Recovery failed: no valid agents resolved.",
                owner=RECOVERY_INSTANCE_ID,
                lease_expires_at=lease_expires_at,
            )

        plan = build_orchestration_schedule(
            [(requested_name, agent) for requested_name, agent in targets if agent is not None],
            sidecar_agent_types=_configured_sidecar_agent_types(),
        )
        orchestration_policy = _build_orchestration_runner_policy(
            plan=plan,
            project_id=project.id if project else None,
            tool_names=available_tools,
            streaming=(task_run.run_kind == "multi_agent_orchestration_stream"),
        )
        queue = OrchestrationRuntimeQueue(plan)
        append_task_event(
            db,
            task_run,
            "task_run_recovery_started",
            summary=(
                "Manual resume started recovery for an interrupted orchestration run."
                if trigger == "manual"
                else "Detected an interrupted orchestration run and started recovery."
            ),
            payload={
                "task_run_id": task_run.id,
                "run_kind": task_run.run_kind,
                "requested_agents": agent_names,
                "resolved_agents": [agent_name_of(agent) for agent in resolved_agents],
                "project_id": project.id if project else None,
                "chatroom_id": chatroom.id,
                "trigger": trigger,
                "recovery_owner": RECOVERY_INSTANCE_ID,
                "recovery_lease_expires_at": lease_expires_at.isoformat() if lease_expires_at else None,
                "runner_policy": orchestration_policy.to_payload(),
            },
        )
        completed_turns, pending_handoffs, last_blocking_result, completed_step_ids = _rebuild_orchestration_recovery_state(
            db,
            task_run=task_run,
            queue=queue,
        )
        agents_by_id = {getattr(agent, "id", None): agent for agent in resolved_agents}
        append_task_event(
            db,
            task_run,
            "scheduler_recovery_state_rebuilt",
            summary=(
                f"Rebuilt scheduler state with {len(completed_step_ids)} completed step(s) and "
                f"{queue.runtime_snapshot().ready_step_count} ready step(s)."
            ),
            payload=_scheduler_plan_payload(
                queue,
                extra={
                    "runner_policy": orchestration_policy.to_payload(),
                    "recovery": {
                        "completed_step_ids": completed_step_ids,
                        "replayed_turn_count": len(completed_turns),
                    }
                },
            ),
        )
        initial_runtime = queue.runtime_snapshot()
        if (
            initial_runtime.ready_step_count == 0
            and initial_runtime.completed_step_count < initial_runtime.step_count
        ):
            append_task_event(
                db,
                task_run,
                "task_run_recovery_failed",
                summary="Recovery rebuilt the scheduler state but found no runnable steps.",
                payload=_scheduler_plan_payload(
                    queue,
                    extra={"runner_policy": orchestration_policy.to_payload()},
                ),
            )
            complete_task_run(db, task_run, status="failed", summary="Recovery failed: no runnable steps after rebuild.")
            return TaskRunRecoveryResult(
                task_run_id=task_run_id,
                resumed=False,
                reason="no_runnable_steps",
                status="failed",
                detail="Recovery failed: no runnable steps after rebuild.",
                owner=RECOVERY_INSTANCE_ID,
                lease_expires_at=lease_expires_at,
            )

        while True:
            lease_expires_at = _renew_task_run_recovery_lease(db, task_run.id)
            if lease_expires_at is None:
                logger.warning(
                    "[Recovery] Lost lease for task run %s while %s was attempting resume",
                    task_run.id,
                    RECOVERY_INSTANCE_ID,
                )
                refreshed = db.query(TaskRun).filter(TaskRun.id == task_run.id).first()
                return TaskRunRecoveryResult(
                    task_run_id=task_run_id,
                    resumed=False,
                    reason="lease_lost",
                    status=refreshed.status if refreshed is not None else task_run.status,
                    detail="Recovery lease was lost before the orchestration could finish.",
                    owner=refreshed.recovery_owner if refreshed is not None else None,
                    lease_expires_at=(
                        refreshed.recovery_lease_expires_at if refreshed is not None else None
                    ),
                )
            step = queue.pop_ready()
            if step is None:
                break

            agent = agents_by_id.get(step.agent_id)
            if agent is None:
                continue

            agent_label = agent_name_of(agent)
            step_policy = find_stage_policy(orchestration_policy, step.step_id)
            append_task_event(
                db,
                task_run,
                "scheduler_step_dispatched",
                agent_name=agent_label,
                summary=f"Recovery dispatched {step.dispatch_kind} work to {agent_label}.",
                payload=_scheduler_event_payload(
                    queue,
                    step,
                    extra={
                        "recovered": True,
                        "stage_policy": step_policy.to_payload() if step_policy is not None else None,
                    },
                ),
            )

            content, msg = await _run_single_agent_turn(
                agent=agent,
                chatroom_id=chatroom.id,
                project=project,
                agents=agents,
                user_message=task_run.user_request or "",
                extra_context=_build_orchestration_previous_work(completed_turns),
                inter_agent_messages=pending_handoffs.pop(step.step_id, []),
                db=db,
                client_turn_id=task_run.client_turn_id,
                task_run=task_run,
            )

            if content:
                await _publish_saved_chat_message(
                    db,
                    chatroom.id,
                    message_id=msg.id,
                    content=content,
                    agent_name=agent_label,
                    message_type="text",
                    created_at=msg.created_at,
                    metadata=_message_metadata_with_turn(task_run.client_turn_id),
                )
                completed_turns.append({"agent": agent_label, "content": content})
                if step.dispatch_kind == "blocking":
                    last_blocking_result = content

            ready_steps = queue.mark_completed(step.step_id)
            append_task_event(
                db,
                task_run,
                "scheduler_step_completed",
                agent_name=agent_label,
                summary=(
                    f"Recovery marked {agent_label} complete and released {len(ready_steps)} waiting step(s)."
                    if ready_steps
                    else f"Recovery marked {agent_label} complete."
                ),
                payload=_scheduler_event_payload(
                    queue,
                    step,
                    extra={
                        "recovered": True,
                        "stage_policy": step_policy.to_payload() if step_policy is not None else None,
                        "released_step_ids": [next_step.step_id for next_step in ready_steps],
                        "released_step_count": len(ready_steps),
                        "completed_with_output": bool(content),
                    },
                ),
            )
            for next_step in ready_steps:
                next_step_policy = find_stage_policy(orchestration_policy, next_step.step_id)
                append_task_event(
                    db,
                    task_run,
                    "scheduler_step_resumed",
                    agent_name=next_step.agent_name,
                    summary=f"Recovery resumed {next_step.agent_name} after {agent_label}.",
                    payload=_scheduler_event_payload(
                        queue,
                        next_step,
                        extra={
                            "recovered": True,
                            "stage_policy": (
                                next_step_policy.to_payload() if next_step_policy is not None else None
                            ),
                            "resumed_by_step_id": step.step_id,
                            "resumed_by_agent": agent_label,
                        },
                    ),
                )

            if content:
                handoff = _build_orchestration_handoff(agent_label, content)
                for next_step in ready_steps:
                    pending_handoffs.setdefault(next_step.step_id, []).append(handoff)
                    append_task_event(
                        db,
                        task_run,
                        "handoff_created",
                        agent_name=agent_label,
                        summary=f"Recovery created a handoff for {next_step.agent_name}.",
                        payload={
                            "from_agent": agent_label,
                            "to_agent": next_step.agent_name,
                            "from_step_id": step.step_id,
                            "to_step_id": next_step.step_id,
                            "dispatch_kind": next_step.dispatch_kind,
                            "attached_to_step_id": next_step.attached_to_step_id,
                            "content_preview": handoff.get("content"),
                            "recovered": True,
                        },
                    )

        final_runtime = queue.runtime_snapshot()
        if final_runtime.completed_step_count < final_runtime.step_count:
            append_task_event(
                db,
                task_run,
                "task_run_recovery_failed",
                summary="Recovery stopped before all scheduled steps completed.",
                payload=_scheduler_plan_payload(
                    queue,
                    extra={"runner_policy": orchestration_policy.to_payload()},
                ),
            )
            complete_task_run(db, task_run, status="failed", summary="Recovery failed: orchestration remained incomplete.")
            return TaskRunRecoveryResult(
                task_run_id=task_run_id,
                resumed=False,
                reason="incomplete",
                status="failed",
                detail="Recovery failed: orchestration remained incomplete.",
                owner=RECOVERY_INSTANCE_ID,
                lease_expires_at=lease_expires_at,
            )
        recovery_summary = _compact_runtime_text(
            last_blocking_result or (completed_turns[-1]["content"] if completed_turns else "Recovered orchestration completed."),
            limit=280,
        )
        append_task_event(
            db,
            task_run,
            "task_run_recovery_completed",
            summary="Interrupted orchestration recovery completed.",
            payload={
                "task_run_id": task_run.id,
                "completed_step_count": queue.runtime_snapshot().completed_step_count,
                "step_count": len(queue.plan.steps),
            },
        )
        complete_task_run(db, task_run, summary=recovery_summary)
        return TaskRunRecoveryResult(
            task_run_id=task_run_id,
            resumed=True,
            reason="completed",
            status="completed",
            detail=recovery_summary,
            owner=RECOVERY_INSTANCE_ID,
            lease_expires_at=lease_expires_at,
        )
    except Exception as exc:
        logger.exception(f"[Recovery] Failed to recover task run {task_run_id}: {exc}")
        task_run = db.query(TaskRun).filter(TaskRun.id == task_run_id).first()
        if task_run is not None and (task_run.status or "").lower() == "running":
            append_task_event(
                db,
                task_run,
                "task_run_recovery_failed",
                summary=f"Interrupted orchestration recovery failed: {exc}",
                payload={"task_run_id": task_run_id},
            )
            complete_task_run(db, task_run, status="failed", summary=f"Recovery failed: {exc}")
        return TaskRunRecoveryResult(
            task_run_id=task_run_id,
            resumed=False,
            reason="exception",
            status=(task_run.status if task_run is not None else None),
            detail=f"Recovery failed: {exc}",
            owner=RECOVERY_INSTANCE_ID,
        )
    finally:
        db.close()


async def recover_interrupted_task_runs(limit: int = 10) -> Dict[str, int]:
    db = SessionLocal()
    try:
        pending_runs = (
            db.query(TaskRun)
            .filter(
                TaskRun.status == "running",
                TaskRun.run_kind.in_(sorted(RECOVERABLE_ORCHESTRATION_RUN_KINDS)),
            )
            .order_by(TaskRun.created_at.asc(), TaskRun.id.asc())
            .limit(max(1, limit))
            .all()
        )
        run_ids = [run.id for run in pending_runs]
    finally:
        db.close()

    recovered = 0
    failed = 0
    skipped = 0
    for run_id in run_ids:
        try:
            result = await _resume_interrupted_orchestration_task_run(run_id, trigger="startup")
            if result.resumed:
                recovered += 1
            elif result.reason in {"leased", "not_running", "not_recoverable", "not_found"}:
                skipped += 1
            else:
                failed += 1
        except Exception:
            failed += 1
    return {"detected": len(run_ids), "recovered": recovered, "failed": failed, "skipped": skipped}


async def _stream_multi_agent_orchestration(
    *,
    db: Session,
    chatroom: Chatroom,
    project: Optional[Project],
    agents: List[Agent],
    agent_names: List[str],
    user_message: str,
    client_turn_id: Optional[str],
    sse_json: Any,
    sse_card,
    set_active_agent=None,
    task_run: Optional[TaskRun] = None,
):
    from tools import tool_registry

    targets = _resolve_orchestration_targets(db, project, agents, agent_names)
    resolved_agents = [agent for _, agent in targets if agent is not None]
    available_tools = tool_registry.list_tools()

    yield f"data: {sse_json.dumps({'type': 'collab_start', 'agents': agent_names}, ensure_ascii=False)}\n\n"
    if not resolved_agents:
        append_task_event(
            db,
            task_run,
            "task_run_failed",
            summary="No valid agents resolved for streaming orchestration.",
            payload={"requested_agents": agent_names},
        )
        complete_task_run(db, task_run, status="failed", summary="No valid agents resolved.")
        for requested_name, agent in targets:
            if agent is None:
                yield f"data: {sse_json.dumps({'type': 'collab_skip', 'agent': requested_name, 'reason': 'not found'}, ensure_ascii=False)}\n\n"
        yield f"data: {sse_json.dumps({'type': 'done', 'agent_name': '', 'collab': True, 'client_turn_id': client_turn_id}, ensure_ascii=False)}\n\n"
        return

    completed_turns: List[Dict[str, str]] = []
    pending_handoffs: Dict[str, List[Dict[str, str]]] = {}
    last_blocking_result = ""
    standalone_note = (
        "This is a standalone chat. Reply directly, stay concise, "
        "and coordinate with the other mentioned agents."
        if project is None
        else ""
    )
    for requested_name, agent in targets:
        if agent is None:
            yield f"data: {sse_json.dumps({'type': 'collab_skip', 'agent': requested_name, 'reason': 'not found'}, ensure_ascii=False)}\n\n"

    plan = build_orchestration_schedule(
        [(requested_name, agent) for requested_name, agent in targets if agent is not None],
        sidecar_agent_types=_configured_sidecar_agent_types(),
    )
    orchestration_policy = _build_orchestration_runner_policy(
        plan=plan,
        project_id=project.id if project else None,
        tool_names=available_tools,
        streaming=True,
    )
    queue = OrchestrationRuntimeQueue(plan)
    agents_by_id = {getattr(agent, "id", None): agent for agent in resolved_agents}

    append_task_event(
        db,
        task_run,
        "orchestration_started",
        summary="Multi-agent streaming orchestration started.",
        payload={
            "requested_agents": agent_names,
            "resolved_agents": [agent_name_of(agent) for agent in resolved_agents],
            "project_id": project.id if project else None,
            "client_turn_id": client_turn_id,
            "runner_policy": orchestration_policy.to_payload(),
        },
    )

    append_task_event(
        db,
        task_run,
        "scheduler_plan_created",
        summary=(
            "Built a blocking-chain streaming schedule with sidecars."
            if plan.mode == "blocking_chain_with_sidecars"
            else "Built a linear blocking streaming schedule."
        ),
        payload=_scheduler_plan_payload(
            queue,
            extra={"runner_policy": orchestration_policy.to_payload()},
        ),
    )

    while True:
        step = queue.pop_ready()
        if step is None:
            break

        agent = agents_by_id.get(step.agent_id)
        if agent is None:
            continue

        agent_label = agent_name_of(agent)
        step_policy = find_stage_policy(orchestration_policy, step.step_id)
        if callable(set_active_agent):
            set_active_agent(agent_label, agent.id)
        append_task_event(
            db,
            task_run,
            "scheduler_step_dispatched",
            agent_name=agent_label,
            summary=f"Scheduler dispatched {step.dispatch_kind} work to {agent_label}.",
            payload=_scheduler_event_payload(
                queue,
                step,
                extra={
                    "stage_policy": step_policy.to_payload() if step_policy is not None else None,
                },
            ),
        )
        yield f"data: {sse_json.dumps({'type': 'collab_step', 'step': step.position, 'total': len(plan.steps), 'agent': step.requested_name, 'agent_name': agent_label, 'dispatch_kind': step.dispatch_kind, 'attached_to_step_id': step.attached_to_step_id, 'runtime': queue.runtime_snapshot_payload(), 'step_state': queue.runtime_state_payload_for_step(step.step_id)}, ensure_ascii=False)}\n\n"

        saved = None
        step_content = ""
        async for event in _iter_agent_turn_events(
            agent=agent,
            chatroom_id=chatroom.id,
            chatroom=chatroom,
            project=project,
            agents=agents,
            user_message=user_message,
            db=db,
            client_turn_id=client_turn_id,
            previous_agent_work=_build_orchestration_previous_work(completed_turns),
            inter_agent_messages=pending_handoffs.pop(step.step_id, []),
            history_limit=3,
            standalone_note=standalone_note,
            task_run=task_run,
        ):
            if event["type"] == "runtime_card":
                yield await sse_card(event["card_type"], event["payload"])
                continue

            if event["type"] == "turn_complete":
                step_content = event.get("content") or ""
                if step_content:
                    saved = await chatroom_manager.send_message(
                        chatroom_id=chatroom.id,
                        agent_id=agent.id,
                        content=step_content,
                        message_type="text",
                        metadata=_message_metadata_with_turn(client_turn_id),
                        agent_name=agent_label,
                    )
                    await _publish_saved_chat_message(
                        db,
                        chatroom.id,
                        message_id=saved.id,
                        content=step_content,
                        agent_name=agent_label,
                        message_type="text",
                        created_at=saved.created_at,
                        metadata=_message_metadata_with_turn(client_turn_id),
                    )
                    record_agent_turn_completed(
                        db,
                        task_run,
                        agent_name=agent_label,
                        message_id=saved.id,
                        response_content=step_content,
                        summary=f"{agent_label} completed the orchestrated streaming turn.",
                    )
                    if len(step_content) > 30:
                        asyncio.create_task(
                            _extract_memories(agent.id, _agent_type(agent), user_message, step_content)
                        )
                    completed_turns.append({"agent": agent_label, "content": step_content})
                    if step.dispatch_kind == "blocking":
                        last_blocking_result = step_content
                continue

            yield f"data: {sse_json.dumps(event, ensure_ascii=False)}\n\n"

        ready_steps = queue.mark_completed(step.step_id)
        append_task_event(
            db,
            task_run,
            "scheduler_step_completed",
            agent_name=agent_label,
            summary=(
                f"Scheduler marked {agent_label} complete and released {len(ready_steps)} waiting step(s)."
                if ready_steps
                else f"Scheduler marked {agent_label} complete."
            ),
            payload=_scheduler_event_payload(
                queue,
                step,
                extra={
                    "stage_policy": step_policy.to_payload() if step_policy is not None else None,
                    "released_step_ids": [next_step.step_id for next_step in ready_steps],
                    "released_step_count": len(ready_steps),
                    "completed_with_output": bool(step_content),
                },
            ),
        )
        for next_step in ready_steps:
            next_step_policy = find_stage_policy(orchestration_policy, next_step.step_id)
            append_task_event(
                db,
                task_run,
                "scheduler_step_resumed",
                agent_name=next_step.agent_name,
                summary=f"Scheduler resumed {next_step.agent_name} after {agent_label}.",
                payload=_scheduler_event_payload(
                    queue,
                    next_step,
                    extra={
                        "stage_policy": (
                            next_step_policy.to_payload() if next_step_policy is not None else None
                        ),
                        "resumed_by_step_id": step.step_id,
                        "resumed_by_agent": agent_label,
                    },
                ),
            )
        if step_content:
            handoff = _build_orchestration_handoff(agent_label, step_content)
            for next_step in ready_steps:
                pending_handoffs.setdefault(next_step.step_id, []).append(handoff)
                append_task_event(
                    db,
                    task_run,
                    "handoff_created",
                    agent_name=agent_label,
                    summary=f"Handoff created for {next_step.agent_name}.",
                    payload={
                        "from_agent": agent_label,
                        "to_agent": next_step.agent_name,
                        "from_step_id": step.step_id,
                        "to_step_id": next_step.step_id,
                        "dispatch_kind": next_step.dispatch_kind,
                        "attached_to_step_id": next_step.attached_to_step_id,
                        "content_preview": handoff.get("content"),
                    },
                )

        yield f"data: {sse_json.dumps({'type': 'collab_step_done', 'agent': step.requested_name, 'agent_name': agent_label, 'message_id': saved.id if saved else None, 'dispatch_kind': step.dispatch_kind, 'attached_to_step_id': step.attached_to_step_id, 'runtime': queue.runtime_snapshot_payload(), 'step_state': queue.runtime_state_payload_for_step(step.step_id), 'released_step_ids': [next_step.step_id for next_step in ready_steps]}, ensure_ascii=False)}\n\n"

    resolved_names = [agent_name_of(agent) for agent in resolved_agents]
    complete_task_run(
        db,
        task_run,
        summary=_compact_runtime_text(
            last_blocking_result or (completed_turns[-1]["content"] if completed_turns else "Streaming orchestration completed."),
            limit=280,
        ),
    )
    yield f"data: {sse_json.dumps({'type': 'done', 'agent_name': ', '.join(resolved_names), 'collab': True, 'client_turn_id': client_turn_id}, ensure_ascii=False)}\n\n"


# ==================== 数据模型 ====================

class AgentInfo(BaseModel):
    id: int
    type: str
    name: str
    role: str
    is_active: bool
    soul: Optional[Dict[str, Any]] = None
    tools: Optional[List[str]] = None
    skills: Optional[List[str]] = None
    system_prompt_preview: Optional[str] = None


class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    agent_names: List[str] = [DEFAULT_AGENT_TYPE]
    workspace_path: Optional[str] = None


class GitHubProjectCreate(BaseModel):
    repo_url: str
    name: Optional[str] = None
    description: Optional[str] = ""
    ref: Optional[str] = None
    agent_names: List[str] = [DEFAULT_AGENT_TYPE]

    @field_validator("repo_url")
    @classmethod
    def validate_repo_url(cls, value: str) -> str:
        repo_url = (value or "").strip()
        if not repo_url:
            raise ValueError("repo_url is required")
        return repo_url


class SkillImportRequest(BaseModel):
    source: str
    marketplace: Optional[str] = None
    skill_id: Optional[str] = None
    ref: Optional[str] = None
    subdir: Optional[str] = None
    force: bool = False

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        source = (value or "").strip()
        if not source:
            raise ValueError("source is required")
        return source


class SkillMarketplaceEnableRequest(BaseModel):
    enabled: bool = True
    bootstrap: bool = True


class ProjectFromChatCreate(ProjectCreate):
    source_chatroom_id: int


class ChatCreate(BaseModel):
    title: Optional[str] = None


class ChatUpdate(BaseModel):
    title: str


class ProjectSubchatCreate(BaseModel):
    title: Optional[str] = None


class ChatInfo(BaseModel):
    id: int
    title: str
    session_type: str
    is_visible_in_chat_list: bool
    project_id: Optional[int] = None
    agent_count: int = 0
    updated_at: str


class ProjectInfo(BaseModel):
    id: int
    name: str
    description: Optional[str]
    status: str
    display_order: int
    chatroom_id: Optional[int]
    default_chatroom_id: Optional[int]
    workspace_path: Optional[str] = None
    source_type: Optional[str] = None
    repo_url: Optional[str] = None
    repo_full_name: Optional[str] = None
    clone_ref: Optional[str] = None
    created_from_chatroom_id: Optional[int] = None
    agents: List[AgentInfo]


class ProjectSyncInfo(BaseModel):
    project: ProjectInfo
    updated: bool
    branch: Optional[str] = None
    head_commit: Optional[str] = None
    head_short: Optional[str] = None
    previous_head_commit: Optional[str] = None
    detached: bool = False
    summary: str


class ProjectUpdate(BaseModel):
    name: str


class ProjectReorderRequest(BaseModel):
    project_ids: List[int]


class MessageRequest(BaseModel):
    content: str
    client_turn_id: Optional[str] = None


class MessageResponse(BaseModel):
    id: int
    content: str
    agent_name: Optional[str]
    message_type: str
    created_at: str
    client_turn_id: Optional[str] = None


class ApprovalQueueDecisionRequest(BaseModel):
    note: Optional[str] = None
    rollback_to: Optional[str] = None
    resolved_by: Optional[str] = "user"


async def _publish_saved_chat_message(
    db: Session,
    chatroom_id: int,
    *,
    message_id: int,
    content: str,
    agent_name: Optional[str],
    message_type: str,
    created_at: Any,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    from routes.websocket import websocket_manager

    created_value = created_at.isoformat() if hasattr(created_at, "isoformat") else created_at
    room_payload = {
        "type": "chat_message",
        "chatroom_id": chatroom_id,
        "id": message_id,
        "content": content,
        "agent_name": agent_name,
        "message_type": message_type,
        "created_at": created_value,
        "client_turn_id": (metadata or {}).get("client_turn_id"),
    }
    await websocket_manager.broadcast_to_room(room_payload, chatroom_id)

    chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
    if not chatroom:
        return

    project = monitor_resolve_chatroom_project(db, chatroom)
    monitor_payload = serialize_monitor_message_item(
        message_id=message_id,
        chatroom_id=chatroom_id,
        chat_title=chatroom.title,
        project_id=project.id if project else None,
        project_name=project.name if project else None,
        agent_name=agent_name,
        content=content,
        message_type=message_type,
        created_at=created_value,
        metadata=metadata,
    )
    await websocket_manager.broadcast_to_topic(
        {
            "type": "monitor_message",
            "payload": monitor_payload,
        },
        "monitor",
    )



_RUNTIME_CARD_PUBLIC_OMITTED_FIELDS = ("system_prompt", "prompt_messages", "raw_response")


def _public_runtime_card_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return the runtime-card payload that is safe for realtime/chat replay."""
    public_payload = dict(payload)
    if public_payload.get("type") == "llm_call":
        omitted = any(public_payload.get(field) for field in _RUNTIME_CARD_PUBLIC_OMITTED_FIELDS)
        for field in _RUNTIME_CARD_PUBLIC_OMITTED_FIELDS:
            public_payload.pop(field, None)
        if omitted:
            public_payload["debug_payload_omitted"] = True
    return public_payload


async def _publish_runtime_card_event(
    db: Session,
    chatroom_id: int,
    *,
    runtime_message_id: int,
    created_at: Any,
    card_payload: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    from routes.websocket import websocket_manager

    created_value = created_at.isoformat() if hasattr(created_at, "isoformat") else created_at
    room_card_payload = dict(card_payload)
    room_card_payload.setdefault("created_at", created_value)
    room_card_payload.setdefault("runtime_message_id", runtime_message_id)

    await websocket_manager.broadcast_to_room(
        {
            "type": "runtime_card",
            "chatroom_id": chatroom_id,
            "card": room_card_payload,
        },
        chatroom_id,
    )

    chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
    if not chatroom:
        return

    project = monitor_resolve_chatroom_project(db, chatroom)
    monitor_payload = serialize_monitor_runtime_item(
        runtime_message_id=runtime_message_id,
        chatroom_id=chatroom_id,
        chat_title=chatroom.title,
        project_id=project.id if project else None,
        project_name=project.name if project else None,
        card=room_card_payload,
        created_at=created_value,
        metadata=metadata,
    )
    await websocket_manager.broadcast_to_topic(
        {
            "type": "monitor_runtime",
            "payload": monitor_payload,
        },
        "monitor",
    )


async def _store_runtime_card(chatroom_id: int, payload: Dict[str, Any]) -> Any:
    """Persist full runtime cards, but publish only the lightweight public payload."""
    card_payload = dict(payload)
    public_card_payload = _public_runtime_card_payload(card_payload)
    runtime_message = await chatroom_manager.send_message(
        chatroom_id=chatroom_id,
        agent_id=None,
        content=card_payload.get("type", "runtime_card"),
        message_type="runtime_card",
        metadata={"card": card_payload},
    )
    db = next(get_db())
    try:
        await _publish_runtime_card_event(
            db,
            chatroom_id,
            runtime_message_id=runtime_message.id,
            created_at=runtime_message.created_at,
            card_payload=public_card_payload,
            metadata={"card": public_card_payload, "client_turn_id": public_card_payload.get("client_turn_id")},
        )
    finally:
        db.close()
    return runtime_message


def _summarize_stream_error(error_message: str, limit: int = 240) -> str:
    text = str(error_message or "").strip()
    if not text:
        return "Unknown streaming error"
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


async def _persist_stream_failure(
    db: Session,
    *,
    chatroom_id: int,
    client_turn_id: Optional[str],
    error_message: str,
    agent_name: Optional[str] = None,
    agent_id: Optional[int] = None,
    detail: Optional[str] = None,
) -> Any:
    """
    Persist a visible fallback message plus a runtime error card for failed SSE turns.

    This ensures refresh/reconnect still shows why a turn terminated before a final reply
    could be saved.
    """
    safe_agent_name = (agent_name or default_agent_name(DEFAULT_AGENT_TYPE)).strip() or default_agent_name(DEFAULT_AGENT_TYPE)
    error_summary = _summarize_stream_error(error_message)
    detail_text = str(detail or error_message or "").strip() or error_summary
    failure_text = (
        "本轮执行中断，未生成最终答复。\n\n"
        f"错误摘要: {error_summary}"
    )

    try:
        db.rollback()
    except Exception:
        pass

    error_card = {
        "type": "agent_error",
        "source": "chatroom",
        "agent": safe_agent_name,
        "summary": "Stream failed before a final reply was saved.",
        "error": error_summary,
        "content": f"### Stream Failure\n\n- Agent: `{safe_agent_name}`\n- Error: `{error_summary}`\n\n```text\n{detail_text}\n```",
    }
    if client_turn_id:
        error_card["client_turn_id"] = client_turn_id

    await _store_runtime_card(chatroom_id, error_card)

    saved = await chatroom_manager.send_message(
        chatroom_id=chatroom_id,
        agent_id=agent_id,
        content=failure_text,
        message_type="text",
        metadata=_message_metadata_with_turn(
            client_turn_id,
            {
                "stream_failure": {
                    "agent": safe_agent_name,
                    "error": error_summary,
                }
            },
        ),
        agent_name=safe_agent_name,
    )
    await _publish_saved_chat_message(
        db,
        chatroom_id,
        message_id=saved.id,
        content=failure_text,
        agent_name=safe_agent_name,
        message_type="text",
        created_at=saved.created_at,
        metadata=_message_metadata_with_turn(
            client_turn_id,
            {
                "stream_failure": {
                    "agent": safe_agent_name,
                    "error": error_summary,
                }
            },
        ),
    )
    return saved


def _message_metadata_with_turn(client_turn_id: Optional[str], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    metadata = dict(extra or {})
    if client_turn_id:
        metadata["client_turn_id"] = client_turn_id
    return metadata


def _serialize_project_agents(db: Session, project_id: int) -> List[Agent]:
    assignments = db.query(AgentAssignment).filter(AgentAssignment.project_id == project_id).all()
    agent_ids = [assignment.agent_id for assignment in assignments]
    if not agent_ids:
        return []
    return db.query(Agent).filter(Agent.id.in_(agent_ids)).all()


def _open_workspace_path(workspace_path: str) -> None:
    resolved = Path(workspace_path).expanduser().resolve()
    if not resolved.exists():
        raise HTTPException(status_code=404, detail="Workspace path not found")

    if os.name == "nt":
        subprocess.Popen(["explorer", str(resolved)])
        return

    if os.getenv("WSL_DISTRO_NAME"):
        explorer = shutil.which("explorer.exe")
        wslpath = shutil.which("wslpath")
        if explorer and wslpath:
            windows_path = subprocess.check_output([wslpath, "-w", str(resolved)], text=True).strip()
            subprocess.Popen([explorer, windows_path])
            return

    opener = "open" if shutil.which("open") else shutil.which("xdg-open")
    if opener:
        subprocess.Popen([opener, str(resolved)])
        return

    raise HTTPException(status_code=500, detail="No file manager opener is available")


def _resolve_chatroom_project(db: Session, chatroom: Chatroom | None) -> Optional[Project]:
    current = chatroom
    visited_ids: set[int] = set()
    while current:
        if current.project_id:
            return db.query(Project).filter(Project.id == current.project_id).first()
        if not current.source_chatroom_id or current.source_chatroom_id in visited_ids:
            return None
        visited_ids.add(current.source_chatroom_id)
        current = db.query(Chatroom).filter(Chatroom.id == current.source_chatroom_id).first()
    return None


def _collect_descendant_chatrooms(db: Session, root_chatroom_ids: List[int]) -> List[Chatroom]:
    descendants: List[Chatroom] = []
    visited_ids: set[int] = set()
    pending_ids = [chatroom_id for chatroom_id in root_chatroom_ids if chatroom_id]

    while pending_ids:
        parent_id = pending_ids.pop(0)
        children = (
            db.query(Chatroom)
            .filter(Chatroom.source_chatroom_id == parent_id)
            .order_by(Chatroom.id.asc())
            .all()
        )
        for child in children:
            if child.id in visited_ids:
                continue
            visited_ids.add(child.id)
            descendants.append(child)
            pending_ids.append(child.id)

    return descendants


def _delete_chatrooms_with_messages(db: Session, chatrooms: List[Chatroom]) -> None:
    unique_chatrooms: List[Chatroom] = []
    seen_ids: set[int] = set()
    for chatroom in chatrooms:
        if not chatroom or chatroom.id in seen_ids:
            continue
        seen_ids.add(chatroom.id)
        unique_chatrooms.append(chatroom)

    if not unique_chatrooms:
        return

    chatroom_ids = [chatroom.id for chatroom in unique_chatrooms]
    task_run_ids = [
        row[0]
        for row in db.query(TaskRun.id).filter(TaskRun.chatroom_id.in_(chatroom_ids)).all()
    ]
    _delete_task_runs_by_ids(db, task_run_ids)
    db.query(Message).filter(Message.chatroom_id.in_(chatroom_ids)).delete(synchronize_session=False)
    for chatroom in unique_chatrooms:
        chatroom_manager.chatrooms.pop(chatroom.id, None)
        db.delete(chatroom)


def _delete_task_runs_by_ids(db: Session, task_run_ids: List[int]) -> None:
    unique_task_run_ids = [task_run_id for task_run_id in dict.fromkeys(task_run_ids) if task_run_id]
    if not unique_task_run_ids:
        return

    db.query(ApprovalQueueItem).filter(
        ApprovalQueueItem.task_run_id.in_(unique_task_run_ids)
    ).delete(synchronize_session=False)
    db.query(TaskRunEvent).filter(
        TaskRunEvent.task_run_id.in_(unique_task_run_ids)
    ).delete(synchronize_session=False)
    db.query(TaskRun).filter(TaskRun.id.in_(unique_task_run_ids)).delete(synchronize_session=False)


def _serialize_chat(db: Session, chatroom: Chatroom) -> ChatInfo:
    project = _resolve_chatroom_project(db, chatroom)
    agent_count = 0
    if project:
        agent_count = db.query(AgentAssignment).filter(AgentAssignment.project_id == project.id).count()

    return ChatInfo(
        id=chatroom.id,
        title=chatroom.title or "New Chat",
        session_type="project-bound" if project else (chatroom.session_type or "standalone"),
        is_visible_in_chat_list=bool(chatroom.is_visible_in_chat_list),
        project_id=project.id if project else None,
        agent_count=agent_count,
        updated_at=chatroom.created_at.isoformat() if chatroom.created_at else "",
    )


def _serialize_project(db: Session, project: Project) -> ProjectInfo:
    agents = _serialize_project_agents(db, project.id)
    default_chatroom = None
    if project.default_chatroom_id:
        default_chatroom = db.query(Chatroom).filter(Chatroom.id == project.default_chatroom_id).first()
    if default_chatroom is None:
        default_chatroom = db.query(Chatroom).filter(Chatroom.project_id == project.id).first()

    chatroom_id = default_chatroom.id if default_chatroom else None

    return ProjectInfo(
        id=project.id,
        name=project.name,
        description=project.description,
        status=project.status,
        display_order=int(project.display_order or 0),
        chatroom_id=chatroom_id,
        default_chatroom_id=chatroom_id,
        workspace_path=project.workspace_path,
        source_type=project.source_type,
        repo_url=project.repo_url,
        repo_full_name=project.repo_full_name,
        clone_ref=project.clone_ref,
        created_from_chatroom_id=default_chatroom.source_chatroom_id if default_chatroom else None,
        agents=[
            AgentInfo(
                id=agent.id,
                type=agent.type,
                name=agent_name_of(agent),
                role=agent.role,
                is_active=agent.is_active,
            )
            for agent in agents
        ],
    )


# ==================== Agent 相关 ====================

@router.get("/agents", response_model=List[AgentInfo])
async def list_agents(db: Session = Depends(get_db)):
    """获取所有可用 Agent 列表"""
    import json as _json2
    agents = db.query(Agent).filter(Agent.is_active == True).all()
    result = []
    for agent in agents:
        soul = {}
        tools = []
        skills = []
        try:
            soul = _json2.loads(agent.soul) if agent.soul else {}
        except Exception:
            pass
        try:
            tools = _json2.loads(agent.tools) if agent.tools else []
        except Exception:
            pass
        try:
            skills = _json2.loads(agent.skills) if agent.skills else []
        except Exception:
            pass
        preview = _agent_base_system_prompt(agent, agent_name_of(agent), agent.role)[:300]
        result.append(AgentInfo(
            id=agent.id, type=agent.type, name=agent_name_of(agent), role=agent.role,
            is_active=agent.is_active, soul=soul, tools=tools,
            skills=skills, system_prompt_preview=preview,
        ))
    return result


@router.get("/agents/{agent_id}", response_model=AgentInfo)
async def get_agent(agent_id: int, db: Session = Depends(get_db)):
    """获取 Agent 详情"""
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    import json as _json3
    soul = {}
    tools = []
    skills = []
    try:
        soul = _json3.loads(agent.soul) if agent.soul else {}
    except Exception:
        pass
    try:
        tools = _json3.loads(agent.tools) if agent.tools else []
    except Exception:
        pass
    try:
        skills = _json3.loads(agent.skills) if agent.skills else []
    except Exception:
        pass
    preview = _agent_base_system_prompt(agent, agent_name_of(agent), agent.role)[:300]

    return AgentInfo(
        id=agent.id, type=agent.type, name=agent_name_of(agent), role=agent.role,
        is_active=agent.is_active, soul=soul, tools=tools,
        skills=skills, system_prompt_preview=preview,
    )


@router.get("/agents/{agent_id}/memory")
async def get_agent_memory(agent_id: int, db: Session = Depends(get_db)):
    """获取 Agent 记忆信息"""
    from models.database import Memory
    
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    memories = db.query(Memory).filter(Memory.agent_id == agent_id).all()
    
    return {
        "agent_name": agent.name,
        "memory_count": len(memories),
        "memories": [
            {
                "id": m.id,
                "type": m.memory_type,
                "content": m.content[:200] + "..." if len(m.content) > 200 else m.content,
                "importance": m.importance,
                "created_at": m.created_at.isoformat()
            }
            for m in memories
        ]
    }


# ==================== 项目相关 ====================

@router.get("/projects", response_model=List[ProjectInfo])
async def list_projects(db: Session = Depends(get_db)):
    """获取所有项目列表"""
    projects = db.query(Project).order_by(Project.display_order.asc(), Project.id.asc()).all()
    return [_serialize_project(db, project) for project in projects]


@router.get("/chats", response_model=List[ChatInfo])
async def list_chats(db: Session = Depends(get_db)):
    """List visible standalone chats."""
    service = SessionService(db)
    return [_serialize_chat(db, chatroom) for chatroom in service.list_visible_chats()]


@router.post("/chats", response_model=ChatInfo)
async def create_chat(chat_create: ChatCreate, db: Session = Depends(get_db)):
    """Create a standalone chat."""
    service = SessionService(db)
    chatroom = service.create_standalone_chat(chat_create.title)
    return _serialize_chat(db, chatroom)


@router.delete("/chats/{chat_id}")
async def delete_chat(chat_id: int, db: Session = Depends(get_db)):
    """Delete a standalone chat and its messages."""
    chatroom = db.query(Chatroom).filter(Chatroom.id == chat_id).first()
    if not chatroom:
        raise HTTPException(status_code=404, detail="Chat not found")
    if chatroom.session_type != "standalone":
        raise HTTPException(status_code=400, detail="Only standalone chats can be deleted here")

    (
        db.query(Chatroom)
        .filter(Chatroom.source_chatroom_id == chatroom.id)
        .update({Chatroom.source_chatroom_id: None}, synchronize_session=False)
    )
    task_run_ids = [
        row[0]
        for row in db.query(TaskRun.id).filter(TaskRun.chatroom_id == chatroom.id).all()
    ]
    _delete_task_runs_by_ids(db, task_run_ids)
    db.query(Message).filter(Message.chatroom_id == chatroom.id).delete()
    db.delete(chatroom)
    chatroom_manager.chatrooms.pop(chatroom.id, None)
    db.commit()

    return {"message": "Chat deleted successfully"}


@router.put("/chats/{chat_id}", response_model=ChatInfo)
async def update_chat(chat_id: int, chat_update: ChatUpdate, db: Session = Depends(get_db)):
    """Rename a standalone chat."""
    chatroom = db.query(Chatroom).filter(Chatroom.id == chat_id).first()
    if not chatroom:
        raise HTTPException(status_code=404, detail="Chat not found")
    if chatroom.session_type != "standalone":
        raise HTTPException(status_code=400, detail="Only standalone chats can be renamed here")

    next_title = (chat_update.title or "").strip()
    if not next_title:
        raise HTTPException(status_code=400, detail="Chat title cannot be empty")

    chatroom.title = next_title
    db.add(chatroom)
    db.commit()
    db.refresh(chatroom)
    return _serialize_chat(db, chatroom)


def _validate_agent_names(agent_names: List[str]) -> None:
    registry = get_registry()
    valid_agent_names = registry.list_agents()

    for agent_name in agent_names:
        normalized = normalize_agent_type(agent_name)
        if normalized not in valid_agent_names:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid agent type: {agent_name}. Valid agents: {valid_agent_names}",
            )


def _normalize_agent_names(agent_names: List[str] | None) -> List[str]:
    normalized = [normalize_agent_type(agent_name) for agent_name in (agent_names or [DEFAULT_AGENT_TYPE]) if agent_name]
    deduped = list(dict.fromkeys(normalized))
    return deduped or [DEFAULT_AGENT_TYPE]


@router.post("/projects", response_model=ProjectInfo)
async def create_project(project_create: ProjectCreate, db: Session = Depends(get_db)):
    """创建新项目"""
    agent_names = _normalize_agent_names(project_create.agent_names)
    _validate_agent_names(agent_names)
    service = SessionService(db)
    project, _, _ = service.create_project_directly(
        name=project_create.name,
        description=project_create.description or "",
        agent_names=agent_names,
        workspace_path=project_create.workspace_path,
    )
    return _serialize_project(db, project)


@router.post("/projects/from-github", response_model=ProjectInfo)
async def create_project_from_github(project_create: GitHubProjectCreate, db: Session = Depends(get_db)):
    """Import a GitHub repository into a managed Catown workspace and create a project."""
    agent_names = _normalize_agent_names(project_create.agent_names)
    _validate_agent_names(agent_names)
    service = SessionService(db)
    project, _, _ = service.create_project_from_github(
        repo_url=project_create.repo_url,
        name=project_create.name,
        description=project_create.description or "",
        ref=project_create.ref,
        agent_names=agent_names,
    )
    return _serialize_project(db, project)


@router.get("/projects/self-bootstrap", response_model=ProjectInfo)
@router.post("/projects/self-bootstrap", response_model=ProjectInfo)
async def get_or_create_self_bootstrap_project(db: Session = Depends(get_db)):
    """Open the Catown repo itself as the default self-bootstrap project workspace."""
    service = SessionService(db)
    project, _, _ = service.get_or_create_self_bootstrap_project()
    return _serialize_project(db, project)


@router.post("/projects/{project_id}/sync", response_model=ProjectSyncInfo)
async def sync_project(project_id: int, db: Session = Depends(get_db)):
    """Sync a GitHub-backed project workspace with its upstream repository."""
    service = SessionService(db)
    project, sync_info = service.sync_github_project(project_id)
    return ProjectSyncInfo(project=_serialize_project(db, project), **sync_info)


@router.put("/projects/reorder", response_model=List[ProjectInfo])
async def reorder_projects(payload: ProjectReorderRequest, db: Session = Depends(get_db)):
    """Persist project sidebar ordering."""
    existing_projects = db.query(Project).order_by(Project.display_order.asc(), Project.id.asc()).all()
    if not existing_projects:
        return []

    existing_ids = [project.id for project in existing_projects]
    requested_ids = payload.project_ids or []
    requested_id_set = set(requested_ids)
    if len(requested_id_set) != len(requested_ids):
        raise HTTPException(status_code=400, detail="Duplicate project ids are not allowed")

    ordered_ids = [project_id for project_id in requested_ids if project_id in requested_id_set and project_id in existing_ids]
    ordered_ids.extend(project_id for project_id in existing_ids if project_id not in requested_id_set)

    project_map = {project.id: project for project in existing_projects}
    for index, project_id in enumerate(ordered_ids):
        project = project_map[project_id]
        project.display_order = index
        db.add(project)

    db.commit()
    reordered = db.query(Project).order_by(Project.display_order.asc(), Project.id.asc()).all()
    return [_serialize_project(db, project) for project in reordered]


@router.post("/projects/from-chat", response_model=ProjectInfo)
async def create_project_from_chat(project_create: ProjectFromChatCreate, db: Session = Depends(get_db)):
    """Create a project from the current standalone chat and copy its context."""
    agent_names = _normalize_agent_names(project_create.agent_names)
    _validate_agent_names(agent_names)
    service = SessionService(db)
    project, _, _ = service.create_project_from_chat(
        source_chatroom_id=project_create.source_chatroom_id,
        name=project_create.name,
        description=project_create.description or "",
        agent_names=agent_names,
        workspace_path=project_create.workspace_path,
    )
    return _serialize_project(db, project)


@router.get("/projects/{project_id}", response_model=ProjectInfo)
async def get_project(project_id: int, db: Session = Depends(get_db)):
    """获取项目详情"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return _serialize_project(db, project)


@router.get("/projects/{project_id}/chat", response_model=ChatInfo)
async def get_project_chat(project_id: int, db: Session = Depends(get_db)):
    """Return the hidden default chat for a project."""
    service = SessionService(db)
    chatroom = service.get_project_chat(project_id)
    return _serialize_chat(db, chatroom)


@router.post("/projects/{project_id}/subchats", response_model=ChatInfo)
async def create_project_subchat(
    project_id: int,
    payload: ProjectSubchatCreate,
    db: Session = Depends(get_db),
):
    """Create a visible sub chat linked to a project's hidden main chat."""
    service = SessionService(db)
    chatroom = service.create_project_subchat(project_id, payload.title)
    return _serialize_chat(db, chatroom)


@router.post("/projects/{project_id}/open-workspace")
async def open_project_workspace(project_id: int, db: Session = Depends(get_db)):
    """Open the project's workspace folder in the local file manager."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.workspace_path:
        raise HTTPException(status_code=400, detail="Project has no workspace path")

    _open_workspace_path(project.workspace_path)
    return {"message": "Workspace opened"}


@router.delete("/projects/{project_id}")
async def delete_project(project_id: int, db: Session = Depends(get_db)):
    """删除项目"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 清理子记录（模型未配置级联删除）
    db.query(AgentAssignment).filter(AgentAssignment.project_id == project_id).delete()
    root_chatrooms = db.query(Chatroom).filter(Chatroom.project_id == project_id).order_by(Chatroom.id.asc()).all()
    if not root_chatrooms and project.default_chatroom_id:
        fallback_chatroom = db.query(Chatroom).filter(Chatroom.id == project.default_chatroom_id).first()
        if fallback_chatroom:
            root_chatrooms = [fallback_chatroom]

    descendant_chatrooms = _collect_descendant_chatrooms(db, [chatroom.id for chatroom in root_chatrooms])
    # Delete descendants first so visible sub-chats do not survive a project deletion.
    _delete_chatrooms_with_messages(db, list(reversed(descendant_chatrooms)) + root_chatrooms)
    project_task_run_ids = [
        row[0]
        for row in db.query(TaskRun.id).filter(TaskRun.project_id == project_id).all()
    ]
    _delete_task_runs_by_ids(db, project_task_run_ids)

    # 删除项目
    db.delete(project)
    db.commit()
    
    return {"message": "Project deleted successfully"}


@router.put("/projects/{project_id}", response_model=ProjectInfo)
async def update_project(project_id: int, project_update: ProjectUpdate, db: Session = Depends(get_db)):
    """Rename a project and its hidden main chat."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    next_name = (project_update.name or "").strip()
    if not next_name:
        raise HTTPException(status_code=400, detail="Project name cannot be empty")

    project.name = next_name
    db.add(project)

    chatroom = db.query(Chatroom).filter(Chatroom.project_id == project_id).first()
    if chatroom:
        chatroom.title = next_name
        db.add(chatroom)

    db.commit()
    db.refresh(project)
    return _serialize_project(db, project)


# ==================== 聊天相关 ====================

@router.get("/chatrooms/{chatroom_id}/messages", response_model=List[MessageResponse])
async def get_messages(chatroom_id: int, limit: int = 50, db: Session = Depends(get_db)):
    """获取聊天室消息"""
    messages = await chatroom_manager.get_messages(chatroom_id, limit)
    
    return [
        MessageResponse(
            id=msg.id,
            content=msg.content,
            agent_name=msg.agent_name,
            message_type=msg.message_type,
            created_at=msg.created_at.isoformat(),
            client_turn_id=_message_client_turn_id(msg),
        )
        for msg in messages
    ]


@router.get("/chatrooms/{chatroom_id}/runtime-cards")
async def get_runtime_cards(chatroom_id: int, limit: int = 200, db: Session = Depends(get_db)):
    """获取聊天室历史 runtime cards，用于刷新后回放 agent 执行过程。"""
    chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
    if not chatroom:
        available_chatrooms = [row[0] for row in db.query(Chatroom.id).order_by(Chatroom.id.asc()).limit(20).all()]
        logger.warning(
            "[RuntimeCards] chatroom not found: id=%s available=%s",
            chatroom_id,
            available_chatrooms,
        )
        raise HTTPException(status_code=404, detail="Chatroom not found")

    rows = (
        db.query(Message)
        .filter(Message.chatroom_id == chatroom_id, Message.message_type == "runtime_card")
        .order_by(Message.created_at.asc())
        .limit(limit)
        .all()
    )

    cards: List[Dict[str, Any]] = []
    for row in rows:
        try:
            metadata = json.loads(row.metadata_json or "{}")
        except json.JSONDecodeError:
            metadata = {}
        card = metadata.get("card")
        if isinstance(card, dict):
            card_payload = _public_runtime_card_payload(dict(card))
            card_payload.setdefault("created_at", row.created_at.isoformat())
            card_payload.setdefault("runtime_message_id", row.id)
            cards.append(card_payload)

    return cards


@router.get("/chatrooms/{chatroom_id}/task-runs")
async def list_task_runs(
    chatroom_id: int,
    limit: int = 20,
    client_turn_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """List orchestration/task runs for a chatroom."""
    chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
    if not chatroom:
        raise HTTPException(status_code=404, detail="Chatroom not found")

    query = (
        db.query(TaskRun)
        .filter(TaskRun.chatroom_id == chatroom_id)
        .order_by(TaskRun.created_at.desc(), TaskRun.id.desc())
    )
    if client_turn_id:
        query = query.filter(TaskRun.client_turn_id == client_turn_id)

    return [serialize_task_run_summary(task_run) for task_run in query.limit(limit).all()]


@router.get("/task-runs/{task_run_id}")
async def get_task_run_detail(task_run_id: int, db: Session = Depends(get_db)):
    """Get a single orchestration/task run with ordered ledger events."""
    task_run = (
        db.query(TaskRun)
        .filter(TaskRun.id == task_run_id)
        .first()
    )
    if not task_run:
        raise HTTPException(status_code=404, detail="Task run not found")
    return serialize_task_run_detail(task_run)


@router.post("/task-runs/{task_run_id}/resume")
async def resume_task_run(task_run_id: int, db: Session = Depends(get_db)):
    """Manually resume an interrupted orchestration task run."""
    task_run = (
        db.query(TaskRun)
        .filter(TaskRun.id == task_run_id)
        .first()
    )
    if not task_run:
        raise HTTPException(status_code=404, detail="Task run not found")

    if (task_run.run_kind or "") not in RECOVERABLE_ORCHESTRATION_RUN_KINDS:
        raise HTTPException(status_code=400, detail="Only recoverable orchestration runs can be resumed.")

    if (task_run.status or "").lower() != "running":
        raise HTTPException(status_code=409, detail="Only running task runs can be resumed.")

    append_task_event(
        db,
        task_run,
        "task_run_manual_resume_requested",
        summary="Manual resume requested from the API.",
        payload={
            "task_run_id": task_run.id,
            "run_kind": task_run.run_kind,
            "status": task_run.status,
            "trigger": "manual",
        },
    )

    result = await _resume_interrupted_orchestration_task_run(task_run_id, trigger="manual")
    db.expire_all()
    refreshed = (
        db.query(TaskRun)
        .filter(TaskRun.id == task_run_id)
        .first()
    )
    if refreshed is None:
        raise HTTPException(status_code=404, detail="Task run not found")

    if not result.resumed:
        if result.reason in {"not_running", "leased"}:
            raise HTTPException(status_code=409, detail=result.detail or "Task run could not be resumed.")
        if result.reason == "not_recoverable":
            raise HTTPException(status_code=400, detail=result.detail or "Task run is not recoverable.")
        if result.reason == "not_found":
            raise HTTPException(status_code=404, detail=result.detail or "Task run not found.")
        raise HTTPException(
            status_code=500,
            detail=result.detail or refreshed.summary or "Task run recovery failed.",
        )

    detail = serialize_task_run_detail(refreshed)
    return {
        "message": "Task run resume completed.",
        "resumed": True,
        "status": refreshed.status,
        "task_run_id": refreshed.id,
        "detail": detail,
    }


def _queue_replay_resolution_payload(
    *,
    request_payload: Dict[str, Any],
    replay_result: Any = None,
    action_taken: str = "queue_resolved_only",
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "request_payload": request_payload,
        "resume_supported": bool(request_payload.get("resume_supported")),
        "action_taken": action_taken,
    }
    if replay_result is None:
        return payload

    payload.update(
        {
            "replay_attempted": True,
            "replay_status": getattr(replay_result, "status", None),
            "replay_success": bool(getattr(replay_result, "success", False)),
            "replay_blocked": bool(getattr(replay_result, "blocked", False)),
            "replay_blocked_kind": getattr(replay_result, "blocked_kind", None),
            "replay_result_preview": _compact_runtime_text(
                getattr(replay_result, "result", ""),
                limit=280,
            ),
        }
    )
    return payload


def _approval_queue_replay_round_payload(item: Any, request_payload: Dict[str, Any]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "replay": True,
        "replay_of_queue_item_id": getattr(item, "id", None),
    }
    for key in ("pipeline_id", "pipeline_run_id", "pipeline_stage_id", "stage_name", "display_name"):
        if request_payload.get(key) is not None:
            payload[key] = request_payload.get(key)
    return payload


async def _replay_runtime_blocked_tool_queue_item(
    db: Session,
    item: Any,
    request_payload: Dict[str, Any],
):
    from tools import tool_registry

    tool_name = str(request_payload.get("tool_name") or getattr(item, "target_name", "") or "").strip()
    arguments_text = str(request_payload.get("arguments") or "{}")
    if not tool_name:
        return build_tool_result_record(
            tool_call_id=f"queue-replay-{getattr(item, 'id', 'tool')}",
            tool_name=getattr(item, "target_name", "tool"),
            arguments=arguments_text,
            result="Error executing blocked tool replay: missing tool_name.",
            success=False,
        )

    try:
        loaded_arguments = json.loads(arguments_text or "{}")
        if not isinstance(loaded_arguments, dict):
            raise ValueError("Tool arguments must be a JSON object.")
    except Exception as exc:
        return build_tool_result_record(
            tool_call_id=f"queue-replay-{getattr(item, 'id', tool_name)}",
            tool_name=tool_name,
            arguments=arguments_text,
            result=f"Error executing blocked tool replay: invalid arguments ({exc}).",
            success=False,
        )

    chatroom = db.query(Chatroom).filter(Chatroom.id == getattr(item, "chatroom_id", None)).first()
    if chatroom is None:
        return build_tool_result_record(
            tool_call_id=f"queue-replay-{getattr(item, 'id', tool_name)}",
            tool_name=tool_name,
            arguments=arguments_text,
            result="Error executing blocked tool replay: chatroom no longer exists.",
            success=False,
        )

    project = _resolve_chatroom_project(db, chatroom)
    agents = _serialize_project_agents(db, project.id) if project else _list_global_agents(db)
    agent = find_agent_by_type(agents, getattr(item, "agent_name", None))
    runtime_kwargs = _tool_runtime_kwargs(agent, chatroom.id, project)

    try:
        tool_result = await tool_registry.execute(
            tool_name,
            **loaded_arguments,
            **runtime_kwargs,
            __catown_approval_granted=True,
        )
        tool_result_text = str(tool_result) if tool_result is not None else "(no output)"
        tool_success = True
    except Exception as exc:
        tool_result_text = f"Error executing {tool_name}: {exc}"
        tool_success = False

    return build_tool_result_record(
        tool_call_id=f"queue-replay-{getattr(item, 'id', tool_name)}",
        tool_name=tool_name,
        arguments=arguments_text,
        result=tool_result_text,
        success=tool_success,
    )


async def _replay_blocked_tool_queue_item(
    db: Session,
    item: Any,
    request_payload: Dict[str, Any],
):
    if getattr(item, "pipeline_run_id", None) is not None or request_payload.get("pipeline_run_id") is not None:
        from pipeline.engine import replay_blocked_tool_queue_item as replay_pipeline_blocked_tool_queue_item

        return await replay_pipeline_blocked_tool_queue_item(db, item)
    return await _replay_runtime_blocked_tool_queue_item(db, item, request_payload)


def _build_tool_replay_followup_context(item: Any, replay_result: Any) -> str:
    tool_name = str(getattr(replay_result, "tool_name", None) or getattr(item, "target_name", None) or "tool").strip() or "tool"
    result_preview = _compact_runtime_text(getattr(replay_result, "result", ""), limit=400)
    return (
        "Approved tool replay completed.\n"
        f"- Tool: {tool_name}\n"
        f"- Status: {getattr(replay_result, 'status', 'unknown')}\n"
        f"- Result: {result_preview}\n"
        "Continue from this result. Do not rerun the same tool call unless the user explicitly asks or the result shows it did not complete."
    )


def _reopen_task_run_for_followup(db: Session, task_run: Optional[TaskRun]) -> Optional[TaskRun]:
    if task_run is None:
        return None
    task_run.status = "running"
    task_run.completed_at = None
    db.add(task_run)
    db.commit()
    db.refresh(task_run)
    return task_run


async def _publish_replayed_tool_result_message(
    db: Session,
    item: Any,
    replay_result: Any,
    *,
    client_turn_id: Optional[str],
):
    chatroom_id = getattr(item, "chatroom_id", None)
    if chatroom_id is None:
        return None

    metadata = _message_metadata_with_turn(
        client_turn_id,
        {
            "tool_call_id": getattr(replay_result, "tool_call_id", None),
            "queue_item_id": getattr(item, "id", None),
            "tool_name": getattr(replay_result, "tool_name", None) or getattr(item, "target_name", None),
            "replayed": True,
            "approval_queue_replay": True,
        },
    )
    saved = await chatroom_manager.send_message(
        chatroom_id=chatroom_id,
        agent_id=None,
        content=getattr(replay_result, "result", "") or "(no output)",
        message_type="tool_result",
        metadata=metadata,
        agent_name=getattr(item, "agent_name", None),
    )
    await _publish_saved_chat_message(
        db,
        chatroom_id,
        message_id=saved.id,
        content=saved.content,
        agent_name=saved.agent_name,
        message_type=saved.message_type,
        created_at=saved.created_at,
        metadata=metadata,
    )
    return saved


async def _continue_runtime_after_approved_tool_replay(
    db: Session,
    item: Any,
    request_payload: Dict[str, Any],
    replay_result: Any,
):
    task_run = get_task_run(db, getattr(item, "task_run_id", None))
    if task_run is None:
        return {"followup_attempted": False, "followup_status": "skipped", "followup_reason": "task_run_missing"}
    if getattr(item, "chatroom_id", None) is None:
        return {"followup_attempted": False, "followup_status": "skipped", "followup_reason": "chatroom_missing"}
    if getattr(item, "pipeline_run_id", None) is not None or request_payload.get("pipeline_run_id") is not None:
        return {"followup_attempted": False, "followup_status": "skipped", "followup_reason": "pipeline_queue_item"}
    if not bool(getattr(replay_result, "success", False)) or bool(getattr(replay_result, "blocked", False)):
        return {"followup_attempted": False, "followup_status": "skipped", "followup_reason": "replay_not_actionable"}

    followup_context = _build_tool_replay_followup_context(item, replay_result)
    saved = await _publish_replayed_tool_result_message(
        db,
        item,
        replay_result,
        client_turn_id=getattr(task_run, "client_turn_id", None),
    )
    _reopen_task_run_for_followup(db, task_run)
    append_task_event(
        db,
        task_run,
        "approval_queue_item_followup_triggered",
        agent_name=item.agent_name,
        message_id=getattr(saved, "id", None),
        summary=f"Continuing agent turn after approved replay of {getattr(replay_result, 'tool_name', item.target_name or 'tool')}.",
        payload={
            "queue_item_id": getattr(item, "id", None),
            "tool_name": getattr(replay_result, "tool_name", None),
            "tool_call_id": getattr(replay_result, "tool_call_id", None),
            "message_id": getattr(saved, "id", None),
        },
    )
    try:
        await trigger_agent_response(
            getattr(item, "chatroom_id", None),
            task_run.user_request or "",
            getattr(task_run, "client_turn_id", None),
            task_run_id=task_run.id,
            extra_context=followup_context,
        )
    except Exception as exc:
        append_task_event(
            db,
            task_run,
            "approval_queue_item_followup_failed",
            agent_name=item.agent_name,
            summary=f"Approved replay follow-up failed for {getattr(replay_result, 'tool_name', item.target_name or 'tool')}.",
            payload={
                "queue_item_id": getattr(item, "id", None),
                "tool_name": getattr(replay_result, "tool_name", None),
                "error": str(exc),
            },
        )
        return {
            "followup_attempted": True,
            "followup_status": "failed",
            "followup_error": str(exc),
            "followup_message_id": getattr(saved, "id", None),
        }

    return {
        "followup_attempted": True,
        "followup_status": "continued",
        "followup_message_id": getattr(saved, "id", None),
    }


@router.get("/approval-queue")
async def get_approval_queue(
    status: Optional[str] = None,
    queue_kind: Optional[str] = None,
    chatroom_id: Optional[int] = None,
    project_id: Optional[int] = None,
    task_run_id: Optional[int] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    items = list_approval_queue_items(
        db,
        status=(status or "").strip() or None,
        queue_kind=(queue_kind or "").strip() or None,
        chatroom_id=chatroom_id,
        project_id=project_id,
        task_run_id=task_run_id,
        limit=limit,
    )
    return [serialize_approval_queue_item(item) for item in items]


@router.get("/approval-queue/{item_id}")
async def get_approval_queue_detail(item_id: int, db: Session = Depends(get_db)):
    item = get_approval_queue_item(db, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Approval queue item not found")
    return serialize_approval_queue_item(item)


@router.post("/approval-queue/{item_id}/approve")
async def approve_approval_queue_item(
    item_id: int,
    req: ApprovalQueueDecisionRequest | None = None,
    db: Session = Depends(get_db),
):
    item = get_approval_queue_item(db, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Approval queue item not found")
    if (item.status or "").lower() != "pending":
        raise HTTPException(status_code=409, detail="Only pending approval queue items can be approved.")

    request_payload = _json_column_payload(item.request_payload_json)
    resolution_note = ((req.note if req else None) or "").strip()
    resolved_by = ((req.resolved_by if req else None) or "user").strip() or "user"

    if (item.target_kind or "") == "pipeline_gate":
        pipeline_id = request_payload.get("pipeline_id")
        if not pipeline_id:
            raise HTTPException(status_code=400, detail="Pipeline gate approval item is missing pipeline_id.")
        try:
            await pipeline_engine.approve(db, int(pipeline_id))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        db.expire_all()
        refreshed = get_approval_queue_item(db, item_id)
        if refreshed is None:
            raise HTTPException(status_code=404, detail="Approval queue item not found")
        return serialize_approval_queue_item(refreshed)

    resolution_payload = _queue_replay_resolution_payload(
        request_payload=request_payload,
        action_taken="queue_resolved_only",
    )
    if (item.target_kind or "") == "tool" and bool(request_payload.get("resume_supported")):
        replay_result = await _replay_blocked_tool_queue_item(db, item, request_payload)
        task_run = get_task_run(db, item.task_run_id)
        try:
            replay_turn = max(1, int(request_payload.get("turn") or 1))
        except (TypeError, ValueError):
            replay_turn = 1
        record_runner_tool_round(
            db,
            task_run,
            agent_name=(item.agent_name or "").strip() or "agent",
            turn=replay_turn,
            tool_names=[replay_result.tool_name],
            tool_results=[replay_result],
            summary=f"Replayed blocked tool {replay_result.tool_name} after approval.",
            payload=_approval_queue_replay_round_payload(item, request_payload),
        )
        resolution_payload = _queue_replay_resolution_payload(
            request_payload=request_payload,
            replay_result=replay_result,
            action_taken="tool_replayed",
        )
        resolution_payload.update(
            await _continue_runtime_after_approved_tool_replay(
                db,
                item,
                request_payload,
                replay_result,
            )
        )

    resolved = resolve_approval_queue_item(
        db,
        item,
        status="approved",
        resolved_by=resolved_by,
        resolution_note=resolution_note or f"Approved {item.target_kind or 'action'} from the API.",
        resolution_payload=resolution_payload,
    )
    task_run = get_task_run(db, item.task_run_id)
    append_task_event(
        db,
        task_run,
        "approval_queue_item_resolved",
        agent_name=item.agent_name,
        summary=f"Approved queue item for {item.target_name or item.target_kind}.",
        payload={
            "queue_item_id": resolved.id if resolved is not None else item.id,
            "queue_kind": item.queue_kind,
            "target_kind": item.target_kind,
            "target_name": item.target_name,
            "status": "approved",
            "resolved_by": resolved_by,
            "resume_supported": bool(request_payload.get("resume_supported")),
            "action_taken": resolution_payload.get("action_taken"),
            "replay_status": resolution_payload.get("replay_status"),
            "replay_success": resolution_payload.get("replay_success"),
            "replay_blocked": resolution_payload.get("replay_blocked"),
            "replay_blocked_kind": resolution_payload.get("replay_blocked_kind"),
        },
    )
    return serialize_approval_queue_item(resolved or item)


@router.post("/approval-queue/{item_id}/reject")
async def reject_approval_queue_item(
    item_id: int,
    req: ApprovalQueueDecisionRequest | None = None,
    db: Session = Depends(get_db),
):
    item = get_approval_queue_item(db, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Approval queue item not found")
    if (item.status or "").lower() != "pending":
        raise HTTPException(status_code=409, detail="Only pending approval queue items can be rejected.")

    request_payload = _json_column_payload(item.request_payload_json)
    resolution_note = ((req.note if req else None) or "").strip()
    resolved_by = ((req.resolved_by if req else None) or "user").strip() or "user"

    if (item.target_kind or "") == "pipeline_gate":
        pipeline_id = request_payload.get("pipeline_id")
        if not pipeline_id:
            raise HTTPException(status_code=400, detail="Pipeline gate approval item is missing pipeline_id.")
        try:
            await pipeline_engine.reject(db, int(pipeline_id), req.rollback_to if req else None)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        db.expire_all()
        refreshed = get_approval_queue_item(db, item_id)
        if refreshed is None:
            raise HTTPException(status_code=404, detail="Approval queue item not found")
        return serialize_approval_queue_item(refreshed)

    resolved = resolve_approval_queue_item(
        db,
        item,
        status="rejected",
        resolved_by=resolved_by,
        resolution_note=resolution_note or f"Rejected {item.target_kind or 'action'} from the API.",
        resolution_payload={
            "request_payload": request_payload,
            "resume_supported": bool(request_payload.get("resume_supported")),
            "action_taken": "queue_resolved_only",
            "rollback_to": req.rollback_to if req else None,
        },
    )
    task_run = get_task_run(db, item.task_run_id)
    append_task_event(
        db,
        task_run,
        "approval_queue_item_resolved",
        agent_name=item.agent_name,
        summary=f"Rejected queue item for {item.target_name or item.target_kind}.",
        payload={
            "queue_item_id": resolved.id if resolved is not None else item.id,
            "queue_kind": item.queue_kind,
            "target_kind": item.target_kind,
            "target_name": item.target_name,
            "status": "rejected",
            "resolved_by": resolved_by,
            "resume_supported": bool(request_payload.get("resume_supported")),
        },
    )
    return serialize_approval_queue_item(resolved or item)


@router.get("/chatrooms/{chatroom_id}/visibility")
async def get_chatroom_visibility(chatroom_id: int, db: Session = Depends(get_db)):
    """获取聊天室消息可见度配置"""
    chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
    if not chatroom:
        raise HTTPException(status_code=404, detail="Chatroom not found")
    return {"chatroom_id": chatroom_id, "message_visibility": chatroom.message_visibility or "all"}


@router.put("/chatrooms/{chatroom_id}/visibility")
async def set_chatroom_visibility(
    chatroom_id: int,
    body: dict,
    db: Session = Depends(get_db)
):
    """设置聊天室消息可见度: all=所有agent可见, target=仅目标agent可见"""
    chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
    if not chatroom:
        raise HTTPException(status_code=404, detail="Chatroom not found")
    visibility = body.get("message_visibility")
    if visibility not in ("all", "target"):
        raise HTTPException(status_code=400, detail="message_visibility must be 'all' or 'target'")
    chatroom.message_visibility = visibility
    db.commit()
    return {"chatroom_id": chatroom_id, "message_visibility": visibility}


@router.post("/chatrooms/{chatroom_id}/messages", response_model=MessageResponse)
async def send_message(chatroom_id: int, message: MessageRequest, db: Session = Depends(get_db)):
    """发送消息到聊天室"""
    logger.info(f"[API] send_message called: chatroom_id={chatroom_id}, content={message.content[:50]}...")
    
    # 发送用户消息
    response_msg = await chatroom_manager.send_message(
        chatroom_id=chatroom_id,
        agent_id=None,  # None 表示用户
        content=message.content,
        message_type="text",
        metadata=_message_metadata_with_turn(message.client_turn_id),
    )
    await _publish_saved_chat_message(
        db,
        chatroom_id,
        message_id=response_msg.id,
        content=response_msg.content,
        agent_name=None,
        message_type=response_msg.message_type,
        created_at=response_msg.created_at,
        metadata=_message_metadata_with_turn(message.client_turn_id),
    )
    
    logger.info(f"[API] User message saved: id={response_msg.id}")

    chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
    project = _resolve_chatroom_project(db, chatroom) if chatroom else None
    task_run = create_task_run(
        db,
        chatroom_id=chatroom_id,
        project_id=project.id if project else None,
        origin_message_id=response_msg.id,
        client_turn_id=message.client_turn_id,
        run_kind="chat_turn",
        user_request=message.content,
        initiator="user",
    )
    append_task_event(
        db,
        task_run,
        "user_message_saved",
        message_id=response_msg.id,
        summary="User message saved for execution.",
        payload={
            "content_preview": _compact_runtime_text(message.content, limit=220),
            "client_turn_id": message.client_turn_id,
        },
    )

    # 触发 Agent 响应（同步等待，方便调试）
    try:
        await trigger_agent_response(
            chatroom_id,
            message.content,
            message.client_turn_id,
            task_run_id=task_run.id,
        )
        logger.info(f"[API] Agent response completed")
    except Exception as e:
        logger.info(f"[API] Agent response error: {e}")
        import traceback
        traceback.print_exc()
    
    return MessageResponse(
        id=response_msg.id,
        content=response_msg.content,
        agent_name=response_msg.agent_name,
        message_type=response_msg.message_type,
        created_at=response_msg.created_at.isoformat(),
        client_turn_id=message.client_turn_id,
    )


@router.post("/chatrooms/{chatroom_id}/messages/stream")
async def send_message_stream(chatroom_id: int, message: MessageRequest, request: Request):
    """
    发送消息到聊天室（SSE 流式响应）

    返回 SSE 事件流：
    - data: {"type": "content", "delta": "..."}      — LLM 生成的文本增量
    - data: {"type": "tool_start", "tool": "..."}     — 开始执行工具
    - data: {"type": "tool_result", "tool": "...", "result": "..."} — 工具执行完毕
    - data: {"type": "done", "agent_name": "...", "message_id": 123} — 全部完成
    - data: {"type": "error", "error": "..."}         — 出错
    """
    import asyncio
    import json as _json
    import time as _time
    import uuid as _uuid

    request_started_at = _time.perf_counter()
    sse_chunks: list[str] = []
    stream_failed = False
    stream_error = ""
    flow_id = f"sse-{_uuid.uuid4().hex[:12]}"
    flow_seq = 0

    def _safe_headers(headers: dict[str, Any]) -> dict[str, str]:
        result: dict[str, str] = {}
        for key, value in headers.items():
            normalized = str(key).lower()
            if normalized in {"authorization", "cookie", "set-cookie"}:
                continue
            result[str(key)] = str(value)
        return result

    def _record_stream_network_event(success: bool, error: str = "") -> None:
        protocol = request.scope.get("scheme", "http").upper()
        http_version = str(request.scope.get("http_version") or "").strip()
        if http_version:
            protocol = f"{protocol}/{http_version}"
        response_text = "".join(sse_chunks)
        monitor_network_buffer.append(
            {
                "category": "frontend_backend",
                "source": "backend",
                "protocol": protocol,
                "from_entity": f"Frontend ({request.headers.get('x-catown-client', 'home')})",
                "to_entity": "Backend SSE",
                "request_direction": f"Frontend ({request.headers.get('x-catown-client', 'home')}) -> Backend SSE",
                "response_direction": f"Backend SSE -> Frontend ({request.headers.get('x-catown-client', 'home')})",
                "flow_id": flow_id,
                "flow_kind": "frontend_backend_sse",
                "aggregated": True,
                "method": request.method,
                "url": str(request.url),
                "host": request.url.hostname or "",
                "path": request.url.path,
                "success": success,
                "status_code": 200 if success else 500,
                "request_bytes": len(_json.dumps(message.model_dump(), ensure_ascii=False).encode("utf-8")),
                "response_bytes": len(response_text.encode("utf-8")),
                "duration_ms": int((_time.perf_counter() - request_started_at) * 1000),
                "content_type": "text/event-stream",
                "preview": f"{request.method} {request.url.path}",
                "error": error,
                "raw_request": _json.dumps(message.model_dump(), ensure_ascii=False)[:40000],
                "raw_response": response_text[:40000],
                "request_headers": _safe_headers(dict(request.headers)),
                "response_headers": {
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                    "Content-Type": "text/event-stream",
                },
                "metadata": {
                    "client_turn_id": message.client_turn_id,
                    "http_version": request.scope.get("http_version"),
                    "streaming": True,
                    "flow_id": flow_id,
                    "flow_kind": "frontend_backend_sse",
                    "aggregated": True,
                },
            }
        )

    def _record_stream_chunk(chunk: str) -> None:
        nonlocal flow_seq
        protocol = request.scope.get("scheme", "http").upper()
        http_version = str(request.scope.get("http_version") or "").strip()
        if http_version:
            protocol = f"{protocol}/{http_version}"
        flow_seq += 1
        monitor_network_buffer.append(
            {
                "category": "frontend_backend",
                "source": "backend",
                "protocol": protocol,
                "from_entity": f"Frontend ({request.headers.get('x-catown-client', 'home')})",
                "to_entity": "Backend SSE",
                "request_direction": f"Frontend ({request.headers.get('x-catown-client', 'home')}) -> Backend SSE",
                "response_direction": f"Backend SSE -> Frontend ({request.headers.get('x-catown-client', 'home')})",
                "flow_id": flow_id,
                "flow_kind": "frontend_backend_sse",
                "flow_seq": flow_seq,
                "aggregated": False,
                "method": request.method,
                "url": str(request.url),
                "host": request.url.hostname or "",
                "path": request.url.path,
                "success": True,
                "request_bytes": 0,
                "response_bytes": len(chunk.encode("utf-8")),
                "duration_ms": int((_time.perf_counter() - request_started_at) * 1000),
                "content_type": "text/event-stream",
                "preview": chunk[:280],
                "raw_request": "",
                "raw_response": chunk[:40000],
                "request_headers": {},
                "response_headers": {},
                "metadata": {
                    "client_turn_id": message.client_turn_id,
                    "http_version": request.scope.get("http_version"),
                    "streaming": True,
                    "flow_id": flow_id,
                    "flow_kind": "frontend_backend_sse",
                    "flow_seq": flow_seq,
                    "aggregated": False,
                },
            }
        )

    async def raw_event_generator():
        nonlocal stream_failed, stream_error
        from models.database import get_db as _get_db
        from tools import tool_registry
        from tools.file_operations import reset_active_workspace, set_active_workspace
        from llm.client import get_llm_client_for_agent, get_default_llm_client, clear_client_cache
        async def _sse_card(event_type, data):
            """格式化卡片事件 SSE，附带 source=chatroom"""
            payload = dict(data)
            payload["type"] = event_type
            payload["source"] = "chatroom"
            if message.client_turn_id:
                payload["client_turn_id"] = message.client_turn_id
            await _store_runtime_card(chatroom_id, payload)
            public_payload = _public_runtime_card_payload(payload)
            return f"data: {_json.dumps(public_payload, ensure_ascii=False)}\n\n"

        db = next(_get_db())
        workspace_token = None
        active_agent_name: Optional[str] = None
        active_agent_id: Optional[int] = None
        final_message_saved = False
        task_run: Optional[TaskRun] = None

        def _mark_active_agent(agent_name: str, agent_id: Optional[int]) -> None:
            nonlocal active_agent_name, active_agent_id
            active_agent_name = agent_name
            active_agent_id = agent_id

        try:
            # 1. 保存用户消息
            user_msg = await chatroom_manager.send_message(
                chatroom_id=chatroom_id,
                agent_id=None,
                content=message.content,
                message_type="text",
                metadata=_message_metadata_with_turn(message.client_turn_id),
            )
            await _publish_saved_chat_message(
                db,
                chatroom_id,
                message_id=user_msg.id,
                content=user_msg.content,
                agent_name=None,
                message_type=user_msg.message_type,
                created_at=user_msg.created_at,
                metadata=_message_metadata_with_turn(message.client_turn_id),
            )

            yield f"data: {_json.dumps({'type': 'user_saved', 'id': user_msg.id, 'client_turn_id': message.client_turn_id})}\n\n"

            # 2. 获取聊天室和项目
            chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
            if not chatroom:
                yield f"data: {_json.dumps({'type': 'error', 'error': 'No chatroom found'})}\n\n"
                return

            project = _resolve_chatroom_project(db, chatroom)
            task_run = create_task_run(
                db,
                chatroom_id=chatroom_id,
                project_id=project.id if project else None,
                origin_message_id=user_msg.id,
                client_turn_id=message.client_turn_id,
                run_kind="chat_turn_stream",
                user_request=message.content,
                initiator="user",
            )
            append_task_event(
                db,
                task_run,
                "user_message_saved",
                message_id=user_msg.id,
                summary="User message saved for streaming execution.",
                payload={
                    "content_preview": _compact_runtime_text(message.content, limit=220),
                    "client_turn_id": message.client_turn_id,
                },
            )
            workspace_token = set_active_workspace(project.workspace_path if project and project.workspace_path else None)
            if not project:
                mentioned_names = [normalize_agent_type(name) for name in re.findall(r'@(\w+)', message.content)] if '@' in message.content else []
                if len(mentioned_names) > 1:
                    agents = _list_global_agents(db)
                    update_task_run(db, task_run, run_kind="multi_agent_orchestration_stream")
                    append_task_event(
                        db,
                        task_run,
                        "runtime_mode_selected",
                        summary="Selected standalone multi-agent streaming orchestration mode.",
                        payload={"agents": mentioned_names, "project_id": None},
                    )
                    async for chunk in _stream_multi_agent_orchestration(
                        db=db,
                        chatroom=chatroom,
                        project=None,
                        agents=agents,
                        agent_names=mentioned_names,
                        user_message=message.content,
                        client_turn_id=message.client_turn_id,
                        sse_json=_json,
                        sse_card=_sse_card,
                        set_active_agent=_mark_active_agent,
                        task_run=task_run,
                    ):
                        yield chunk
                    return

                standalone_target = _resolve_standalone_target_agent(db, message.content)
                standalone_agent_name = _agent_type(standalone_target) if standalone_target else DEFAULT_AGENT_TYPE
                update_task_run(
                    db,
                    task_run,
                    run_kind="standalone_assistant_stream",
                    target_agent_name=standalone_agent_name,
                )
                standalone_stream_policy = _build_single_agent_runner_policy(
                    run_kind="standalone_assistant_stream",
                    agent_name=standalone_agent_name,
                    project_id=None,
                    tool_names=[],
                    streaming=True,
                    standalone=True,
                )
                append_task_event(
                    db,
                    task_run,
                    "runtime_mode_selected",
                    summary="Selected standalone assistant streaming mode.",
                    payload={
                        "project_id": None,
                        "runner_policy": standalone_stream_policy.to_payload(),
                    },
                )
                async for chunk in _stream_standalone_assistant_response(
                    db=db,
                    chatroom_id=chatroom_id,
                    user_message=message.content,
                    sse_json=_json,
                    client_turn_id=message.client_turn_id,
                    task_run=task_run,
                ):
                    yield chunk
                return

            # 3. 解析 @mention（支持多 Agent 编排）
            mentioned_names = []
            if '@' in message.content:
                mentioned_names = [normalize_agent_type(name) for name in re.findall(r'@(\w+)', message.content)]

            # 多 Agent 模式：转入 Codex 风格编排
            if len(mentioned_names) > 1:
                assignments = db.query(AgentAssignment).filter(
                    AgentAssignment.project_id == project.id
                ).all()
                agent_ids = [a.agent_id for a in assignments]
                agents = db.query(Agent).filter(Agent.id.in_(agent_ids)).all()
                update_task_run(db, task_run, run_kind="multi_agent_orchestration_stream")
                append_task_event(
                    db,
                    task_run,
                    "runtime_mode_selected",
                    summary="Selected project multi-agent streaming orchestration mode.",
                    payload={"agents": mentioned_names, "project_id": project.id},
                )
                async for chunk in _stream_multi_agent_orchestration(
                    db=db,
                    chatroom=chatroom,
                    project=project,
                    agents=agents,
                    agent_names=mentioned_names,
                    user_message=message.content,
                    client_turn_id=message.client_turn_id,
                    sse_json=_json,
                    sse_card=_sse_card,
                    set_active_agent=_mark_active_agent,
                    task_run=task_run,
                ):
                    yield chunk
                return

            # 单 Agent 模式（原有逻辑）
            target_agent_name = mentioned_names[0] if mentioned_names else None

            # 4. 获取项目 Agents
            assignments = db.query(AgentAssignment).filter(
                AgentAssignment.project_id == project.id
            ).all()
            agent_ids = [a.agent_id for a in assignments]
            agents = db.query(Agent).filter(Agent.id.in_(agent_ids)).all()

            target_agent = None
            if target_agent_name:
                target_agent = find_agent_by_type(agents, target_agent_name)
                # @mentioned agent 不在项目中 → 从全局查找并自动分配
                if not target_agent:
                    global_agent = _find_db_agent_by_type(db, target_agent_name)
                    if global_agent:
                        logger.info(f"[Agent] Auto-assigning '{target_agent_name}' to project '{project.name}'")
                        assignment = AgentAssignment(project_id=project.id, agent_id=global_agent.id)
                        db.add(assignment)
                        db.commit()
                        target_agent = global_agent
                        agents.append(global_agent)
            if not target_agent:
                target_agent = find_agent_by_type(agents, DEFAULT_AGENT_TYPE) or (agents[0] if agents else None)
            if not target_agent:
                append_task_event(
                    db,
                    task_run,
                    "task_run_failed",
                    summary="No target agent resolved for streaming execution.",
                    payload={"project_id": project.id},
                )
                complete_task_run(db, task_run, status="failed", summary="No target agent resolved.")
                yield f"data: {_json.dumps({'type': 'error', 'error': 'No agent available'})}\n\n"
                return

            target_agent_label = agent_name_of(target_agent)
            active_agent_name = target_agent_label
            active_agent_id = target_agent.id
            available_tools = tool_registry.list_tools()
            project_single_agent_stream_policy = _build_single_agent_runner_policy(
                run_kind="project_single_agent_stream",
                agent_name=target_agent_label,
                project_id=project.id,
                tool_names=available_tools,
                streaming=True,
                standalone=False,
            )
            update_task_run(
                db,
                task_run,
                run_kind="project_single_agent_stream",
                target_agent_name=target_agent_label,
            )
            append_task_event(
                db,
                task_run,
                "runtime_mode_selected",
                agent_name=target_agent_label,
                summary="Selected project single-agent streaming execution mode.",
                payload={
                    "project_id": project.id,
                    "target_agent_name": target_agent_label,
                    "runner_policy": project_single_agent_stream_policy.to_payload(),
                },
            )

            _ensure_collaboration_context(agents, chatroom_id)

            # 5. 构建该 Agent 的消息上下文
            llm_client = get_llm_client_for_agent(_agent_type(target_agent))
            recent_messages = await chatroom_manager.get_messages(chatroom_id, limit=10)
            turn_state = TurnContextState()
            tool_schemas = tool_registry.get_schemas()
            runtime_kwargs = _tool_runtime_kwargs(target_agent, chatroom_id, project)
            record_agent_turn_started(
                db,
                task_run,
                agent_name=target_agent_label,
                summary=f"{target_agent_label} started a streaming turn.",
                payload={
                    "target_agent_name": target_agent_label,
                    "client_turn_id": message.client_turn_id,
                    "stage_policy": (
                        project_single_agent_stream_policy.stages[0].to_payload()
                        if project_single_agent_stream_policy.stages
                        else None
                    ),
                },
            )

            final_content = ""
            def _assemble_single_agent_stream_messages(current_turn_state: TurnContextState) -> List[Dict[str, Any]]:
                return _assemble_chat_messages(
                    db=db,
                    agent=target_agent,
                    agent_name=target_agent_label,
                    model_id=getattr(llm_client, "model", ""),
                    chatroom=chatroom,
                    project=project,
                    agents=agents,
                    recent_messages=recent_messages,
                    user_message=message.content,
                    available_tools=available_tools,
                    history_limit=6,
                    turn_state=current_turn_state,
                )

            async def _execute_single_agent_stream_tool(tool_name, tool_args, tool_args_str, tool_call_id, tool_index, turn_index):
                return await tool_registry.execute(
                    tool_name,
                    **tool_args,
                    **runtime_kwargs,
                )

            async def _on_single_agent_stream_tool_round(frame, normalized_tool_calls, tool_results, current_turn_state):
                record_runner_tool_round(
                    db,
                    task_run,
                    agent_name=target_agent_label,
                    turn=frame.turn_index,
                    tool_names=[tool_call["function"]["name"] for tool_call in normalized_tool_calls],
                    tool_results=tool_results,
                    summary=f"{target_agent_label} completed a streaming tool round.",
                )

            def _build_single_agent_stream_llm_card(frame, response_content, raw_tool_calls, tool_call_previews, raw_event):
                return _build_llm_card_payload(
                    agent_name=target_agent_label,
                    llm_client=llm_client,
                    turn=frame.turn_index,
                    duration_ms=int((raw_event.get("timings", {}) or {}).get("completed_ms") or ((time.time() - frame.llm_started_at) * 1000)),
                    system_prompt=frame.system_prompt,
                    prompt_messages=frame.prompt_snapshot,
                    response_content=response_content,
                    tool_call_previews=tool_call_previews,
                    raw_tool_calls=raw_tool_calls,
                    usage=raw_event.get("usage"),
                    finish_reason=raw_event.get("finish_reason"),
                    timings=raw_event.get("timings"),
                )

            async for event in iter_stream_turn_events(
                llm_client=llm_client,
                tools=tool_schemas,
                turn_state=turn_state,
                agent_name=target_agent_label,
                client_turn_id=message.client_turn_id,
                assemble_messages=_assemble_single_agent_stream_messages,
                execute_tool=_execute_single_agent_stream_tool,
                build_llm_runtime_card=_build_single_agent_stream_llm_card,
                snapshot_messages=_snapshot_llm_messages,
                preview_tool_calls=_preview_tool_calls,
                format_prompt_messages=_format_json_block,
                tool_result_success=_tool_result_succeeded,
                max_turns=MAX_TOOL_ITERATIONS,
                on_tool_round=_on_single_agent_stream_tool_round,
            ):
                if event["type"] == "runtime_card":
                    yield await _sse_card(event["card_type"], event["payload"])
                    continue
                if event["type"] == "turn_complete":
                    final_content = event.get("content") or ""
                    continue
                yield f"data: {_json.dumps(event, ensure_ascii=False)}\n\n"

            # 7. 保存最终响应
            if not final_content:
                final_content = "(Agent returned empty response)"

            agent_response = await chatroom_manager.send_message(
                chatroom_id=chatroom_id,
                agent_id=target_agent.id,
                content=final_content,
                message_type="text",
                metadata=_message_metadata_with_turn(message.client_turn_id),
                agent_name=target_agent_label
            )

            await _publish_saved_chat_message(
                db,
                chatroom_id,
                message_id=agent_response.id,
                content=final_content,
                agent_name=target_agent_label,
                message_type="text",
                created_at=agent_response.created_at,
                metadata=_message_metadata_with_turn(message.client_turn_id),
            )
            final_message_saved = True
            record_agent_turn_completed(
                db,
                task_run,
                agent_name=target_agent_label,
                message_id=agent_response.id,
                response_content=final_content,
                summary=f"{target_agent_label} completed the streaming turn.",
            )
            complete_task_run(
                db,
                task_run,
                summary=_compact_runtime_text(final_content, limit=280),
            )

            yield f"data: {_json.dumps({'type': 'done', 'agent_name': target_agent_label, 'message_id': agent_response.id, 'client_turn_id': message.client_turn_id})}\n\n"

            # 异步提取记忆
            if len(final_content) > 30:
                asyncio.create_task(_extract_memories(
                    agent_id=target_agent.id,
                    agent_type=_agent_type(target_agent),
                    user_message=message.content,
                    agent_response=final_content
                ))

        except Exception as e:
            logger.error(f"[SSE] Stream error: {e}")
            traceback.print_exc()
            stream_failed = True
            stream_error = str(e)
            if task_run is not None and (task_run.status or "running") == "running":
                append_task_event(
                    db,
                    task_run,
                    "task_run_failed",
                    agent_name=active_agent_name,
                    summary=f"Streaming execution failed: {e}",
                    payload={"error": str(e)},
                )
                complete_task_run(db, task_run, status="failed", summary=str(e))
            if final_message_saved:
                yield f"data: {_json.dumps({'type': 'error', 'error': str(e)})}\n\n"
            else:
                try:
                    saved = await _persist_stream_failure(
                        db,
                        chatroom_id=chatroom_id,
                        client_turn_id=message.client_turn_id,
                        error_message=str(e),
                        agent_name=active_agent_name,
                        agent_id=active_agent_id,
                        detail=traceback.format_exc(),
                    )
                    yield f"data: {_json.dumps({'type': 'done', 'agent_name': active_agent_name or default_agent_name(DEFAULT_AGENT_TYPE), 'message_id': saved.id, 'client_turn_id': message.client_turn_id}, ensure_ascii=False)}\n\n"
                except Exception as persist_exc:
                    logger.error(f"[SSE] Failed to persist stream failure: {persist_exc}")
                    traceback.print_exc()
                    yield f"data: {_json.dumps({'type': 'error', 'error': str(e)})}\n\n"
        finally:
            if workspace_token is not None:
                reset_active_workspace(workspace_token)
            db.close()

    async def event_generator():
        queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=256)
        client_connected = True

        async def producer():
            nonlocal client_connected, stream_failed, stream_error
            try:
                async for chunk in raw_event_generator():
                    sse_chunks.append(chunk)
                    _record_stream_chunk(chunk)
                    if '"type": "error"' in chunk:
                        stream_failed = True
                    if not client_connected:
                        continue
                    try:
                        queue.put_nowait(chunk)
                    except asyncio.QueueFull:
                        try:
                            queue.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                        try:
                            queue.put_nowait(chunk)
                        except asyncio.QueueFull:
                            # If the client is lagging badly, drop intermediate chunks
                            # and let the persisted runtime cards/final message catch up.
                            pass
                if stream_failed:
                    _record_stream_network_event(False, stream_error or "stream_error")
                else:
                    _record_stream_network_event(True)
            finally:
                if client_connected:
                    try:
                        queue.put_nowait(None)
                    except asyncio.QueueFull:
                        pass

        asyncio.create_task(producer())

        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        except asyncio.CancelledError:
            client_connected = False
            raise
        finally:
            client_connected = False

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


# ==================== 状态相关 ====================

@router.get("/status")
async def get_status(db: Session = Depends(get_db)):
    """获取系统状态"""
    agent_count = db.query(Agent).count()
    project_count = db.query(Project).count()
    chatroom_count = db.query(Chatroom).count()
    message_count = db.query(Message).count()
    
    return {
        "status": "healthy",
        "version": "1.0.0",
        "stats": {
            "agents": agent_count,
            "projects": project_count,
            "chatrooms": chatroom_count,
            "messages": message_count
        },
        "features": {
            "llm_enabled": True,
            "websocket_enabled": True,
            "tools_enabled": True,
            "memory_enabled": True
        }
    }


@router.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "ok"}


# ==================== 配置相关 ====================

@router.get("/config")
async def get_config():
    """
    获取配置信息（唯一来源：agents.json）

    返回：
    - global_llm: 全局 LLM 配置（Agent 未配置时的 fallback）
    - orchestration: 编排调度配置（如 sidecar agent types）
    - agents: 各 Agent 的完整配置
    - agent_llm_configs: 各 Agent 实际生效的 LLM 配置摘要
    - server: 服务器配置
    - features: 功能开关
    """
    from pathlib import Path

    config = {
        "server": {
            "host": os.getenv("HOST", "0.0.0.0"),
            "port": int(os.getenv("PORT", "8000"))
        },
        "llm": {
            "base_url": os.getenv("LLM_BASE_URL", ""),
            "model": os.getenv("LLM_MODEL", ""),
            "has_api_key": bool(os.getenv("LLM_API_KEY", ""))
        },
        "global_llm": {},
        "features": {
            "llm_enabled": True,
            "websocket_enabled": True,
            "tools_enabled": True,
            "memory_enabled": True
        },
        "orchestration": _effective_orchestration_config(),
        "agents": {},
        "agent_llm_configs": {}
    }

    # 从 agents.json 加载（唯一配置源）
    agents_config_file = Path(settings.AGENT_CONFIG_FILE)
    if agents_config_file.exists():
        try:
            with open(agents_config_file, 'r', encoding='utf-8') as f:
                agents_config = json.load(f)

            # 全局 LLM 配置
            config["global_llm"] = agents_config.get("global_llm", {})
            config["orchestration"] = _effective_orchestration_config(agents_config)

            agents_data = dict(agents_config.get("agents", {}))
            if "assistant" in agents_data and DEFAULT_AGENT_TYPE not in agents_data:
                agents_data[DEFAULT_AGENT_TYPE] = agents_data.pop("assistant")
            for agent_type, agent_data in agents_data.items():
                raw_name = agent_data.get("name")
                agent_data["name"] = (
                    default_agent_name(agent_type)
                    if is_legacy_default_agent_name(raw_name, agent_type)
                    else str(raw_name).strip()
                )
            config["agents"] = agents_data

            # 全局 provider 摘要（用于显示 fallback 来源）
            global_provider = agents_config.get("global_llm", {}).get("provider", {})
            global_model = agents_config.get("global_llm", {}).get("default_model", "")
            if not global_model:
                gm = global_provider.get("models", [])
                if gm:
                    global_model = gm[0].get("id", "")

            # 提取各 Agent 实际生效的 LLM 配置摘要
            for agent_name, agent_data in agents_data.items():
                provider = agent_data.get("provider", {})
                default_model = agent_data.get("default_model", "")
                models = provider.get("models", [])

                # 判断是否使用 Agent 自身配置还是全局 fallback
                has_own_provider = bool(provider.get("baseUrl", ""))
                if has_own_provider:
                    effective_model = default_model or (models[0]["id"] if models else "")
                    effective_url = provider.get("baseUrl", "")
                else:
                    effective_model = global_model
                    effective_url = global_provider.get("baseUrl", "")

                config["agent_llm_configs"][agent_name] = {
                    "baseUrl": effective_url,
                    "model": effective_model,
                    "hasApiKey": bool(provider.get("apiKey", "") if has_own_provider else global_provider.get("apiKey", "")),
                    "models": [m["id"] for m in models] if has_own_provider else [m["id"] for m in global_provider.get("models", [])],
                    "source": "agent" if has_own_provider else "global"
                }
        except Exception as e:
            logger.warning(f"Failed to load agents.json: {e}")

    return config


@router.post("/config")
async def update_config(config: LLMConfigModel):
    """
    更新 LLM 配置（验证通过的配置）

    配置将通过 LLMConfigModel 验证：
    - api_key 不能为空
    - base_url 必须是有效的 URL
    - temperature 必须在 0-2 之间
    - max_tokens 必须在 1-100000 之间
    """
    return {
        "message": "Configuration validated successfully",
        "config": config.model_dump()
    }


@router.put("/config/global")
async def update_global_llm_config(config: Dict[str, Any]):
    """
    更新全局 LLM 配置（global_llm 段）

    请求体：
    {
        "provider": {
            "baseUrl": "https://api.openai.com/v1",
            "apiKey": "sk-...",
            "models": [{"id": "gpt-4", ...}]
        },
        "default_model": "gpt-4"
    }
    """
    from pathlib import Path

    config_file = Path(settings.AGENT_CONFIG_FILE)
    try:
        # 读取现有配置
        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        else:
            data = {"agents": {}}

        # 更新全局配置
        data["global_llm"] = config
        config_file.parent.mkdir(parents=True, exist_ok=True)

        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        # 清空 LLM 客户端缓存
        clear_client_cache()

        return {"message": "Global LLM config updated", "global_llm": config}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update config: {e}")


@router.put("/config/orchestration")
async def update_orchestration_config(config: OrchestrationConfigModel):
    """
    Update runtime orchestration config stored in agents.json.

    Request body:
    {
        "sidecar_agent_types": ["tester"]
    }

    An empty list disables sidecar scheduling entirely.
    """
    from pathlib import Path

    config_file = Path(settings.AGENT_CONFIG_FILE)
    try:
        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        else:
            data = {"agents": {}}

        data["orchestration"] = config.model_dump()
        config_file.parent.mkdir(parents=True, exist_ok=True)

        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        return {"message": "Orchestration config updated", "orchestration": data["orchestration"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update orchestration config: {e}")


@router.put("/config/agent/{agent_name}")
async def update_agent_llm_config(agent_name: str, config: Dict[str, Any]):
    """
    更新指定 Agent 的完整配置

    请求体：
    {
        "provider": {
            "baseUrl": "https://api.openai.com/v1",
            "apiKey": "sk-...",
            "models": [{"id": "gpt-4", ...}]
        },
        "default_model": "gpt-4"
    }

    支持更新 provider/default_model/role/soul/tools/skills。
    设置 provider 为空对象 {} 可清除 Agent 级 LLM 配置，回退到全局。
    """
    from pathlib import Path

    config_file = Path(settings.AGENT_CONFIG_FILE)
    agent_name = normalize_agent_type(agent_name)
    try:
        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        else:
            data = {"agents": {}}

        agents = data.get("agents", {})
        if "assistant" in agents and DEFAULT_AGENT_TYPE not in agents:
            agents[DEFAULT_AGENT_TYPE] = agents.pop("assistant")
        if agent_name not in agents:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")

        # 更新 Agent 的完整配置字段
        for field_name in ("provider", "default_model", "role", "soul", "tools", "skills"):
            if field_name in config:
                agents[agent_name][field_name] = config[field_name]

        config_file.parent.mkdir(parents=True, exist_ok=True)
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        # 清空该 Agent 的 LLM 客户端缓存
        clear_client_cache()

        return {
            "message": f"Agent '{agent_name}' LLM config updated",
            "agent": agents[agent_name]
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update config: {e}")


@router.post("/config/reload")
async def reload_config():
    """
    重新加载 agents.json 配置（清空 LLM 客户端缓存）

    用于外部修改 agents.json 后通知服务生效，无需重启。
    """
    clear_client_cache()

    # 重新注册 Agent
    from agents.registry import register_builtin_agents
    register_builtin_agents()

    return {"message": "Configuration reloaded from agents.json"}


@router.get("/skills")
async def list_skills():
    """List canonical skill packages."""
    return {
        "skills": list(load_skill_registry(settings.SKILLS_DIR).values()),
        "skills_dir": str(settings.SKILLS_DIR),
    }


@router.get("/skills/marketplaces")
async def list_skill_marketplaces():
    """List configured skill marketplaces."""
    return {
        "marketplaces": list_marketplaces(settings.SKILL_MARKETPLACES_CONFIG_FILE),
        "config_file": str(settings.SKILL_MARKETPLACES_CONFIG_FILE),
    }


@router.put("/skills/marketplaces/{marketplace_id}")
async def update_skill_marketplace(marketplace_id: str, request: SkillMarketplaceEnableRequest):
    """Enable or disable a skill marketplace. Enabling may bootstrap its CLI."""
    try:
        return set_marketplace_enabled(
            marketplace_id,
            request.enabled,
            bootstrap=request.bootstrap,
            config_file=settings.SKILL_MARKETPLACES_CONFIG_FILE,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=424, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Failed to update skill marketplace")
        raise HTTPException(status_code=502, detail=f"Failed to update skill marketplace: {e}")


@router.post("/skills/import")
async def import_skill(request: SkillImportRequest):
    """Import a public skill package from a hub URL, GitHub repo, zip, or raw SKILL.md."""
    try:
        skill = import_skill_from_marketplace(
            source=request.source,
            skills_dir=settings.SKILLS_DIR,
            marketplace=request.marketplace,
            skill_id=request.skill_id,
            ref=request.ref,
            subdir=request.subdir,
            force=request.force,
        )
        return {"message": "Skill imported", "skill": skill}
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=424, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Failed to import skill")
        raise HTTPException(status_code=502, detail=f"Failed to import skill: {e}")


@router.post("/config/test")
async def test_agent_config(agent_name: str = DEFAULT_AGENT_TYPE):
    """
    测试指定 Agent 的 LLM 连接

    从 agents.json 读取该 Agent 的 provider 配置并发送测试请求。
    """
    from llm.client import _load_agent_provider
    from openai import AsyncOpenAI

    agent_name = normalize_agent_type(agent_name)
    provider = _load_agent_provider(agent_name)
    if not provider:
        raise HTTPException(
            status_code=404,
            detail=f"No provider config found for agent '{agent_name}' in agents.json"
        )

    try:
        client = AsyncOpenAI(
            api_key=provider["api_key"],
            base_url=provider["base_url"]
        )
        response = await client.chat.completions.create(
            model=provider["model"],
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=5
        )
        return {
            "status": "success",
            "agent": agent_name,
            "model": provider["model"],
            "baseUrl": provider["base_url"]
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Connection failed: {str(e)}")


# ==================== Tools 相关 ====================

@router.get("/tools")
async def list_tools():
    """获取所有可用的工具列表"""
    from tools import tool_registry
    
    tools = []
    for name in tool_registry.list_tools():
        tool = tool_registry.get(name)
        if tool:
            tools.append({
                "name": tool.name,
                "description": tool.description,
                "schema": tool.get_schema()
            })
    
    return {"tools": tools, "count": len(tools)}


@router.post("/tools/{tool_name}/execute")
async def execute_tool(tool_name: str, arguments: Dict[str, Any]):
    """执行指定的工具"""
    from tools import tool_registry
    
    try:
        result = await tool_registry.execute(tool_name, **arguments)
        return {"success": True, "result": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ==================== Collaboration 相关 ====================

@router.get("/collaboration/status")
async def get_collaboration_status():
    """获取协作系统状态"""
    from agents.collaboration import collaboration_coordinator
    
    return {
        "active_collaborators": len(collaboration_coordinator.collaborators),
        "chatrooms": len(collaboration_coordinator.chatroom_agents),
        "pending_tasks": len(collaboration_coordinator.task_registry),
        "status": "active"
    }


@router.get("/collaboration/chatrooms/{chatroom_id}/status")
async def get_chatroom_collaboration_status(chatroom_id: int):
    """获取聊天室的协作状态"""
    from agents.collaboration import collaboration_coordinator
    
    status = collaboration_coordinator.get_chatroom_status(chatroom_id)
    return status


@router.get("/collaboration/tasks/{task_id}")
async def get_task_status(task_id: str):
    """获取任务状态"""
    from agents.collaboration import collaboration_coordinator
    
    task = collaboration_coordinator.get_task_status(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status,
        "assigned_to": task.assigned_to_agent_id,
        "result": task.result,
        "created_at": task.created_at.isoformat(),
        "completed_at": task.completed_at.isoformat() if task.completed_at else None
    }


@router.post("/collaboration/delegate")
async def delegate_task_to_agent(
    target_agent_name: str,
    task_title: str,
    task_description: str,
    chatroom_id: int,
    db: Session = Depends(get_db)
):
    """委托任务给指定 Agent"""
    from agents.collaboration import collaboration_coordinator, CollaborationTask, TaskStatus, uuid
    from tools import tool_registry
    
    # 查找目标 Agent
    target_agent_type = normalize_agent_type(target_agent_name)
    target_agent = _find_db_agent_by_type(db, target_agent_type)
    if not target_agent:
        raise HTTPException(status_code=404, detail=f"Agent '{target_agent_type}' not found")
    
    # 创建任务
    task = CollaborationTask(
        id=str(uuid.uuid4()),
        title=task_title,
        description=task_description,
        status=TaskStatus.DELEGATED,
        created_by_agent_id=0,  # User
        assigned_to_agent_id=target_agent.id,
        chatroom_id=chatroom_id
    )
    
    collaboration_coordinator.task_registry[task.id] = task
    
    return {
        "task_id": task.id,
        "status": "delegated",
        "assigned_to": target_agent_type
    }


@router.get("/collaboration/tasks")
async def list_collaboration_tasks(chatroom_id: Optional[int] = None):
    """列出协作任务"""
    from agents.collaboration import collaboration_coordinator
    
    tasks = list(collaboration_coordinator.task_registry.values())
    
    if chatroom_id:
        tasks = [t for t in tasks if t.chatroom_id == chatroom_id]
    
    return {
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "status": t.status,
                "assigned_to": t.assigned_to_agent_id,
                "created_at": t.created_at.isoformat()
            }
            for t in tasks
        ],
        "count": len(tasks)
    }
