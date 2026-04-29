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
from functools import lru_cache, partial
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session
from typing import Callable, List, Optional, Dict, Any
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
    OrchestrationHandoffDelivery,
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
)
from services.chat_publish import publish_saved_chat_message
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
    build_chat_context_selector as shared_build_chat_context_selector,
)
from services.chat_runtime import (
    PreparedChatTurnRuntime,
    assemble_runtime_chat_messages,
    build_tool_runtime_kwargs,
    prepare_chat_turn_runtime,
)
from services.orchestration_scheduler import (
    DEFAULT_SIDECAR_AGENT_TYPES,
    OrchestrationRuntimeQueue,
    build_orchestration_schedule,
)
from services.turn_state import TurnContextState, build_tool_result_record, build_turn_state_from_checkpoint_snapshot, normalize_tool_call
from services.session_service import SessionService
from services.run_ledger import (
    append_task_event,
    describe_checkpoint_continuation_state,
    build_task_run_checkpoint_snapshot,
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
    claim_approval_queue_resolution_lease,
    get_approval_queue_item,
    list_approval_queue_items,
    resolve_approval_queue_item,
    serialize_approval_queue_item,
)
from services.approval_replay import (
    approval_queue_item_has_pipeline_cursor,
    build_approval_queue_item_resolved_event_payload,
    build_approval_queue_replay_round_payload,
    build_followup_continued_payload,
    build_followup_failed_event_payload,
    build_followup_failed_payload,
    build_followup_skipped_payload,
    build_followup_triggered_event_payload,
    build_queue_replay_resolution_payload,
    build_queue_rejection_resolution_payload,
    build_tool_replay_followup_context,
    load_approval_queue_request_payload,
    parse_replay_arguments,
    build_replay_tool_result_record,
    replay_result_is_actionable,
    resolve_replay_arguments_text,
    resolve_replay_tool_name,
)
from services.tool_governance import tool_result_succeeded as shared_tool_result_succeeded
from services.task_run_control import TaskRunCancelledError, raise_if_task_run_cancelled
from services.runner_policy import (
    compile_orchestration_run_policy,
    compile_single_agent_run_policy,
    find_stage_policy,
)
from services.runtime_event_helpers import build_context_compaction_callback, build_runtime_event_payload
from services.memory_extraction import (
    extract_agent_memories,
    schedule_agent_memory_extraction,
)
from services.stream_turn_executor import iter_stream_turn_events
from services.stream_runtime_persistence import (
    public_runtime_card_payload,
    store_runtime_card,
)
from services.single_agent_session_finalizer import (
    finalize_single_agent_session_failure,
)
from services.single_agent_session_orchestrator import (
    build_single_agent_raw_execution_inputs,
    build_single_agent_raw_runtime_inputs,
    build_single_agent_stream_runtime_profile_from_runtime,
    build_single_agent_sync_runtime_profile_from_runtime,
    iter_managed_single_agent_stream_runtime_profile,
    run_managed_single_agent_sync_runtime_profile,
)
from services.single_agent_session_runner import (
    build_single_agent_sync_raw_execution_inputs,
    SingleAgentSessionRunnerDeps,
)
from services.single_agent_stream_session import (
    build_single_agent_stream_raw_execution_inputs,
)
from services.stream_transport import (
    iter_rendered_stream_turn_events,
    render_sse_payload,
    render_chatroom_runtime_card_sse,
    render_stream_turn_event,
)
from services.nonstream_turn_executor import execute_non_stream_turn_loop
from services.subagent_lifecycle import cancellable_subagents_from_lifecycle
from services.orchestration_events import (
    record_orchestration_started,
    record_scheduler_plan_created,
    record_scheduler_recovery_state_rebuilt,
    record_scheduler_step_cancelled,
    record_scheduler_step_completed,
    record_scheduler_step_dispatched,
    record_scheduler_step_failed,
    record_scheduler_step_resumed,
    record_task_run_recovery_started,
    scheduler_event_payload,
    scheduler_plan_payload,
)
from services.orchestration_handoffs import (
    build_orchestration_handoff,
    build_orchestration_previous_work,
    compact_runtime_text as compact_orchestration_text,
    record_orchestration_handoffs,
)
from services.orchestration_inbox import has_orchestration_handoffs_for_task_run
from services.orchestration_guards import (
    fail_orchestration_preflight,
    fail_recovery_guard,
)
from services.orchestration_recovery_lease import (
    RecoveryLeaseClaimResult,
    RecoveryLeaseLostError,
    claim_recovery_lease,
    ensure_recovery_lease,
)
from services.orchestration_finalizer import (
    fail_orchestration_task_run,
    finalize_orchestration_task_run,
    summarize_orchestration_result,
)
from services.orchestration_agent_turn import (
    OrchestrationAgentTurnDeps,
    StreamOrchestrationAgentTurnDeps,
    iter_stream_orchestration_agent_turn_events,
    run_orchestration_agent_turn,
)
from services.orchestration_step_state import OrchestrationStepOutputState, record_orchestration_step_output
from services.orchestration_step_completion import complete_orchestration_scheduler_step
from services.orchestration_runtime_runner import (
    NonstreamOrchestrationRuntimeDeps,
    run_nonstream_orchestration_runtime,
)
from services.orchestration_recovery_runner import (
    OrchestrationRecoveryRuntimeDeps,
    run_orchestration_recovery_runtime,
)
from services.orchestration_recovery_prepare import (
    PreparedOrchestrationRecoveryContext,
    prepare_orchestration_recovery_context,
)
from services.orchestration_stream_runner import (
    StreamOrchestrationRuntimeDeps,
    iter_stream_orchestration_session_events,
    iter_stream_orchestration_runtime_events,
    render_stream_runtime_event,
)

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


@dataclass
class PreparedOrchestrationRuntime:
    targets: List[Any]
    resolved_agents: List[Agent]
    available_tools: List[str]
    plan: Any | None
    runner_policy: Any | None


@dataclass
class PreparedStandaloneTurnRuntime:
    llm_client: Any
    assistant_name: str
    assistant_label: str
    assistant_id: Optional[int]
    recent_messages: List[Any]
    turn_state: TurnContextState

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


def _build_context_compaction_callback(
    db: Session,
    task_run: Optional[TaskRun],
    *,
    agent_name: str,
    extra_payload: Optional[Dict[str, Any]] = None,
) -> Callable[[Dict[str, Any]], None]:
    if task_run is None:
        return lambda diagnostics: None

    return build_context_compaction_callback(
        emit_event=lambda event_type, summary, payload: append_task_event(
            db,
            task_run,
            event_type,
            agent_name=agent_name,
            summary=summary,
            payload=payload,
        ),
        agent_name=agent_name,
        extra_payload=extra_payload,
        summary_noun="context",
    )


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
    checkpoint_snapshot: Optional[Dict[str, Any]] = None,
):
    """Generate a plain assistant reply for standalone chats."""
    chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
    if not chatroom:
        logger.debug("[ No chatroom found for standalone response")
        return

    runtime = await _prepare_standalone_turn_runtime(
        db=db,
        chatroom_id=chatroom_id,
        user_message=user_message,
        checkpoint_snapshot=checkpoint_snapshot,
        previous_agent_work=extra_context,
        recent_message_limit=20,
    )
    standalone_policy = _build_single_agent_runner_policy(
        run_kind="standalone_assistant",
        agent_name=runtime.assistant_name,
        project_id=None,
        tool_names=[],
        streaming=False,
        standalone=True,
    )

    record_agent_turn_started(
        db,
        task_run,
        agent_name=runtime.assistant_name,
        summary=f"{runtime.assistant_name} started a standalone assistant turn.",
        payload=build_runtime_event_payload(
            client_turn_id=client_turn_id,
            stage_policy=standalone_policy.stages[0] if standalone_policy.stages else None,
        ),
    )

    compaction_callback = _build_context_compaction_callback(
        db,
        task_run,
        agent_name=runtime.assistant_name,
        extra_payload={
            "run_kind": "standalone_assistant",
            "client_turn_id": client_turn_id,
        },
    )
    context_messages = assemble_runtime_chat_messages(
        db=db,
        agent=None,
        agent_name=runtime.assistant_name,
        model_id=getattr(runtime.llm_client, "model", ""),
        chatroom=chatroom,
        project=None,
        agents=[],
        recent_messages=runtime.recent_messages,
        user_message=user_message,
        history_limit=10,
        standalone_note="This is a standalone chat. Reply directly, be concise, and help the user explore before creating a project if needed.",
        extra_context=extra_context,
        turn_state=runtime.turn_state,
        on_compaction=compaction_callback,
    )
    standalone_runtime_inputs = build_single_agent_raw_runtime_inputs(
        db=db,
        task_run=task_run,
        chatroom_id=chatroom_id,
        client_turn_id=client_turn_id,
        agent_id=runtime.assistant_id,
        agent_name=runtime.assistant_name,
        agent_type=runtime.assistant_name,
        user_message=user_message,
        save_message=chatroom_manager.send_message,
        publish_message=publish_saved_chat_message,
        record_turn_completed=record_agent_turn_completed,
        message_metadata=_message_metadata_with_turn,
        compact_summary=lambda content: _compact_runtime_text(content, limit=280),
        completion_summary=f"{runtime.assistant_name} completed the standalone turn.",
        failure_summary=lambda error: f"Agent response failed: {error}",
        extract_memories=extract_agent_memories,
        stream_failure_message_metadata=_message_metadata_with_turn,
    )

    await run_managed_single_agent_sync_runtime_profile(
        build_single_agent_sync_runtime_profile_from_runtime(
            runtime_inputs=standalone_runtime_inputs,
            execution_inputs=build_single_agent_raw_execution_inputs(
                execution=build_single_agent_sync_raw_execution_inputs(
                    execute_turn=lambda: runtime.llm_client.chat(context_messages, temperature=0.7, max_tokens=1200),
                    on_empty=lambda: logger.debug("[ Standalone assistant returned empty response"),
                )
            ),
        )
    )


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

    runtime = await _prepare_standalone_turn_runtime(
        db=db,
        chatroom_id=chatroom_id,
        user_message=user_message,
        checkpoint_snapshot=build_task_run_checkpoint_snapshot(task_run),
        recent_message_limit=20,
    )
    standalone_stream_policy = _build_single_agent_runner_policy(
        run_kind="standalone_assistant_stream",
        agent_name=runtime.assistant_name,
        project_id=None,
        tool_names=[],
        streaming=True,
        standalone=True,
    )

    record_agent_turn_started(
        db,
        task_run,
        agent_name=runtime.assistant_name,
        summary=f"{runtime.assistant_name} started a standalone streaming turn.",
        payload=build_runtime_event_payload(
            client_turn_id=client_turn_id,
            stage_policy=standalone_stream_policy.stages[0] if standalone_stream_policy.stages else None,
        ),
    )

    compaction_callback = _build_context_compaction_callback(
        db,
        task_run,
        agent_name=runtime.assistant_name,
        extra_payload={
            "run_kind": "standalone_assistant_stream",
            "client_turn_id": client_turn_id,
        },
    )
    def _assemble_standalone_stream_messages(current_turn_state: TurnContextState) -> List[Dict[str, Any]]:
        return assemble_runtime_chat_messages(
            db=db,
            agent=None,
            agent_name=runtime.assistant_name,
            model_id=getattr(runtime.llm_client, "model", ""),
            chatroom=chatroom,
            project=None,
            agents=[],
            recent_messages=runtime.recent_messages,
            user_message=user_message,
            history_limit=10,
            standalone_note="This is a standalone chat. Reply directly, be concise, and help the user explore before creating a project if needed.",
            turn_state=current_turn_state,
            on_compaction=compaction_callback,
        )

    async def _execute_standalone_stream_tool(tool_name, tool_args, tool_args_str, tool_call_id, tool_index, turn_index):
        raise RuntimeError(f"Standalone assistant stream cannot execute tool '{tool_name}'")

    def _build_standalone_stream_llm_card(frame, response_content, raw_tool_calls, tool_call_previews, raw_event):
        return _build_llm_card_payload(
            agent_name=runtime.assistant_label,
            llm_client=runtime.llm_client,
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
    standalone_stream_runtime_inputs = build_single_agent_raw_runtime_inputs(
        db=db,
        task_run=task_run,
        chatroom_id=chatroom_id,
        client_turn_id=client_turn_id,
        agent_id=runtime.assistant_id,
        agent_name=runtime.assistant_name,
        agent_type=runtime.assistant_name,
        user_message=user_message,
        save_message=chatroom_manager.send_message,
        publish_message=publish_saved_chat_message,
        record_turn_completed=record_agent_turn_completed,
        message_metadata=_message_metadata_with_turn,
        compact_summary=lambda content: _compact_runtime_text(content, limit=280),
        completion_summary=f"{runtime.assistant_name} completed the standalone streaming turn.",
        failure_summary=lambda error: f"Standalone stream failed: {error}",
        extract_memories=extract_agent_memories,
        stream_failure_message_metadata=_message_metadata_with_turn,
    )

    async for outcome in iter_managed_single_agent_stream_runtime_profile(
        build_single_agent_stream_runtime_profile_from_runtime(
            runtime_inputs=standalone_stream_runtime_inputs,
            execution_inputs=build_single_agent_raw_execution_inputs(
                execution=build_single_agent_stream_raw_execution_inputs(
                    llm_client=runtime.llm_client,
                    tools=None,
                    turn_state=runtime.turn_state,
                    assemble_messages=_assemble_standalone_stream_messages,
                    execute_tool=_execute_standalone_stream_tool,
                    build_llm_runtime_card=_build_standalone_stream_llm_card,
                    snapshot_messages=_snapshot_llm_messages,
                    preview_tool_calls=_preview_tool_calls,
                    format_prompt_messages=_format_json_block,
                    tool_result_success=_tool_result_succeeded,
                    serialize_payload=lambda payload: sse_json.dumps(payload, ensure_ascii=False),
                    store_runtime_card=store_runtime_card,
                    public_runtime_card_payload=public_runtime_card_payload,
                    max_turns=1,
                )
            ),
            detail_builder=traceback.format_exc,
        )
    ):
        if outcome.chunk is not None:
            yield outcome.chunk


async def trigger_agent_response(
    chatroom_id: int,
    user_message: str,
    client_turn_id: Optional[str] = None,
    task_run_id: Optional[int] = None,
    extra_context: str = "",
    checkpoint_snapshot: Optional[Dict[str, Any]] = None,
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
                prepared_orchestration = _prepare_orchestration_runtime(
                    db=db,
                    project=None,
                    agents=_list_global_agents(db),
                    agent_names=mentioned_names,
                    streaming=False,
                )
                _select_task_run_runtime_mode(
                    db,
                    task_run,
                    run_kind="multi_agent_orchestration",
                    summary="Selected standalone multi-agent orchestration mode.",
                    project_id=None,
                    runner_policy=prepared_orchestration.runner_policy,
                    extra_payload={"agents": mentioned_names},
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
                    prepared_runtime=prepared_orchestration,
                )
                return
            standalone_target = _resolve_standalone_target_agent(db, user_message)
            standalone_agent_name = _agent_type(standalone_target) if standalone_target else DEFAULT_AGENT_TYPE
            standalone_policy = _build_single_agent_runner_policy(
                run_kind="standalone_assistant",
                agent_name=standalone_agent_name,
                project_id=None,
                tool_names=[],
                streaming=False,
                standalone=True,
            )
            _select_task_run_runtime_mode(
                db,
                task_run,
                run_kind="standalone_assistant",
                summary="Selected standalone assistant mode.",
                project_id=None,
                target_agent_name=standalone_agent_name,
                runner_policy=standalone_policy,
            )
            await _trigger_standalone_assistant_response(
                db,
                chatroom_id,
                user_message,
                client_turn_id,
                task_run=task_run,
                extra_context=extra_context,
                checkpoint_snapshot=checkpoint_snapshot,
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
            prepared_orchestration = _prepare_orchestration_runtime(
                db=db,
                project=project,
                agents=agents,
                agent_names=mentioned_names,
                streaming=False,
            )
            _select_task_run_runtime_mode(
                db,
                task_run,
                run_kind="multi_agent_orchestration",
                summary="Selected project multi-agent orchestration mode.",
                project_id=project.id,
                runner_policy=prepared_orchestration.runner_policy,
                extra_payload={"agents": mentioned_names},
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
                prepared_runtime=prepared_orchestration,
            )
            return

        target_agent_name = mentioned_names[0] if mentioned_names else None
        logger.debug(f"[ Target agent name: {target_agent_name}")

        # 4. 确定响应的 Agent
        target_agent = _resolve_project_runtime_target_agent(
            db,
            project=project,
            agents=agents,
            target_agent_name=target_agent_name,
        )

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
        _select_task_run_runtime_mode(
            db,
            task_run,
            run_kind="project_single_agent",
            target_agent_name=agent_name_of(target_agent),
            agent_name=agent_name_of(target_agent),
            summary="Selected project single-agent execution mode.",
            project_id=project.id,
            runner_policy=single_agent_policy,
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
        
        visibility = chatroom.message_visibility or "all"
        runtime = await prepare_chat_turn_runtime(
            agent=target_agent,
            chatroom_id=chatroom_id,
            project=project,
            checkpoint_snapshot=checkpoint_snapshot,
            previous_agent_work=extra_context,
            recent_message_limit=20,
        )
        logger.debug(f"[ LLM client obtained for {_agent_type(target_agent)}: {runtime.llm_client.base_url}")
        compaction_callback = _build_context_compaction_callback(
            db,
            task_run,
            agent_name=runtime.agent_label,
            extra_payload={
                "run_kind": "project_single_agent",
                "project_id": project.id,
                "client_turn_id": client_turn_id,
            },
        )
        record_agent_turn_started(
            db,
            task_run,
        agent_name=runtime.agent_label,
        summary=f"{runtime.agent_label} started working on the request.",
        payload=build_runtime_event_payload(
            client_turn_id=client_turn_id,
            stage_policy=single_agent_policy.stages[0] if single_agent_policy.stages else None,
            target_agent_name=runtime.agent_label,
        ),
        )

        def _assemble_project_single_agent_messages(current_turn_state: TurnContextState) -> List[Dict[str, Any]]:
            return assemble_runtime_chat_messages(
                db=db,
                agent=target_agent,
                agent_name=runtime.agent_label,
                model_id=getattr(runtime.llm_client, "model", ""),
                chatroom=chatroom,
                project=project,
                agents=agents,
                recent_messages=runtime.recent_messages,
                user_message=user_message,
                available_tools=runtime.available_tools,
                history_limit=10,
                history_visibility="all" if visibility == "all" else "target",
                target_agent_name=runtime.agent_label,
                prefix_assistant_name=visibility == "all",
                extra_context=extra_context,
                turn_state=current_turn_state,
                on_compaction=compaction_callback,
            )

        logger.debug(
            f"[ Context messages: {len(_assemble_project_single_agent_messages(runtime.turn_state))} messages"
        )
        logger.info(
            f"[LLM] Calling LLM for agent: {_agent_type(target_agent)} with {len(runtime.tool_schemas)} tools available"
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
                    **runtime.runtime_kwargs,
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
                agent_name=runtime.agent_label,
                turn=frame.turn_index + 1,
                tool_names=[tool_call["function"]["name"] for tool_call in frame.normalized_tool_calls],
                tool_results=tool_results,
                summary=f"{runtime.agent_label} completed a tool round.",
            )
        project_single_agent_runtime_inputs = build_single_agent_raw_runtime_inputs(
            db=db,
            task_run=task_run,
            chatroom_id=chatroom_id,
            client_turn_id=client_turn_id,
            agent_id=target_agent.id,
            agent_name=runtime.agent_label,
            agent_type=_agent_type(target_agent),
            user_message=user_message,
            save_message=chatroom_manager.send_message,
            publish_message=publish_saved_chat_message,
            record_turn_completed=record_agent_turn_completed,
            message_metadata=_message_metadata_with_turn,
            compact_summary=lambda content: _compact_runtime_text(content, limit=280),
            completion_summary=f"{agent_name_of(target_agent)} completed the turn.",
            failure_summary=lambda error: f"Agent response failed: {error}",
            extract_memories=extract_agent_memories,
            stream_failure_message_metadata=_message_metadata_with_turn,
        )

        finalized = await run_managed_single_agent_sync_runtime_profile(
            build_single_agent_sync_runtime_profile_from_runtime(
                runtime_inputs=project_single_agent_runtime_inputs,
                execution_inputs=build_single_agent_raw_execution_inputs(
                    execution=build_single_agent_sync_raw_execution_inputs(
                        execute_turn=lambda: execute_non_stream_turn_loop(
                            llm_client=runtime.llm_client,
                            tools=runtime.tool_schemas,
                            turn_state=runtime.turn_state,
                            assemble_messages=_assemble_project_single_agent_messages,
                            execute_tool_call=_execute_project_single_agent_tool,
                            max_turns=MAX_TOOL_ITERATIONS,
                            on_tool_round=_on_project_single_agent_tool_round,
                        ),
                        on_empty=lambda: logger.error(f"[ LLM returned empty response after all tool iterations"),
                    )
                ),
            )
        )

        if finalized.final_content:
            logger.debug(f"[ Agent response saved: id=completed")
            logger.info(f"[Agent] {_agent_type(target_agent)} responded to message successfully")

    except Exception as e:
        logger.error(f"[ Agent response failed: {str(e)}")
        finalize_single_agent_session_failure(
            db,
            task_run,
            error=e,
            failure_summary=f"Agent response failed: {e}",
        )
        import traceback
        traceback.print_exc()
    finally:
        if workspace_token is not None:
            reset_active_workspace(workspace_token)
        db.close()


def _compact_runtime_text(value: Any, *, limit: int = 600) -> str:
    return compact_orchestration_text(value, limit=limit)


def _build_orchestration_previous_work(turns: List[Dict[str, str]]) -> str:
    return build_orchestration_previous_work(turns)


def _build_orchestration_handoff(from_agent_name: str, content: str) -> Dict[str, str]:
    return build_orchestration_handoff(from_agent_name, content)


def _scheduler_event_payload(
    queue: OrchestrationRuntimeQueue,
    step,
    *,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return scheduler_event_payload(queue, step, extra=extra)


def _scheduler_plan_payload(
    queue: OrchestrationRuntimeQueue,
    *,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return scheduler_plan_payload(queue, extra=extra)


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


def _prepare_orchestration_runtime(
    *,
    db: Session,
    project: Optional[Project],
    agents: List[Agent],
    agent_names: List[str],
    streaming: bool,
) -> PreparedOrchestrationRuntime:
    from tools import tool_registry as runtime_tool_registry

    targets = _resolve_orchestration_targets(db, project, agents, agent_names)
    resolved_agents = [agent for _, agent in targets if agent is not None]
    available_tools = runtime_tool_registry.list_tools()
    if not resolved_agents:
        return PreparedOrchestrationRuntime(
            targets=targets,
            resolved_agents=[],
            available_tools=available_tools,
            plan=None,
            runner_policy=None,
        )

    plan = build_orchestration_schedule(
        [(requested_name, agent) for requested_name, agent in targets if agent is not None],
        sidecar_agent_types=_configured_sidecar_agent_types(),
    )
    runner_policy = _build_orchestration_runner_policy(
        plan=plan,
        project_id=project.id if project else None,
        tool_names=available_tools,
        streaming=streaming,
    )
    return PreparedOrchestrationRuntime(
        targets=targets,
        resolved_agents=resolved_agents,
        available_tools=available_tools,
        plan=plan,
        runner_policy=runner_policy,
    )


def _runner_policy_payload(policy: Any) -> Any:
    return policy.to_payload() if policy is not None and hasattr(policy, "to_payload") else None


def _select_task_run_runtime_mode(
    db: Session,
    task_run: Optional[TaskRun],
    *,
    run_kind: str,
    summary: str,
    project_id: Optional[int],
    target_agent_name: Optional[str] = None,
    agent_name: Optional[str] = None,
    runner_policy: Any = None,
    extra_payload: Optional[Dict[str, Any]] = None,
) -> None:
    if task_run is None:
        return

    update_task_run(
        db,
        task_run,
        run_kind=run_kind,
        target_agent_name=target_agent_name if target_agent_name is not None else task_run.target_agent_name,
    )

    payload: Dict[str, Any] = {"project_id": project_id, "runner_policy": _runner_policy_payload(runner_policy)}
    if target_agent_name:
        payload["target_agent_name"] = target_agent_name
    if isinstance(extra_payload, dict):
        payload.update(extra_payload)

    append_task_event(
        db,
        task_run,
        "runtime_mode_selected",
        agent_name=agent_name,
        summary=summary,
        payload=payload,
    )


def _resolve_project_runtime_target_agent(
    db: Session,
    *,
    project: Project,
    agents: List[Agent],
    target_agent_name: Optional[str],
) -> Optional[Agent]:
    target_agent = None
    if target_agent_name:
        target_agent = find_agent_by_type(agents, target_agent_name)
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
    return target_agent


async def _prepare_standalone_turn_runtime(
    *,
    db: Session,
    chatroom_id: int,
    user_message: str,
    checkpoint_snapshot: Optional[Dict[str, Any]] = None,
    previous_agent_work: str = "",
    recent_message_limit: int = 20,
) -> PreparedStandaloneTurnRuntime:
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

    recent_messages = await chatroom_manager.get_messages(chatroom_id, limit=max(1, recent_message_limit))
    turn_state = build_turn_state_from_checkpoint_snapshot(
        checkpoint_snapshot,
        previous_agent_work=previous_agent_work or "",
    )
    return PreparedStandaloneTurnRuntime(
        llm_client=llm_client,
        assistant_name=assistant_name,
        assistant_label=assistant_label,
        assistant_id=assistant_id,
        recent_messages=recent_messages,
        turn_state=turn_state,
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

def _claim_task_run_recovery_lease(
    db: Session,
    task_run_id: int,
) -> tuple[Optional[TaskRun], TaskRunRecoveryResult]:
    claim_result = claim_recovery_lease(
        db,
        task_run_id=task_run_id,
        recoverable_run_kinds=RECOVERABLE_ORCHESTRATION_RUN_KINDS,
        owner=RECOVERY_INSTANCE_ID,
        lease_seconds=RECOVERY_LEASE_SECONDS,
    )
    return claim_result.task_run, TaskRunRecoveryResult(
        task_run_id=task_run_id,
        resumed=False,
        reason=claim_result.reason,
        status=claim_result.status,
        detail=claim_result.detail,
        owner=claim_result.owner,
        lease_expires_at=claim_result.lease_expires_at,
    )


def _renew_task_run_recovery_lease(db: Session, task_run_id: int) -> Optional[datetime]:
    try:
        return ensure_recovery_lease(
            db,
            task_run_id=task_run_id,
            owner=RECOVERY_INSTANCE_ID,
            lease_seconds=RECOVERY_LEASE_SECONDS,
        )
    except RecoveryLeaseLostError:
        return None


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
    durable_handoffs_present = has_orchestration_handoffs_for_task_run(db, task_run_id=task_run.id)

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
        if durable_handoffs_present:
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



def _build_stream_orchestration_agent_turn_iterator():
    deps = StreamOrchestrationAgentTurnDeps(
        ensure_collaboration_context=_ensure_collaboration_context,
        prepare_chat_turn_runtime=prepare_chat_turn_runtime,
        assemble_chat_messages=assemble_runtime_chat_messages,
        build_llm_card_payload=_build_llm_card_payload,
        snapshot_messages=_snapshot_llm_messages,
        preview_tool_calls=_preview_tool_calls,
        format_prompt_messages=_format_json_block,
        tool_result_success=_tool_result_succeeded,
        max_tool_iterations=MAX_TOOL_ITERATIONS,
    )
    return partial(iter_stream_orchestration_agent_turn_events, deps=deps)


def _build_orchestration_agent_turn_executor():
    deps = OrchestrationAgentTurnDeps(
        ensure_collaboration_context=_ensure_collaboration_context,
        prepare_chat_turn_runtime=prepare_chat_turn_runtime,
        build_context_compaction_callback=_build_context_compaction_callback,
        assemble_chat_messages=assemble_runtime_chat_messages,
        save_message=chatroom_manager.send_message,
        message_metadata=_message_metadata_with_turn,
        schedule_memory_extraction=lambda agent, request, response: schedule_agent_memory_extraction(
            extract_agent_memories,
            agent_id=agent.id,
            agent_type=_agent_type(agent),
            user_message=request,
            agent_response=response,
        ),
        max_tool_iterations=MAX_TOOL_ITERATIONS,
    )
    return partial(run_orchestration_agent_turn, deps=deps)


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
    prepared_runtime: Optional[PreparedOrchestrationRuntime] = None,
):
    """
    多 Agent 协作编排

    以 turn/inbox/handoff 驱动执行，而不是固定 stage pipeline。
    """
    prepared = prepared_runtime or _prepare_orchestration_runtime(
        db=db,
        project=project,
        agents=agents,
        agent_names=agent_names,
        streaming=False,
    )
    targets = prepared.targets
    resolved_agents = prepared.resolved_agents

    if not resolved_agents:
        logger.warning("[Collab] No valid agents found for multi-agent orchestration")
        fail_orchestration_preflight(
            db,
            task_run,
            requested_agents=agent_names,
            kind="no_valid_agents",
        )
        return

    logger.info(f"[Collab] Orchestration: {' -> '.join(_agent_type(a) for a in resolved_agents)}")
    output_state = OrchestrationStepOutputState()
    completed_turns = output_state.completed_turns
    results = output_state.results
    pending_handoffs: Dict[str, List[Dict[str, str]]] = {}
    plan = prepared.plan
    orchestration_policy = prepared.runner_policy
    if plan is None or orchestration_policy is None:
        fail_orchestration_preflight(
            db,
            task_run,
            requested_agents=agent_names,
            kind="runtime_unprepared",
        )
        return
    queue = OrchestrationRuntimeQueue(plan)
    execute_orchestration_turn = _build_orchestration_agent_turn_executor()

    record_orchestration_started(
        db,
        task_run,
        requested_agents=agent_names,
        resolved_agents=[agent_name_of(agent) for agent in resolved_agents],
        project_id=project.id if project else None,
        runner_policy=orchestration_policy,
    )

    record_scheduler_plan_created(
        db,
        task_run,
        queue,
        runner_policy=orchestration_policy,
    )

    try:
        await run_nonstream_orchestration_runtime(
            db=db,
            task_run=task_run,
            queue=queue,
            resolved_agents=resolved_agents,
            chatroom_id=chatroom_id,
            project=project,
            agents=agents,
            user_message=user_message,
            client_turn_id=client_turn_id,
            output_state=output_state,
            pending_handoffs=pending_handoffs,
            orchestration_policy=orchestration_policy,
            deps=NonstreamOrchestrationRuntimeDeps(
                execute_turn=execute_orchestration_turn,
                publish_message=publish_saved_chat_message,
                message_metadata=_message_metadata_with_turn(client_turn_id),
                build_step_context=lambda step, agent, agent_label: {"extra_context": extra_context},
                log_agent_type=_agent_type,
            ),
        )
    except TaskRunCancelledError:
        logger.info("[Collab] Orchestration task run %s observed cancellation and stopped.", getattr(task_run, "id", None))
        return
    except Exception as exc:
        logger.exception("[Collab] Orchestration runtime failed: %s", exc)
        fail_orchestration_task_run(
            db,
            task_run,
            summary=f"Orchestration failed: {exc}",
            payload={"error": str(exc)},
        )
        raise

    raise_if_task_run_cancelled(db, task_run, context="orchestration finalize")
    logger.info(f"[Collab] Orchestration complete: {len(results)}/{len(resolved_agents)} agents responded")
    finalize_orchestration_task_run(
        db,
        task_run,
        last_blocking_result=output_state.last_blocking_result,
        results=results,
        fallback="Orchestration completed without agent output.",
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

        prepared_recovery = prepare_orchestration_recovery_context(
            db=db,
            task_run=task_run,
            task_run_id=task_run_id,
            recovery_owner=RECOVERY_INSTANCE_ID,
            lease_expires_at=lease_expires_at,
            resolve_chatroom=lambda current_db, chatroom_id: (
                current_db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
            ),
            resolve_chatroom_project=_resolve_chatroom_project,
            serialize_project_agents=_serialize_project_agents,
            list_global_agents=_list_global_agents,
            recover_agent_names=_recover_orchestration_agent_names,
            prepare_orchestration_runtime=_prepare_orchestration_runtime,
        )
        if not isinstance(prepared_recovery, PreparedOrchestrationRecoveryContext):
            return TaskRunRecoveryResult(
                task_run_id=task_run_id,
                resumed=False,
                reason=prepared_recovery.reason,
                status=prepared_recovery.status,
                detail=prepared_recovery.detail,
                owner=prepared_recovery.owner,
                lease_expires_at=prepared_recovery.lease_expires_at,
            )
        chatroom = prepared_recovery.chatroom
        project = prepared_recovery.project
        agents = prepared_recovery.agents
        agent_names = prepared_recovery.agent_names
        resolved_agents = prepared_recovery.resolved_agents
        plan = prepared_recovery.plan
        orchestration_policy = prepared_recovery.orchestration_policy
        execute_orchestration_turn = _build_orchestration_agent_turn_executor()

        def _before_recovery_step():
            nonlocal lease_expires_at
            lease_expires_at = _renew_task_run_recovery_lease(db, task_run.id)
            if lease_expires_at is not None:
                return None
            logger.warning(
                "[Recovery] Lost lease for task run %s while %s was attempting resume",
                task_run.id,
                RECOVERY_INSTANCE_ID,
            )
            raise RecoveryLeaseLostError(task_run.id)
        recovery_result = await run_orchestration_recovery_runtime(
            db=db,
            task_run=task_run,
            task_run_id=task_run_id,
            chatroom=chatroom,
            project=project,
            agents=agents,
            agent_names=agent_names,
            resolved_agents=resolved_agents,
            plan=plan,
            orchestration_policy=orchestration_policy,
            trigger=trigger,
            lease_expires_at=lease_expires_at,
            deps=OrchestrationRecoveryRuntimeDeps(
                build_checkpoint_snapshot=build_task_run_checkpoint_snapshot,
                describe_recovery_continuation_state=_describe_recovery_continuation_state,
                rebuild_recovery_state=_rebuild_orchestration_recovery_state,
                execute_turn=execute_orchestration_turn,
                publish_message=publish_saved_chat_message,
                message_metadata=_message_metadata_with_turn(task_run.client_turn_id),
                renew_lease=_before_recovery_step,
                recovery_owner=RECOVERY_INSTANCE_ID,
            ),
        )
        return TaskRunRecoveryResult(
            task_run_id=task_run_id,
            resumed=recovery_result.resumed,
            reason=recovery_result.reason,
            status=recovery_result.status,
            detail=recovery_result.detail,
            owner=recovery_result.owner,
            lease_expires_at=recovery_result.lease_expires_at,
        )
    except RecoveryLeaseLostError:
        refreshed = db.query(TaskRun).filter(TaskRun.id == task_run_id).first()
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
    except TaskRunCancelledError:
        refreshed = db.query(TaskRun).filter(TaskRun.id == task_run_id).first()
        return TaskRunRecoveryResult(
            task_run_id=task_run_id,
            resumed=False,
            reason="cancelled",
            status=(refreshed.status if refreshed is not None else "cancelled"),
            detail="Recovery stopped because the task run was cancelled.",
            owner=RECOVERY_INSTANCE_ID,
        )
    except Exception as exc:
        logger.exception(f"[Recovery] Failed to recover task run {task_run_id}: {exc}")
        task_run = db.query(TaskRun).filter(TaskRun.id == task_run_id).first()
        if task_run is not None and (task_run.status or "").lower() == "running":
            fail_orchestration_task_run(
                db,
                task_run,
                event_type="task_run_recovery_failed",
                summary=f"Recovery failed: {exc}",
                event_summary=f"Interrupted orchestration recovery failed: {exc}",
                payload={"task_run_id": task_run_id},
            )
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
    prepared_runtime: Optional[PreparedOrchestrationRuntime] = None,
):
    prepared = prepared_runtime or _prepare_orchestration_runtime(
        db=db,
        project=project,
        agents=agents,
        agent_names=agent_names,
        streaming=True,
    )
    standalone_note = (
        "This is a standalone chat. Reply directly, stay concise, "
        "and coordinate with the other mentioned agents."
        if project is None
        else ""
    )

    stream_runtime_deps = StreamOrchestrationRuntimeDeps(
        iter_agent_events=_build_stream_orchestration_agent_turn_iterator(),
        save_message=chatroom_manager.send_message,
        publish_message=publish_saved_chat_message,
        record_turn_completed=record_agent_turn_completed,
        message_metadata=_message_metadata_with_turn,
        schedule_memory_extraction=lambda current_agent, request, response: schedule_agent_memory_extraction(
            extract_agent_memories,
            agent_id=current_agent.id,
            agent_type=_agent_type(current_agent),
            user_message=request,
            agent_response=response,
        ),
        build_checkpoint_snapshot=build_task_run_checkpoint_snapshot,
        find_stage_policy=find_stage_policy,
        agent_name_of=agent_name_of,
        fail_task_run=fail_orchestration_task_run,
        finalize_task_run=finalize_orchestration_task_run,
        set_active_agent=set_active_agent,
    )

    async for runtime_event in iter_stream_orchestration_session_events(
        db=db,
        task_run=task_run,
        prepared_runtime=prepared,
        chatroom=chatroom,
        project=project,
        agents=agents,
        agent_names=agent_names,
        user_message=user_message,
        client_turn_id=client_turn_id,
        standalone_note=standalone_note,
        deps=stream_runtime_deps,
    ):
        yield await render_stream_runtime_event(
            runtime_event,
            serialize_payload=lambda payload: sse_json.dumps(payload, ensure_ascii=False),
            render_runtime_card=sse_card,
        )



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


class TaskRunCancelRequest(BaseModel):
    note: Optional[str] = None
    cancelled_by: Optional[str] = "user"


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
    db.query(OrchestrationHandoffDelivery).filter(
        OrchestrationHandoffDelivery.task_run_id.in_(unique_task_run_ids)
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
            card_payload = public_runtime_card_payload(dict(card))
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


@router.post("/task-runs/{task_run_id}/cancel")
async def cancel_task_run(
    task_run_id: int,
    req: TaskRunCancelRequest | None = None,
    db: Session = Depends(get_db),
):
    """Cancel a running task run and terminalize any non-terminal subagent states."""
    task_run = (
        db.query(TaskRun)
        .filter(TaskRun.id == task_run_id)
        .first()
    )
    if not task_run:
        raise HTTPException(status_code=404, detail="Task run not found")

    if (task_run.status or "").lower() != "running":
        raise HTTPException(status_code=409, detail="Only running task runs can be cancelled.")

    checkpoint_snapshot = build_task_run_checkpoint_snapshot(task_run)
    lifecycle = checkpoint_snapshot.get("subagent_lifecycle")
    cancellable_subagents = cancellable_subagents_from_lifecycle(lifecycle)
    cancelled_by = ((req.cancelled_by if req else None) or "user").strip() or "user"
    note = ((req.note if req else None) or "").strip()

    for subagent in cancellable_subagents:
        record_scheduler_step_cancelled(
            db,
            task_run,
            subagent,
            cancelled_by=cancelled_by,
            note=note,
        )

    append_task_event(
        db,
        task_run,
        "task_run_cancelled",
        summary=note or "Task run cancelled from the API.",
        payload={
            "task_run_id": task_run.id,
            "run_kind": task_run.run_kind,
            "cancelled_by": cancelled_by,
            "cancelled_subagent_count": len(cancellable_subagents),
            "cancelled_step_ids": [
                subagent.get("step_id")
                for subagent in cancellable_subagents
                if subagent.get("step_id") is not None
            ],
            "note": note or None,
            "checkpoint_snapshot": checkpoint_snapshot,
        },
    )
    complete_task_run(db, task_run, status="cancelled", summary=note or "Task run cancelled.")
    db.refresh(task_run)
    return {
        "message": "Task run cancelled.",
        "cancelled": True,
        "task_run_id": task_run.id,
        "status": task_run.status,
        "cancelled_subagent_count": len(cancellable_subagents),
        "detail": serialize_task_run_detail(task_run),
    }


async def _replay_runtime_blocked_tool_queue_item(
    db: Session,
    item: Any,
    request_payload: Dict[str, Any],
):
    from tools import tool_registry

    tool_name = resolve_replay_tool_name(item, request_payload)
    arguments_text = resolve_replay_arguments_text(request_payload)
    if not tool_name:
        return build_replay_tool_result_record(
            item,
            tool_name=getattr(item, "target_name", "tool"),
            arguments=arguments_text,
            result="Error executing blocked tool replay: missing tool_name.",
            success=False,
        )

    loaded_arguments, arguments_error = parse_replay_arguments(arguments_text)
    if arguments_error is not None:
        return build_replay_tool_result_record(
            item,
            tool_name=tool_name,
            arguments=arguments_text,
            result=f"Error executing blocked tool replay: invalid arguments ({arguments_error}).",
            success=False,
        )

    chatroom = db.query(Chatroom).filter(Chatroom.id == getattr(item, "chatroom_id", None)).first()
    if chatroom is None:
        return build_replay_tool_result_record(
            item,
            tool_name=tool_name,
            arguments=arguments_text,
            result="Error executing blocked tool replay: chatroom no longer exists.",
            success=False,
        )

    project = _resolve_chatroom_project(db, chatroom)
    agents = _serialize_project_agents(db, project.id) if project else _list_global_agents(db)
    agent = find_agent_by_type(agents, getattr(item, "agent_name", None))
    runtime_kwargs = build_tool_runtime_kwargs(agent, chatroom.id, project)

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

    return build_replay_tool_result_record(
        item,
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
    if approval_queue_item_has_pipeline_cursor(item, request_payload):
        from pipeline.engine import replay_blocked_tool_queue_item as replay_pipeline_blocked_tool_queue_item

        return await replay_pipeline_blocked_tool_queue_item(db, item)
    return await _replay_runtime_blocked_tool_queue_item(db, item, request_payload)


def _describe_recovery_continuation_state(checkpoint_snapshot: Any) -> Dict[str, Any]:
    return describe_checkpoint_continuation_state(checkpoint_snapshot)


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
    await publish_saved_chat_message(
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
        return build_followup_skipped_payload("task_run_missing")
    if getattr(item, "chatroom_id", None) is None:
        return build_followup_skipped_payload("chatroom_missing")
    if approval_queue_item_has_pipeline_cursor(item, request_payload):
        return build_followup_skipped_payload("pipeline_queue_item")
    if not replay_result_is_actionable(replay_result):
        return build_followup_skipped_payload("replay_not_actionable")

    followup_context = build_tool_replay_followup_context(item, replay_result)
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
        payload=build_followup_triggered_event_payload(
            item,
            replay_result,
            message_id=getattr(saved, "id", None),
        ),
    )
    followup_snapshot = build_task_run_checkpoint_snapshot(task_run)
    try:
        await trigger_agent_response(
            getattr(item, "chatroom_id", None),
            task_run.user_request or "",
            getattr(task_run, "client_turn_id", None),
            task_run_id=task_run.id,
            extra_context=followup_context,
            checkpoint_snapshot=followup_snapshot,
        )
    except Exception as exc:
        append_task_event(
            db,
            task_run,
            "approval_queue_item_followup_failed",
            agent_name=item.agent_name,
            summary=f"Approved replay follow-up failed for {getattr(replay_result, 'tool_name', item.target_name or 'tool')}.",
            payload=build_followup_failed_event_payload(item, replay_result, exc),
        )
        return build_followup_failed_payload(exc, followup_message_id=getattr(saved, "id", None))

    return build_followup_continued_payload(followup_message_id=getattr(saved, "id", None))


async def _continue_pipeline_after_approved_tool_replay(
    db: Session,
    item: Any,
    request_payload: Dict[str, Any],
    replay_result: Any,
):
    pipeline_run_id = getattr(item, "pipeline_run_id", None) or request_payload.get("pipeline_run_id")
    if not pipeline_run_id:
        return build_followup_skipped_payload("pipeline_run_missing")
    if not replay_result_is_actionable(replay_result):
        return build_followup_skipped_payload("replay_not_actionable")

    from models.database import Pipeline, PipelineRun

    run = db.query(PipelineRun).filter(PipelineRun.id == int(pipeline_run_id)).first()
    if run is None:
        return build_followup_skipped_payload("pipeline_run_missing")

    pipeline = db.query(Pipeline).filter(Pipeline.id == run.pipeline_id).first()
    if pipeline is None:
        return build_followup_skipped_payload("pipeline_missing")

    task_run = get_task_run(db, getattr(item, "task_run_id", None))
    append_task_event(
        db,
        task_run,
        "approval_queue_item_followup_triggered",
        agent_name=item.agent_name,
        summary=f"Resuming pipeline after approved replay of {getattr(replay_result, 'tool_name', item.target_name or 'tool')}.",
        payload=build_followup_triggered_event_payload(
            item,
            replay_result,
            pipeline=pipeline,
            pipeline_run=run,
            request_payload=request_payload,
        ),
    )

    try:
        await pipeline_engine.instruct(
            db,
            pipeline.id,
            str(item.agent_name or request_payload.get("agent_name") or "").strip() or "agent",
            build_tool_replay_followup_context(item, replay_result),
        )
        if (pipeline.status or "").lower() == "paused":
            await pipeline_engine.resume(db, pipeline.id)
    except Exception as exc:
        append_task_event(
            db,
            task_run,
            "approval_queue_item_followup_failed",
            agent_name=item.agent_name,
            summary=f"Approved replay follow-up failed for pipeline tool {getattr(replay_result, 'tool_name', item.target_name or 'tool')}.",
            payload=build_followup_failed_event_payload(
                item,
                replay_result,
                exc,
                pipeline=pipeline,
                pipeline_run=run,
            ),
        )
        return build_followup_failed_payload(exc)

    return build_followup_continued_payload(followup_reason="pipeline_resumed")


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

    request_payload = load_approval_queue_request_payload(item.request_payload_json)
    resolution_note = ((req.note if req else None) or "").strip()
    resolved_by = ((req.resolved_by if req else None) or "user").strip() or "user"
    if not claim_approval_queue_resolution_lease(
        db,
        item,
        owner=f"approval-api:{resolved_by}:{item_id}",
    ):
        raise HTTPException(status_code=409, detail="Approval queue item is leased by another resolver.")

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

    resolution_payload = build_queue_replay_resolution_payload(
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
            payload=build_approval_queue_replay_round_payload(item, request_payload),
        )
        resolution_payload = build_queue_replay_resolution_payload(
            request_payload=request_payload,
            replay_result=replay_result,
            action_taken="tool_replayed",
        )
        if approval_queue_item_has_pipeline_cursor(item, request_payload):
            resolution_payload.update(
                await _continue_pipeline_after_approved_tool_replay(
                    db,
                    item,
                    request_payload,
                    replay_result,
                )
            )
        else:
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
        payload=build_approval_queue_item_resolved_event_payload(
            resolved or item,
            status="approved",
            resolved_by=resolved_by,
            request_payload=request_payload,
            resolution_payload=resolution_payload,
        ),
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

    request_payload = load_approval_queue_request_payload(item.request_payload_json)
    resolution_note = ((req.note if req else None) or "").strip()
    resolved_by = ((req.resolved_by if req else None) or "user").strip() or "user"
    if not claim_approval_queue_resolution_lease(
        db,
        item,
        owner=f"approval-api:{resolved_by}:{item_id}",
    ):
        raise HTTPException(status_code=409, detail="Approval queue item is leased by another resolver.")

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
        resolution_payload=build_queue_rejection_resolution_payload(
            request_payload=request_payload,
            rollback_to=req.rollback_to if req else None,
        ),
    )
    task_run = get_task_run(db, item.task_run_id)
    append_task_event(
        db,
        task_run,
        "approval_queue_item_resolved",
        agent_name=item.agent_name,
        summary=f"Rejected queue item for {item.target_name or item.target_kind}.",
        payload=build_approval_queue_item_resolved_event_payload(
            resolved or item,
            status="rejected",
            resolved_by=resolved_by,
            request_payload=request_payload,
        ),
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
    await publish_saved_chat_message(
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
            return await render_chatroom_runtime_card_sse(
                event_type=event_type,
                payload=data,
                chatroom_id=chatroom_id,
                client_turn_id=message.client_turn_id,
                serialize_payload=lambda payload: _json.dumps(payload, ensure_ascii=False),
                store_runtime_card=store_runtime_card,
                public_runtime_card_payload=public_runtime_card_payload,
            )

        db = next(_get_db())
        workspace_token = None
        active_agent_name: Optional[str] = None
        active_agent_id: Optional[int] = None
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
            await publish_saved_chat_message(
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
                    prepared_orchestration = _prepare_orchestration_runtime(
                        db=db,
                        project=None,
                        agents=agents,
                        agent_names=mentioned_names,
                        streaming=True,
                    )
                    _select_task_run_runtime_mode(
                        db,
                        task_run,
                        run_kind="multi_agent_orchestration_stream",
                        summary="Selected standalone multi-agent streaming orchestration mode.",
                        project_id=None,
                        runner_policy=prepared_orchestration.runner_policy,
                        extra_payload={"agents": mentioned_names},
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
                        prepared_runtime=prepared_orchestration,
                    ):
                        yield chunk
                    return

                standalone_target = _resolve_standalone_target_agent(db, message.content)
                standalone_agent_name = _agent_type(standalone_target) if standalone_target else DEFAULT_AGENT_TYPE
                standalone_stream_policy = _build_single_agent_runner_policy(
                    run_kind="standalone_assistant_stream",
                    agent_name=standalone_agent_name,
                    project_id=None,
                    tool_names=[],
                    streaming=True,
                    standalone=True,
                )
                _select_task_run_runtime_mode(
                    db,
                    task_run,
                    run_kind="standalone_assistant_stream",
                    summary="Selected standalone assistant streaming mode.",
                    project_id=None,
                    target_agent_name=standalone_agent_name,
                    runner_policy=standalone_stream_policy,
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
                prepared_orchestration = _prepare_orchestration_runtime(
                    db=db,
                    project=project,
                    agents=agents,
                    agent_names=mentioned_names,
                    streaming=True,
                )
                _select_task_run_runtime_mode(
                    db,
                    task_run,
                    run_kind="multi_agent_orchestration_stream",
                    summary="Selected project multi-agent streaming orchestration mode.",
                    project_id=project.id,
                    runner_policy=prepared_orchestration.runner_policy,
                    extra_payload={"agents": mentioned_names},
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
                    prepared_runtime=prepared_orchestration,
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

            target_agent = _resolve_project_runtime_target_agent(
                db,
                project=project,
                agents=agents,
                target_agent_name=target_agent_name,
            )
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
            _select_task_run_runtime_mode(
                db,
                task_run,
                run_kind="project_single_agent_stream",
                target_agent_name=target_agent_label,
                agent_name=target_agent_label,
                summary="Selected project single-agent streaming execution mode.",
                project_id=project.id,
                runner_policy=project_single_agent_stream_policy,
            )

            _ensure_collaboration_context(agents, chatroom_id)

            # 5. 构建该 Agent 的消息上下文
            checkpoint_snapshot = build_task_run_checkpoint_snapshot(task_run)
            runtime = await prepare_chat_turn_runtime(
                agent=target_agent,
                chatroom_id=chatroom_id,
                project=project,
                checkpoint_snapshot=checkpoint_snapshot,
                recent_message_limit=10,
            )
            compaction_callback = _build_context_compaction_callback(
                db,
                task_run,
                agent_name=target_agent_label,
                extra_payload={
                    "run_kind": "project_single_agent_stream",
                    "project_id": project.id,
                    "client_turn_id": message.client_turn_id,
                },
            )
            record_agent_turn_started(
                db,
                task_run,
                agent_name=target_agent_label,
                summary=f"{target_agent_label} started a streaming turn.",
                payload=build_runtime_event_payload(
                    client_turn_id=message.client_turn_id,
                    stage_policy=(
                        project_single_agent_stream_policy.stages[0]
                        if project_single_agent_stream_policy.stages
                        else None
                    ),
                    target_agent_name=target_agent_label,
                ),
            )

            final_content = ""
            def _assemble_single_agent_stream_messages(current_turn_state: TurnContextState) -> List[Dict[str, Any]]:
                return assemble_runtime_chat_messages(
                    db=db,
                    agent=target_agent,
                    agent_name=target_agent_label,
                    model_id=getattr(runtime.llm_client, "model", ""),
                    chatroom=chatroom,
                    project=project,
                    agents=agents,
                    recent_messages=runtime.recent_messages,
                    user_message=message.content,
                    available_tools=runtime.available_tools,
                    history_limit=6,
                    turn_state=current_turn_state,
                    on_compaction=compaction_callback,
                )

            async def _execute_single_agent_stream_tool(tool_name, tool_args, tool_args_str, tool_call_id, tool_index, turn_index):
                from tools import tool_registry

                return await tool_registry.execute(
                    tool_name,
                    **tool_args,
                    **runtime.runtime_kwargs,
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
                    llm_client=runtime.llm_client,
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
            project_single_agent_stream_runtime_inputs = build_single_agent_raw_runtime_inputs(
                db=db,
                task_run=task_run,
                chatroom_id=chatroom_id,
                client_turn_id=message.client_turn_id,
                agent_id=target_agent.id,
                agent_name=target_agent_label,
                agent_type=_agent_type(target_agent),
                user_message=message.content,
                save_message=chatroom_manager.send_message,
                publish_message=publish_saved_chat_message,
                record_turn_completed=record_agent_turn_completed,
                message_metadata=_message_metadata_with_turn,
                compact_summary=lambda content: _compact_runtime_text(content, limit=280),
                completion_summary=f"{target_agent_label} completed the streaming turn.",
                failure_summary=lambda error: f"Streaming execution failed: {error}",
                extract_memories=extract_agent_memories,
                stream_failure_message_metadata=_message_metadata_with_turn,
            )

            async for outcome in iter_managed_single_agent_stream_runtime_profile(
                build_single_agent_stream_runtime_profile_from_runtime(
                    runtime_inputs=project_single_agent_stream_runtime_inputs,
                    execution_inputs=build_single_agent_raw_execution_inputs(
                        execution=build_single_agent_stream_raw_execution_inputs(
                            llm_client=runtime.llm_client,
                            tools=runtime.tool_schemas,
                            turn_state=runtime.turn_state,
                            assemble_messages=_assemble_single_agent_stream_messages,
                            execute_tool=_execute_single_agent_stream_tool,
                            build_llm_runtime_card=_build_single_agent_stream_llm_card,
                            snapshot_messages=_snapshot_llm_messages,
                            preview_tool_calls=_preview_tool_calls,
                            format_prompt_messages=_format_json_block,
                            tool_result_success=_tool_result_succeeded,
                            serialize_payload=lambda payload: _json.dumps(payload, ensure_ascii=False),
                            store_runtime_card=store_runtime_card,
                            public_runtime_card_payload=public_runtime_card_payload,
                            max_turns=MAX_TOOL_ITERATIONS,
                            on_tool_round=_on_single_agent_stream_tool_round,
                        )
                    ),
                    failure_agent_name=active_agent_name or default_agent_name(DEFAULT_AGENT_TYPE),
                    failure_agent_id=active_agent_id,
                    detail_builder=traceback.format_exc,
                )
            ):
                if outcome.chunk is not None:
                    yield outcome.chunk

        except Exception as persist_exc:
            logger.error(f"[SSE] Failed to drive streaming session: {persist_exc}")
            traceback.print_exc()
            yield render_sse_payload({"type": "error", "error": str(persist_exc)}, serialize_payload=lambda payload: _json.dumps(payload))
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
