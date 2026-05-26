# -*- coding: utf-8 -*-
"""
API 路由 - 主要端点
"""
import logging
import re
import json
import os
import asyncio
import hashlib
import threading
import socket
import shutil
import subprocess
import time
import traceback
import uuid
from types import SimpleNamespace
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, UploadFile, File as FastAPIFile
from fastapi.responses import StreamingResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session, object_session
from typing import Callable, List, Optional, Dict, Any, Awaitable
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
    Asset,
    Chatroom,
    AgentAssignment,
    Message,
    TaskRun,
    TaskRunEvent,
    OrchestrationHandoffDelivery,
    ApprovalQueueItem,
    ToolExecutionPreference,
    SessionLocal,
    Base,
)
from models.enums import EventType, RunKind
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
from services.assistant_handoff import maybe_schedule_assistant_handoff
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
    default_selector_profiles,
    effective_selector_profiles,
    resolve_llm_context_window,
)
from services.model_context import context_window_from_provider
from services.agent_lifecycle_runtime import (
    cancel_runtime_task_run,
    cancel_runtime_task_run_subagent,
    close_runtime_task_run_subagent,
    delegate_runtime_collaboration_task,
    ensure_runtime_chatroom_collaborators,
    get_runtime_chatroom_collaboration_status,
    get_runtime_collaboration_status,
    get_runtime_collaboration_task,
    list_runtime_collaboration_tasks,
    list_runtime_task_run_subagents,
    observe_runtime_task_run_subagent,
)
from services.chat_runtime import (
    PreparedChatTurnRuntime,
    assemble_runtime_chat_messages,
    build_runtime_environment_context,
    build_tool_runtime_kwargs,
    canonical_tool_names,
    prepare_chat_turn_runtime,
    resolve_agent_tool_names,
)
from services.run_shell_processes import (
    build_tracked_run_shell_result,
    load_tracked_run_shell_handle,
    list_tracked_run_shell_processes,
    read_tracked_run_shell_tail,
    run_shell_process_state_dir,
    terminate_tracked_run_shell,
    tracked_run_shell_has_exit,
    tracked_run_shell_is_active,
    wait_for_tracked_run_shell,
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
from services.task_run_lifecycle import terminalize_task_run
from services.task_activity_projection import build_task_activity_projection
from services.chat_timeline_projection import (
    build_chatroom_timeline_projection,
    build_task_run_timeline_projection,
)
from services.runner_lifecycle import (
    build_llm_request_prompt_payload,
    complete_agent_turn as record_agent_turn_completed,
    record_llm_request_created,
    record_llm_response_completed,
    record_llm_response_started,
    record_tool_round as record_runner_tool_round,
    start_tool_call as record_tool_call_started,
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
    build_followup_interrupted_payload,
    build_followup_skipped_payload,
    build_followup_triggered_event_payload,
    build_queue_replay_resolution_payload,
    build_queue_rejection_resolution_payload,
    build_tool_replay_followup_context,
    load_approval_queue_request_payload,
    parse_replay_arguments,
    build_replay_tool_result_record,
    replay_result_non_actionable_reason,
    resolve_replay_arguments_text,
    resolve_replay_tool_name,
)
from services.test_runner_contract import (
    extract_pytest_failure_summary,
    is_valid_test_runner_result,
    normalize_test_runner_result,
)
from services.artifact_naming import build_timestamped_artifact_path, slug_artifact_subject
from services.task_status_transition import validate_transition
from services.artifact_history import (
    archive_workspace_artifact_snapshot,
    classify_workspace_artifact_path,
)
from services.tool_governance import tool_result_succeeded as shared_tool_result_succeeded
from services.task_run_control import TaskRunCancelledError, raise_if_task_run_cancelled
from services.runner_policy import (
    compile_orchestration_run_policy,
    compile_single_agent_run_policy,
    find_stage_policy,
)
from services.runtime_event_helpers import build_context_compaction_callback, build_runtime_event_payload
from services.runtime_lifecycle import runtime_is_shutting_down
from services.memory_extraction import (
    extract_agent_memories,
    schedule_agent_memory_extraction,
)
from services.stream_turn_executor import iter_stream_turn_events
from services.stream_runtime_persistence import (
    public_runtime_card_payload,
    store_runtime_card,
)
from services.audit_recorder import (
    chain_before_event_callbacks,
    make_nonstream_audit_callbacks,
    make_stream_audit_before_event,
)
from services.single_agent_session_finalizer import (
    finalize_single_agent_session_failure,
)
from services.single_agent_session_orchestrator import (
    build_single_agent_raw_runtime_inputs,
    build_single_agent_stream_loop_callbacks,
    build_single_agent_stream_transport_context,
    build_single_agent_stream_failure_policy,
    iter_managed_single_agent_stream_runtime_profile,
    run_managed_single_agent_sync_runtime_profile,
)
from services.single_agent_chat_profile import (
    build_single_agent_stream_chat_profile,
    build_single_agent_sync_chat_profile,
)
from services.stream_transport import (
    iter_rendered_stream_turn_events,
    render_sse_payload,
    render_chatroom_runtime_card_sse,
    render_stream_turn_event,
)
from services.approval_audit import record_approval_audit
from services.tool_execution_preferences import (
    AUTH_DECISION_ALLOW,
    AUTH_DECISION_DENY,
    AUTH_DECISION_ALLOW_NO_TIMEOUT,
    AUTH_MATCHER_ALL_TOOLS,
    AUTH_MATCHER_COMMAND_FINGERPRINT,
    AUTH_MATCHER_SHELL_BIN,
    AUTH_MATCHER_TOOL_TARGET,
    AUTH_PREFERENCE_KIND,
    AUTH_SCOPE_CHATROOM,
    AUTH_SCOPE_GLOBAL,
    AUTH_SCOPE_PROJECT,
    authorization_matchers_for_tool,
    build_tool_target_matcher_value,
    list_authorization_rules,
    normalize_authorization_scope,
    revoke_authorization_rule,
    serialize_authorization_rule,
    upsert_authorization_rule,
)
from services.nonstream_turn_executor import execute_non_stream_turn_loop
from services.subagent_lifecycle import wait_timeout_seconds
from services.subagent_runtime_control import (
    SubagentRuntimeControlError,
    build_task_run_subagent_projection,
    cancel_task_run_subagent_handle,
    cancel_task_run_with_subagents,
    close_task_run_subagent_handle,
    observe_task_run_subagent,
)
from services.collaboration_task_runtime import CollaborationTaskRuntimeError
from services.orchestration_events import (
    record_orchestration_started,
    record_scheduler_plan_created,
    record_scheduler_recovery_state_rebuilt,
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
from services.orchestration_session_profile import (
    build_orchestration_session_runtime_profile,
    build_orchestration_turn_runtime_profile,
)
from services.orchestration_step_state import OrchestrationStepOutputState, record_orchestration_step_output
from services.orchestration_step_completion import complete_orchestration_scheduler_step
from services.orchestration_recovery_prepare import (
    PreparedOrchestrationRecoveryContext,
    prepare_orchestration_recovery_context,
)
from services.orchestration_stream_runner import render_stream_runtime_event

logger = logging.getLogger("catown.api")

MAX_TOOL_ITERATIONS = 50
RECOVERABLE_ORCHESTRATION_RUN_KINDS = {
    RunKind.MULTI_AGENT_ORCHESTRATION,
    RunKind.MULTI_AGENT_ORCHESTRATION_STREAM,
}
INTERRUPTIBLE_SINGLE_AGENT_RUN_KINDS = {
    RunKind.PROJECT_SINGLE_AGENT,
    RunKind.PROJECT_SINGLE_AGENT_STREAM,
    RunKind.STANDALONE_ASSISTANT,
    RunKind.STANDALONE_ASSISTANT_STREAM,
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
    available_tools: Dict[str, List[str]]
    plan: Any | None
    runner_policy: Any | None


@dataclass
class PreparedStandaloneTurnRuntime:
    llm_client: Any
    assistant_name: str
    assistant_label: str
    assistant_id: Optional[int]
    recent_messages: List[Any]
    tool_policy_pack: Dict[str, Any]
    turn_state: TurnContextState


def _resolve_agent_runtime_tools(agent: Any) -> List[str]:
    from tools import tool_registry as runtime_tool_registry

    return resolve_agent_tool_names(agent, runtime_tool_registry.list_agent_tools())

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


def _normalize_authorization_matcher_config(value: Any) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if not normalized:
        return None
    aliases = {
        "command": AUTH_MATCHER_COMMAND_FINGERPRINT,
        "exact_command": AUTH_MATCHER_COMMAND_FINGERPRINT,
        "command_fingerprint": AUTH_MATCHER_COMMAND_FINGERPRINT,
        "bin": AUTH_MATCHER_SHELL_BIN,
        "shell_bin": AUTH_MATCHER_SHELL_BIN,
        "tool": AUTH_MATCHER_TOOL_TARGET,
        "tool_target": AUTH_MATCHER_TOOL_TARGET,
        "full": AUTH_MATCHER_ALL_TOOLS,
        "all": AUTH_MATCHER_ALL_TOOLS,
        "all_tools": AUTH_MATCHER_ALL_TOOLS,
    }
    return aliases.get(normalized)


class PermissionsConfigModel(BaseModel):
    """Runtime permission policy config validation model."""

    allow_read_only_tools_without_approval: bool = True
    auto_approve_all: bool = False
    remember_default_scope: str = AUTH_SCOPE_PROJECT
    remember_default_matcher: str = AUTH_MATCHER_COMMAND_FINGERPRINT

    @field_validator("remember_default_scope")
    @classmethod
    def _validate_remember_default_scope(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if not normalized:
            return AUTH_SCOPE_PROJECT
        if normalized not in {AUTH_SCOPE_PROJECT, AUTH_SCOPE_CHATROOM, AUTH_SCOPE_GLOBAL}:
            raise ValueError("remember_default_scope must be one of: project, chatroom, global")
        return normalized

    @field_validator("remember_default_matcher")
    @classmethod
    def _validate_remember_default_matcher(cls, value: str) -> str:
        normalized = _normalize_authorization_matcher_config(value)
        if normalized is None:
            raise ValueError("remember_default_matcher must be one of: command_fingerprint, shell_bin, tool_target, all_tools")
        return normalized


class ContextSelectorProfileModel(BaseModel):
    """Runtime context-selector budget config for one profile."""

    allowed_visibilities: Optional[List[str]] = None
    allowed_scopes: Optional[List[str]] = None
    max_fragments: Optional[int] = Field(default=None, ge=1, le=500)
    max_tokens_cap: Optional[int] = Field(default=None, ge=1, le=1000000)
    max_tokens_cap_ratio: Optional[float] = Field(default=None, gt=0, le=1)
    max_tokens_by_role: Dict[str, int] = Field(default_factory=dict)
    max_tokens_by_role_ratio: Dict[str, float] = Field(default_factory=dict)
    max_tokens_by_scope: Dict[str, int] = Field(default_factory=dict)
    max_tokens_by_scope_ratio: Dict[str, float] = Field(default_factory=dict)
    truncate_to_budget: bool = True
    min_tokens_for_truncation: int = Field(default=48, ge=1, le=10000)

    @field_validator("max_tokens_by_role", "max_tokens_by_scope")
    @classmethod
    def validate_positive_budget_map(cls, value: Dict[str, int]) -> Dict[str, int]:
        normalized: Dict[str, int] = {}
        for key, raw_limit in (value or {}).items():
            label = str(key or "").strip()
            if not label:
                continue
            limit = int(raw_limit)
            if limit <= 0:
                raise ValueError("context budget values must be positive")
            normalized[label] = limit
        return normalized

    @field_validator("max_tokens_by_role_ratio", "max_tokens_by_scope_ratio")
    @classmethod
    def validate_positive_ratio_map(cls, value: Dict[str, float]) -> Dict[str, float]:
        normalized: Dict[str, float] = {}
        for key, raw_ratio in (value or {}).items():
            label = str(key or "").strip()
            if not label:
                continue
            ratio = float(raw_ratio)
            if ratio <= 0 or ratio > 1:
                raise ValueError("context budget ratios must be in the range (0, 1]")
            normalized[label] = ratio
        return normalized


class ContextConfigModel(BaseModel):
    """Runtime context management config stored in agents.json."""

    selector_profiles: Dict[str, ContextSelectorProfileModel] = Field(default_factory=dict)


class UiChatCardsConfigModel(BaseModel):
    """Runtime chat card display preferences."""

    expand_current_step_by_default: bool = False


class UiConfigModel(BaseModel):
    """Runtime UI preferences stored in agents.json."""

    chat_cards: UiChatCardsConfigModel = Field(default_factory=UiChatCardsConfigModel)


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


def _tool_result_succeeded(result: Any) -> bool:
    return shared_tool_result_succeeded(result)


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


def _build_message_runtime_summary(
    db: Session,
    *,
    chatroom_id: int,
    client_turn_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    normalized_turn_id = str(client_turn_id or "").strip()
    if not normalized_turn_id:
        return None

    task_run = (
        db.query(TaskRun)
        .filter(TaskRun.chatroom_id == chatroom_id, TaskRun.client_turn_id == normalized_turn_id)
        .order_by(TaskRun.created_at.desc(), TaskRun.id.desc())
        .first()
    )
    if task_run is None:
        return None

    summary = serialize_task_run_summary(task_run)
    activity = build_task_activity_projection(task_run)
    runtime_summary = {
        "task_run_id": task_run.id,
        "status": task_run.status,
        "run_kind": task_run.run_kind,
        "active_subagent_handle": activity.get("active_subagent_handle"),
        "active_consult_handle": activity.get("active_consult_handle"),
        "continuation_state_summary": summary.get("continuation_state_summary"),
        "subagent_handles_summary": summary.get("subagent_handles_summary"),
    }
    if not any(value is not None for value in runtime_summary.values()):
        return None
    return runtime_summary


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


def _effective_permissions_config(config_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = config_data if config_data is not None else _load_agent_config_data()
    permissions_data = payload.get("permissions") if isinstance(payload, dict) else None
    if not isinstance(permissions_data, dict):
        permissions_data = {}
    return {
        "allow_read_only_tools_without_approval": bool(
            permissions_data.get("allow_read_only_tools_without_approval", True)
        ),
        "auto_approve_all": bool(permissions_data.get("auto_approve_all", False)),
        "remember_default_scope": PermissionsConfigModel._validate_remember_default_scope(
            permissions_data.get("remember_default_scope", AUTH_SCOPE_PROJECT)
        ),
        "remember_default_matcher": (
            _normalize_authorization_matcher_config(
                permissions_data.get("remember_default_matcher", AUTH_MATCHER_COMMAND_FINGERPRINT)
            )
            or AUTH_MATCHER_COMMAND_FINGERPRINT
        ),
    }


def _effective_ui_config(config_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = config_data if config_data is not None else _load_agent_config_data()
    ui_data = payload.get("ui") if isinstance(payload, dict) else None
    if not isinstance(ui_data, dict):
        ui_data = {}
    chat_cards_data = ui_data.get("chat_cards")
    if not isinstance(chat_cards_data, dict):
        chat_cards_data = {}
    return {
        "chat_cards": {
            "expand_current_step_by_default": bool(
                chat_cards_data.get("expand_current_step_by_default", False)
            ),
        },
    }


def _configured_sidecar_agent_types(config_data: Optional[Dict[str, Any]] = None) -> set[str] | None:
    payload = config_data if config_data is not None else _load_agent_config_data()
    orchestration_data = payload.get("orchestration") if isinstance(payload, dict) else None
    if not isinstance(orchestration_data, dict) or "sidecar_agent_types" not in orchestration_data:
        return None
    return set(_normalize_sidecar_agent_types(orchestration_data.get("sidecar_agent_types")))


def _context_window_from_provider(provider_data: Any, model_id: str) -> Optional[int]:
    return context_window_from_provider(provider_data, model_id)


def _resolve_llm_context_window(agent_name: str, model_id: str) -> Optional[int]:
    return resolve_llm_context_window(agent_name, model_id)


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


def _build_llm_fact_recorder(
    db: Session,
    task_run: Optional[TaskRun],
    *,
    agent_name: str,
    llm_client: Any,
    client_turn_id: Optional[str],
):
    """Build a stream event callback that records factual LLM timeline events."""

    seen_request_turns: set[int] = set()
    seen_response_started_turns: set[int] = set()

    async def _record(frame: Any, event: Dict[str, Any], _turn_state: TurnContextState) -> None:
        event_type = str(event.get("type") or "")
        turn_index = int(getattr(frame, "turn_index", 1) or 1)
        step_id = f"llm:{agent_name}:{turn_index}"
        if event_type in {"agent_start", "request_sent"} and turn_index not in seen_request_turns:
            seen_request_turns.add(turn_index)
            record_llm_request_created(
                db,
                task_run,
                agent_name=agent_name,
                turn=turn_index,
                model=getattr(llm_client, "model", None),
                client_turn_id=client_turn_id,
                payload=build_llm_request_prompt_payload(
                    frame,
                    elapsed_ms=event.get("elapsed_ms"),
                    step_id=step_id,
                ),
            )
            return
        if event_type in {"first_content", "first_chunk"} and turn_index not in seen_response_started_turns:
            seen_response_started_turns.add(turn_index)
            record_llm_response_started(
                db,
                task_run,
                agent_name=agent_name,
                turn=turn_index,
                client_turn_id=client_turn_id,
                payload={"elapsed_ms": event.get("elapsed_ms"), "step_id": step_id},
            )
            return
        if event_type == "done":
            record_llm_response_completed(
                db,
                task_run,
                agent_name=agent_name,
                turn=turn_index,
                finish_reason=event.get("finish_reason"),
                client_turn_id=client_turn_id,
                payload={
                    "response_preview": str(event.get("full_content") or getattr(frame, "llm_content", "") or "")[:280],
                    "tool_call_count": len(event.get("tool_calls") or []),
                    "step_id": step_id,
                },
            )

    return _record

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
        run_kind=RunKind.STANDALONE_ASSISTANT,
        agent_name=runtime.assistant_name,
        project_id=None,
        tool_names=[],
        streaming=False,
        standalone=True,
    )
    _record_target_agent_selected(
        db,
        task_run,
        agent_name=runtime.assistant_name,
        model=getattr(runtime.llm_client, "model", None),
        tool_names=[],
        client_turn_id=client_turn_id,
        project_id=None,
        run_kind=RunKind.STANDALONE_ASSISTANT,
    )

    record_agent_turn_started(
        db,
        task_run,
        agent_name=runtime.assistant_name,
        summary="",
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
        tool_policy_pack=runtime.tool_policy_pack,
        history_limit=5,
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
        completion_summary="",
        failure_summary=lambda error: f"Agent response failed: {error}",
        extract_memories=extract_agent_memories,
        stream_failure_message_metadata=_message_metadata_with_turn,
        post_publish_success=_build_assistant_auto_handoff_callback(chatroom_id=chatroom_id, source_agent_name=runtime.assistant_name),
    )

    await run_managed_single_agent_sync_runtime_profile(
        build_single_agent_sync_chat_profile(
            runtime_inputs=standalone_runtime_inputs,
            execute_turn=lambda: runtime.llm_client.chat(context_messages, temperature=0.7, max_tokens=1200),
            on_empty=lambda: logger.debug("[ Standalone assistant returned empty response"),
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
        run_kind=RunKind.STANDALONE_ASSISTANT_STREAM,
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
        summary="",
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
            tool_policy_pack=runtime.tool_policy_pack,
            history_limit=5,
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
        completion_summary="",
        failure_summary=lambda error: f"Standalone stream failed: {error}",
        extract_memories=extract_agent_memories,
        stream_failure_message_metadata=_message_metadata_with_turn,
        post_publish_success=_build_assistant_auto_handoff_callback(chatroom_id=chatroom_id, source_agent_name=runtime.assistant_name),
    )

    async for outcome in iter_managed_single_agent_stream_runtime_profile(
        build_single_agent_stream_chat_profile(
            runtime_inputs=standalone_stream_runtime_inputs,
            llm_client=runtime.llm_client,
            tools=None,
            turn_state=runtime.turn_state,
            loop_callbacks=build_single_agent_stream_loop_callbacks(
                assemble_messages=_assemble_standalone_stream_messages,
                execute_tool=_execute_standalone_stream_tool,
                build_llm_runtime_card=_build_standalone_stream_llm_card,
                snapshot_messages=_snapshot_llm_messages,
                preview_tool_calls=_preview_tool_calls,
                format_prompt_messages=_format_json_block,
                tool_result_success=_tool_result_succeeded,
                before_event=chain_before_event_callbacks(
                    _build_llm_fact_recorder(
                        db,
                        task_run,
                        agent_name=runtime.assistant_name,
                        llm_client=runtime.llm_client,
                        client_turn_id=client_turn_id,
                    ),
                    make_stream_audit_before_event(
                        db=db,
                        run_id=getattr(task_run, "id", None),
                        stage_id=None,
                        agent_name=runtime.assistant_name,
                    ),
                ),
            ),
            transport=build_single_agent_stream_transport_context(
                serialize_payload=lambda payload: sse_json.dumps(payload, ensure_ascii=False),
                store_runtime_card=store_runtime_card,
                public_runtime_card_payload=public_runtime_card_payload,
            ),
            max_turns=1,
            stream_failure=build_single_agent_stream_failure_policy(
                detail_builder=traceback.format_exc,
            ),
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
            return {"completed": False, "awaiting_tool_approval": False, "task_run_id": None}

        project = _resolve_chatroom_project(db, chatroom)
        task_run = get_task_run(db, task_run_id) if task_run_id else None
        if task_run is None:
            task_run = create_task_run(
                db,
                chatroom_id=chatroom_id,
                project_id=project.id if project else None,
                origin_message_id=None,
                client_turn_id=client_turn_id,
                run_kind=RunKind.CHAT_TURN,
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
                _record_target_agents_selected(
                    db,
                    task_run,
                    agent_names=mentioned_names,
                    client_turn_id=client_turn_id,
                    project_id=None,
                    run_kind=RunKind.MULTI_AGENT_ORCHESTRATION,
                )
                _select_task_run_runtime_mode(
                    db,
                    task_run,
                    run_kind=RunKind.MULTI_AGENT_ORCHESTRATION,
                    summary="",
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
                return {"completed": True, "awaiting_tool_approval": False, "task_run_id": getattr(task_run, "id", None)}
            standalone_target = _resolve_standalone_target_agent(db, user_message)
            standalone_agent_name = _agent_type(standalone_target) if standalone_target else DEFAULT_AGENT_TYPE
            standalone_policy = _build_single_agent_runner_policy(
                run_kind=RunKind.STANDALONE_ASSISTANT,
                agent_name=standalone_agent_name,
                project_id=None,
                tool_names=[],
                streaming=False,
                standalone=True,
            )
            _select_task_run_runtime_mode(
                db,
                task_run,
                run_kind=RunKind.STANDALONE_ASSISTANT,
                summary="",
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
            return {"completed": True, "awaiting_tool_approval": False, "task_run_id": getattr(task_run, "id", None)}
        
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
            _record_target_agents_selected(
                db,
                task_run,
                agent_names=mentioned_names,
                client_turn_id=client_turn_id,
                project_id=project.id,
                run_kind=RunKind.MULTI_AGENT_ORCHESTRATION,
            )
            _select_task_run_runtime_mode(
                db,
                task_run,
                run_kind=RunKind.MULTI_AGENT_ORCHESTRATION,
                summary="",
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
            return {"completed": True, "awaiting_tool_approval": False, "task_run_id": getattr(task_run, "id", None)}

        target_agent_name = mentioned_names[0] if mentioned_names else (
            str(getattr(task_run, "target_agent_name", "") or "").strip() or None
        )
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
            return {"completed": True, "awaiting_tool_approval": False, "task_run_id": getattr(task_run, "id", None)}

        logger.debug(f"[ Selected agent: {target_agent.name} (role: {target_agent.role})")
        available_tools = _resolve_agent_runtime_tools(target_agent)
        target_llm_client = get_llm_client_for_agent(_agent_type(target_agent))
        single_agent_policy = _build_single_agent_runner_policy(
            run_kind=RunKind.PROJECT_SINGLE_AGENT,
            agent_name=agent_name_of(target_agent),
            project_id=project.id,
            tool_names=available_tools,
            streaming=False,
            standalone=False,
        )
        _select_task_run_runtime_mode(
            db,
            task_run,
            run_kind=RunKind.PROJECT_SINGLE_AGENT,
            target_agent_name=agent_name_of(target_agent),
            agent_name=agent_name_of(target_agent),
            summary="",
            project_id=project.id,
            runner_policy=single_agent_policy,
        )
        _record_target_agent_selected(
            db,
            task_run,
            agent_name=agent_name_of(target_agent),
            model=getattr(target_llm_client, "model", None),
            tool_names=available_tools,
            client_turn_id=client_turn_id,
            project_id=project.id,
            run_kind=RunKind.PROJECT_SINGLE_AGENT,
        )

        # Register all project agents as collaborators so collaboration tools see the full room context.
        registered = ensure_runtime_chatroom_collaborators(
            agents=agents,
            chatroom_id=chatroom_id,
            agent_name_resolver=_agent_type,
        )
        for collaborator in registered:
            logger.info("[Collab] Auto-registered collaborator: %s", collaborator["agent_name"])
        
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
            summary="",
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
                tool_policy_pack=runtime.tool_policy_pack,
                history_limit=5,
                history_visibility="all" if visibility == "all" else "target",
                target_agent_name=runtime.agent_label,
                prefix_assistant_name=visibility == "all",
                runtime_context=build_runtime_environment_context(project),
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
            record_tool_call_started(
                db,
                task_run,
                agent_name=runtime.agent_label,
                turn=frame.turn_index + 1,
                tool_name=tool_name,
                arguments=tool_args_str,
                payload={"tool_call_id": tool_call.get("id")},
            )
            async def emit_tool_progress(progress: dict[str, Any]) -> None:
                await store_runtime_card(
                    chatroom.id,
                    {
                        "type": "tool_call",
                        "source": "chatroom",
                        "agent": runtime.agent_label,
                        "tool": tool_name,
                        "arguments": tool_args_str,
                        "success": None,
                        "status": "running",
                        "blocked": False,
                        "result": str(progress.get("tail_output") or "").strip() or "Tool is running.",
                        "duration_ms": progress.get("duration_ms"),
                        "pid": progress.get("pid"),
                        "tracked_process": progress.get("tracked_process"),
                        "tool_call_id": tool_call.get("id"),
                        "client_turn_id": getattr(task_run, "client_turn_id", None) if task_run is not None else None,
                        "run_id": getattr(task_run, "id", None) if task_run is not None else None,
                        "turn": frame.turn_index + 1,
                    },
                )
            try:
                tool_result = await tool_registry.execute(
                    tool_name,
                    **tool_args,
                    **runtime.runtime_kwargs,
                    task_run_id=getattr(task_run, "id", None) if task_run is not None else None,
                    client_turn_id=getattr(task_run, "client_turn_id", None) if task_run is not None else None,
                    tool_call_id=tool_call.get("id"),
                    turn=frame.turn_index + 1,
                    progress_callback=emit_tool_progress if tool_name == "run_shell" else None,
                )
                tool_success = _tool_result_succeeded(tool_result)
                logger.debug(f"[Tool] Result: {_tool_result_text(tool_result)[:150]}...")
            except Exception as te:
                tool_result = f"Error executing {tool_name}: {str(te)}"
                tool_success = False
                logger.debug(f"[Tool] Error: {te}")
            return build_tool_result_record(
                tool_call_id=tool_call.get("id"),
                tool_name=tool_name,
                arguments=tool_args_str,
                result=tool_result,
                success=tool_success,
            )

        async def _on_project_single_agent_tool_round(frame, tool_results, current_turn_state):
            logger.info(f"[LLM] Loop iteration {frame.turn_index + 1}")
            logger.info(
                f"[LLM] Response received: {frame.content[:100] if frame.content else 'None'}..."
            )
            logger.info(f"[LLM] Tool calls: {frame.normalized_tool_calls}")
            recorded_tool_calls = frame.executed_tool_calls or frame.normalized_tool_calls
            blocked_tool_result = getattr(frame, "blocked_tool_result", None)
            record_runner_tool_round(
                db,
                task_run,
                agent_name=runtime.agent_label,
                turn=frame.turn_index + 1,
                tool_names=[
                    tool_call["function"]["name"]
                    for tool_call in (
                        recorded_tool_calls
                        + ([{
                            "function": {"name": blocked_tool_result.tool_name},
                        }] if blocked_tool_result is not None else [])
                    )
                ],
                tool_results=tool_results,
                blocked_tool_results=[blocked_tool_result] if blocked_tool_result is not None else None,
                summary=f"{runtime.agent_label} completed a tool round.",
                assistant_content=frame.content,
            )
            background_result = next((tool_result for tool_result in tool_results if _tool_result_is_background_running(tool_result)), None)
            if background_result is not None:
                tracked_process = (
                    getattr(background_result, "metadata", {}).get("tracked_process")
                    if isinstance(getattr(background_result, "metadata", None), dict)
                    else {}
                ) or {}
                _spawn_background_tracked_run_shell_watch(
                    task_run.id,
                    {
                        "type": "tool_call",
                        "source": "non_stream_background_wait",
                        "agent": runtime.agent_label,
                        "tool": "run_shell",
                        "arguments": str(getattr(background_result, "arguments", "") or "{}"),
                        "success": None,
                        "status": "running",
                        "blocked": False,
                        "result": str(getattr(background_result, "result", "") or "").strip() or "run_shell is running.",
                        "tracked_process": tracked_process,
                        "tool_call_id": getattr(background_result, "tool_call_id", None),
                        "client_turn_id": getattr(task_run, "client_turn_id", None),
                        "run_id": getattr(task_run, "id", None),
                        "turn": frame.turn_index + 1,
                    },
                )
                return {
                    "stop": True,
                    "final_content": "",
                    "awaiting_background_tool": True,
                }
            if blocked_tool_result is None:
                parent_context = _find_delegated_parent_context(db, task_run)
                if parent_context is not None:
                    required_outputs = parent_context.get("required_outputs") if isinstance(parent_context.get("required_outputs"), list) else []
                    is_tester = str(parent_context.get("target_agent_name") or runtime.agent_label or "").strip().lower() == "tester"
                    if "test_report" in required_outputs or is_tester:
                        for tool_result in tool_results:
                            if str(getattr(tool_result, "tool_name", "") or "").strip().lower() != "run_shell":
                                continue
                            reported = await _report_delegated_child_result_to_parent(
                                db,
                                task_run,
                                parent_context=parent_context,
                                tool_result=tool_result,
                                arguments=str(getattr(tool_result, "arguments", "") or "{}"),
                                tracked_process=(
                                    getattr(tool_result, "metadata", {}).get("tracked_process")
                                    if isinstance(getattr(tool_result, "metadata", None), dict)
                                    else {}
                                )
                                or {},
                            )
                            if reported:
                                return {
                                    "stop": True,
                                    "final_content": f"{runtime.agent_label} produced required test_report and returned ownership to {parent_context.get('delegator') or 'the parent owner'}.",
                                }
        awaiting_tool_approval = False
        awaiting_background_tool = False

        async def _execute_project_single_agent_turn():
            nonlocal awaiting_tool_approval, awaiting_background_tool
            audit_cbs = make_nonstream_audit_callbacks(
                db=db,
                run_id=getattr(task_run, "id", None),
                stage_id=None,
                agent_name=runtime.agent_label,
            )
            _original_on_tool_round = _on_project_single_agent_tool_round

            async def _chained_on_tool_round(frame, tool_results, turn_state):
                # Audit recording first
                if audit_cbs.get("on_tool_round"):
                    await audit_cbs["on_tool_round"](frame, tool_results, turn_state)
                # Then original handler
                return await _original_on_tool_round(frame, tool_results, turn_state)

            loop_result = await execute_non_stream_turn_loop(
                llm_client=runtime.llm_client,
                tools=runtime.tool_schemas,
                turn_state=runtime.turn_state,
                assemble_messages=_assemble_project_single_agent_messages,
                execute_tool_call=_execute_project_single_agent_tool,
                max_turns=MAX_TOOL_ITERATIONS,
                on_tool_round=_chained_on_tool_round,
                before_llm_call=audit_cbs["before_llm_call"],
                on_llm_response=audit_cbs["on_llm_response"],
                on_llm_error=audit_cbs["on_llm_error"],
            )
            awaiting_tool_approval = loop_result.awaiting_tool_approval
            awaiting_background_tool = loop_result.awaiting_background_tool
            return loop_result.final_content or None

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
            completion_summary="",
            failure_summary=lambda error: f"Agent response failed: {error}",
            extract_memories=extract_agent_memories,
            stream_failure_message_metadata=_message_metadata_with_turn,
            post_publish_success=_build_assistant_auto_handoff_callback(chatroom_id=chatroom_id, source_agent_name=runtime.agent_label),
        )

        finalized = await run_managed_single_agent_sync_runtime_profile(
            build_single_agent_sync_chat_profile(
                runtime_inputs=project_single_agent_runtime_inputs,
                execute_turn=_execute_project_single_agent_turn,
                on_empty=lambda: None if awaiting_tool_approval or awaiting_background_tool else logger.error(f"[ LLM returned empty response after all tool iterations"),
            )
        )

        if finalized.final_content:
            logger.debug(f"[ Agent response saved: id=completed")
            logger.info(f"[Agent] {_agent_type(target_agent)} responded to message successfully")
            return {
                "completed": True,
                "awaiting_tool_approval": False,
                "awaiting_background_tool": False,
                "task_run_id": getattr(task_run, "id", None),
                "outcome": finalized.outcome,
            }

        return {
            "completed": False,
            "awaiting_tool_approval": awaiting_tool_approval,
            "awaiting_background_tool": awaiting_background_tool,
            "task_run_id": getattr(task_run, "id", None),
            "outcome": finalized.outcome,
        }

    except asyncio.CancelledError:
        logger.warning("[ Agent response interrupted during shutdown")
        raise
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
        return {
            "completed": True,
            "awaiting_tool_approval": False,
            "awaiting_background_tool": False,
            "task_run_id": getattr(task_run, "id", None),
            "outcome": "failed",
        }
    finally:
        if workspace_token is not None:
            reset_active_workspace(workspace_token)
        db.close()


def _compact_runtime_text(value: Any, *, limit: int = 600) -> str:
    return compact_orchestration_text(value, limit=limit)


def _tool_result_text(value: Any) -> str:
    if isinstance(value, dict) and value.get("__catown_tool_result__") is True:
        return str(value.get("result") or "(no output)")
    return str(value) if value is not None else "(no output)"


def _tool_result_is_background_running(tool_result: Any) -> bool:
    if str(getattr(tool_result, "tool_name", "") or "").strip().lower() != "run_shell":
        return False
    return str(getattr(tool_result, "status", "") or "").strip().lower() == "background_running"


_TRACKED_RUN_SHELL_WATCH_LOCK = threading.Lock()
_TRACKED_RUN_SHELL_WATCH_TOKENS: set[str] = set()


def _claim_tracked_run_shell_watch(token: str | None) -> bool:
    normalized = str(token or "").strip()
    if not normalized:
        return False
    with _TRACKED_RUN_SHELL_WATCH_LOCK:
        if normalized in _TRACKED_RUN_SHELL_WATCH_TOKENS:
            return False
        _TRACKED_RUN_SHELL_WATCH_TOKENS.add(normalized)
        return True


def _release_tracked_run_shell_watch(token: str | None) -> None:
    normalized = str(token or "").strip()
    if not normalized:
        return
    with _TRACKED_RUN_SHELL_WATCH_LOCK:
        _TRACKED_RUN_SHELL_WATCH_TOKENS.discard(normalized)


def _load_jsonish_payload(raw_value: Any) -> Dict[str, Any]:
    if isinstance(raw_value, dict):
        return dict(raw_value)
    if not raw_value:
        return {}
    try:
        loaded = json.loads(str(raw_value))
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


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
    targets = _resolve_orchestration_targets(db, project, agents, agent_names)
    resolved_agents = [agent for _, agent in targets if agent is not None]
    available_tools = {
        agent_name_of(agent): _resolve_agent_runtime_tools(agent)
        for agent in resolved_agents
    }
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
        tool_names=sorted({tool_name for tool_names in available_tools.values() for tool_name in tool_names}),
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
    summary: str = "",
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
        EventType.RUNTIME_MODE_SELECTED,
        agent_name=agent_name,
        summary=summary or "",
        payload=payload,
    )


def _record_target_agent_selected(
    db: Session,
    task_run: Optional[TaskRun],
    *,
    agent_name: str,
    model: Optional[str] = None,
    tool_names: Optional[List[str]] = None,
    client_turn_id: Optional[str] = None,
    project_id: Optional[int] = None,
    run_kind: Optional[str] = None,
) -> None:
    append_task_event(
        db,
        task_run,
        EventType.TARGET_AGENT_SELECTED,
        agent_name=agent_name,
        summary="",
        payload={
            "agent_name": agent_name,
            "model": model,
            "tool_names": tool_names or [],
            "client_turn_id": client_turn_id,
            "project_id": project_id,
            "run_kind": run_kind,
        },
    )


def _record_target_agents_selected(
    db: Session,
    task_run: Optional[TaskRun],
    *,
    agent_names: List[str],
    client_turn_id: Optional[str] = None,
    project_id: Optional[int] = None,
    run_kind: Optional[str] = None,
) -> None:
    normalized_names = [str(name or "").strip() for name in agent_names if str(name or "").strip()]
    append_task_event(
        db,
        task_run,
        EventType.TARGET_AGENTS_SELECTED,
        summary="",
        payload={
            "agent_names": normalized_names,
            "client_turn_id": client_turn_id,
            "project_id": project_id,
            "run_kind": run_kind,
        },
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
    from tools import tool_registry as runtime_tool_registry

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
        tool_policy_pack=runtime_tool_registry.get_policy_pack([]),
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


def _latest_task_run_event(task_run: TaskRun) -> Optional[TaskRunEvent]:
    events = list(getattr(task_run, "events", []) or [])
    return events[-1] if events else None


def _task_run_has_interrupted_approval_followup(task_run: TaskRun) -> bool:
    latest_event = _latest_task_run_event(task_run)
    if latest_event is None:
        return False
    return str(getattr(latest_event, "event_type", "") or "").strip() == EventType.APPROVAL_QUEUE_ITEM_FOLLOWUP_INTERRUPTED


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


def _terminalize_interrupted_single_agent_task_run(
    db: Session,
    task_run: TaskRun,
    *,
    trigger: str,
) -> bool:
    run_kind = str(task_run.run_kind or "").strip()
    if run_kind not in INTERRUPTIBLE_SINGLE_AGENT_RUN_KINDS:
        return False
    if str(task_run.status or "").strip().lower() != "running":
        return False
    pending_approvals = [
        item
        for item in list(getattr(task_run, "approval_queue_items", []) or [])
        if str(getattr(item, "status", "") or "").strip().lower() == "pending"
    ]
    if pending_approvals:
        return False

    latest_event = (
        db.query(TaskRunEvent)
        .filter(TaskRunEvent.task_run_id == task_run.id)
        .order_by(TaskRunEvent.event_index.desc(), TaskRunEvent.id.desc())
        .first()
    )
    summary = "Task run was interrupted by a backend restart before it could finish."
    append_task_event(
        db,
        task_run,
        EventType.TASK_RUN_INTERRUPTED,
        agent_name=task_run.target_agent_name,
        summary=summary,
        payload={
            "task_run_id": task_run.id,
            "run_kind": run_kind,
            "trigger": trigger,
            "latest_event_type": latest_event.event_type if latest_event is not None else None,
            "latest_event_index": latest_event.event_index if latest_event is not None else None,
            "latest_event_at": latest_event.created_at.isoformat() if latest_event is not None and latest_event.created_at else None,
        },
    )
    complete_task_run(db, task_run, status="failed", summary=summary)
    return True


def _find_tracked_run_shell_from_runtime_cards(db: Session, task_run: TaskRun) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    rows = (
        db.query(Message)
        .filter(Message.chatroom_id == task_run.chatroom_id, Message.message_type == "runtime_card")
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(500)
        .all()
    )
    for row in rows:
        try:
            metadata = json.loads(row.metadata_json or "{}")
        except json.JSONDecodeError:
            continue
        card = metadata.get("card")
        if not isinstance(card, dict):
            continue
        if str(card.get("tool") or "").strip().lower() != "run_shell":
            continue
        run_id = card.get("run_id")
        if run_id is not None and str(run_id) != str(task_run.id):
            continue
        if run_id is None and card.get("client_turn_id") and card.get("client_turn_id") != task_run.client_turn_id:
            continue
        tracked = card.get("tracked_process")
        handle = load_tracked_run_shell_handle(tracked) if isinstance(tracked, dict) else None
        if handle is not None:
            return handle, {"source": "runtime_card", "runtime_message_id": row.id}
    return None, {}


def _find_tracked_run_shell_from_state(task_run: TaskRun) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    try:
        state_files = sorted(
            run_shell_process_state_dir().glob("*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return None, {}

    for path in state_files:
        if path.name.endswith(".exit.json"):
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict):
            continue
        if str(record.get("task_run_id") or "") == str(task_run.id):
            return record, {"source": "state_file", "state_path": str(path)}
        if task_run.client_turn_id and record.get("client_turn_id") == task_run.client_turn_id:
            return record, {"source": "state_file", "state_path": str(path)}
    return None, {}


def _find_tracked_run_shell_for_task_run(db: Session, task_run: TaskRun) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    handle, source = _find_tracked_run_shell_from_runtime_cards(db, task_run)
    if handle is not None:
        return handle, source
    return _find_tracked_run_shell_from_state(task_run)


def _tracked_run_shell_final_card_payload(card: Dict[str, Any], record: Dict[str, Any]) -> Dict[str, Any] | None:
    result = build_tracked_run_shell_result(record, max_chars=50000)
    if not isinstance(result, dict) or result.get("__catown_tool_result__") is not True:
        return None
    next_card = dict(card)
    next_card["success"] = bool(result.get("success"))
    next_card["status"] = str(result.get("status") or ("succeeded" if result.get("success") else "failed"))
    next_card["blocked"] = bool(result.get("blocked"))
    next_card["blocked_kind"] = result.get("blocked_kind")
    next_card["blocked_reason"] = result.get("blocked_reason")
    next_card["result"] = str(result.get("result") or "").strip() or "Tracked run_shell finished without output."
    next_card["pid"] = record.get("pid") if isinstance(record.get("pid"), int) else next_card.get("pid")
    tracked = next_card.get("tracked_process") if isinstance(next_card.get("tracked_process"), dict) else {}
    next_card["tracked_process"] = {
        **tracked,
        "token": record.get("token") or tracked.get("token"),
        "pid": record.get("pid"),
        "worker_pid": record.get("worker_pid"),
        "pgid": record.get("pgid"),
        "status": record.get("status"),
        "finished_at": record.get("finished_at"),
        "task_run_id": record.get("task_run_id") or tracked.get("task_run_id"),
        "client_turn_id": record.get("client_turn_id") or tracked.get("client_turn_id"),
        "tool_call_id": record.get("tool_call_id") or tracked.get("tool_call_id"),
    }
    return next_card


def _build_tracked_run_shell_progress_card(
    *,
    task_run: TaskRun,
    base_card: Dict[str, Any],
    progress: Dict[str, Any],
) -> Dict[str, Any]:
    tracked = progress.get("tracked_process") if isinstance(progress.get("tracked_process"), dict) else {}
    return {
        "type": "tool_call",
        "source": "tracked_background_watch",
        "agent": str(base_card.get("agent") or task_run.target_agent_name or "agent").strip() or "agent",
        "tool": "run_shell",
        "arguments": str(base_card.get("arguments") or "{}"),
        "success": None,
        "status": "running",
        "blocked": False,
        "result": str(progress.get("tail_output") or "").strip() or "run_shell is running.",
        "duration_ms": progress.get("duration_ms"),
        "pid": progress.get("pid"),
        "tracked_process": tracked,
        "tool_call_id": base_card.get("tool_call_id") or tracked.get("tool_call_id"),
        "client_turn_id": task_run.client_turn_id,
        "run_id": task_run.id,
        "turn": base_card.get("turn"),
    }


def _append_tracked_run_shell_completed_event(
    db: Session,
    task_run: TaskRun | None,
    next_card: Dict[str, Any],
) -> bool:
    if task_run is None:
        return False
    if str(getattr(task_run, "status", "") or "").strip().lower() != "running":
        return False
    token = (
        str((next_card.get("tracked_process") or {}).get("token") or "").strip()
        if isinstance(next_card.get("tracked_process"), dict)
        else ""
    ) or None
    tool_call_id = str(next_card.get("tool_call_id") or "").strip() or None
    if _task_run_has_tracked_shell_followup(
        task_run,
        token=token,
        tool_call_id=tool_call_id,
        include_completed_marker=True,
    ):
        return False
    if _task_run_has_terminal_tool_round_result(task_run, token=token, tool_call_id=tool_call_id):
        return False
    append_task_event(
        db,
        task_run,
        EventType.TRACKED_RUN_SHELL_COMPLETED,
        agent_name=(task_run.target_agent_name or next_card.get("agent") or "").strip() or None,
        summary="Tracked run_shell completed; agent follow-up will analyze the result.",
        payload={
            "tool_name": "run_shell",
            "tool_call_id": next_card.get("tool_call_id"),
            "tool_status": next_card.get("status"),
            "tool_success": bool(next_card.get("success")),
            "tracked_process": next_card.get("tracked_process"),
        },
    )
    return True


async def _watch_background_tracked_run_shell_async(task_run_id: int, base_card: Dict[str, Any]) -> None:
    tracked = base_card.get("tracked_process") if isinstance(base_card.get("tracked_process"), dict) else {}
    token = str(tracked.get("token") or "").strip() or None
    if not _claim_tracked_run_shell_watch(token):
        return
    db = SessionLocal()
    try:
        task_run = get_task_run(db, task_run_id)
        if task_run is None or not isinstance(getattr(task_run, "chatroom_id", None), int):
            return
        handle = load_tracked_run_shell_handle(tracked) or tracked
        if not isinstance(handle, dict):
            return

        async def emit_progress(progress: dict[str, Any]) -> None:
            await store_runtime_card(
                task_run.chatroom_id,
                _build_tracked_run_shell_progress_card(
                    task_run=task_run,
                    base_card=base_card,
                    progress=progress,
                ),
            )

        result = await wait_for_tracked_run_shell(
            handle,
            progress_callback=emit_progress,
            timeout_seconds=None,
            progress_interval_seconds=2.0,
            tail_chars=4000,
            result_chars=50000,
        )
        record = load_tracked_run_shell_handle(handle) or handle
        next_card = _tracked_run_shell_final_card_payload(base_card, record)
        if next_card is None:
            next_card = {
                **base_card,
                "success": bool(result.get("success")) if isinstance(result, dict) else False,
                "status": str(result.get("status") or "failed") if isinstance(result, dict) else "failed",
                "blocked": bool(result.get("blocked")) if isinstance(result, dict) else False,
                "blocked_kind": result.get("blocked_kind") if isinstance(result, dict) else None,
                "blocked_reason": result.get("blocked_reason") if isinstance(result, dict) else None,
                "result": str(result.get("result") or "").strip() if isinstance(result, dict) else "Tracked run_shell finished without output.",
                "tracked_process": record,
            }
        await store_runtime_card(task_run.chatroom_id, next_card)
        task_run = get_task_run(db, task_run_id)
        if _append_tracked_run_shell_completed_event(db, task_run, next_card):
            db.commit()
            _spawn_tracked_run_shell_followup(task_run_id, dict(next_card))
        else:
            db.commit()
    except Exception as exc:
        logger.exception("Tracked run_shell background watch failed for task_run_id=%s: %s", task_run_id, exc)
    finally:
        db.close()
        _release_tracked_run_shell_watch(token)


def _spawn_background_tracked_run_shell_watch(task_run_id: int, base_card: Dict[str, Any]) -> None:
    def _runner() -> None:
        try:
            asyncio.run(_watch_background_tracked_run_shell_async(task_run_id, dict(base_card)))
        except Exception as exc:
            logger.exception("Tracked run_shell background watcher crashed for task_run_id=%s: %s", task_run_id, exc)

    thread = threading.Thread(
        target=_runner,
        name=f"tracked-run-shell-watch-{task_run_id}",
        daemon=True,
    )
    thread.start()


def _runtime_card_is_running_tracked_run_shell(card: Dict[str, Any]) -> bool:
    return (
        str(card.get("type") or "").strip().lower() == "tool_call"
        and str(card.get("tool") or "").strip().lower() == "run_shell"
        and str(card.get("status") or "").strip().lower() == "running"
        and isinstance(card.get("tracked_process"), dict)
        and not _is_internal_tool_pause(card)
    )


def _task_run_has_tracked_shell_followup(
    task_run: TaskRun | None,
    *,
    token: str | None = None,
    tool_call_id: str | None = None,
    include_completed_marker: bool = False,
) -> bool:
    if task_run is None:
        return False
    event_types = [
        EventType.TRACKED_RUN_SHELL_FOLLOWUP_QUEUED,
        EventType.APPROVAL_QUEUE_ITEM_FOLLOWUP_TRIGGERED,
        EventType.AGENT_TURN_COMPLETED,
    ]
    if include_completed_marker:
        event_types.append(EventType.TRACKED_RUN_SHELL_COMPLETED)
    if token or tool_call_id:
        event_types.append(EventType.RUN_SHELL_CONTINUATION_CLAIMED)
    normalized_token = str(token or "").strip()
    normalized_tool_call_id = str(tool_call_id or "").strip()

    def _event_matches(payload: Dict[str, Any]) -> bool:
        tracked = payload.get("tracked_process") if isinstance(payload.get("tracked_process"), dict) else {}
        event_status = str(payload.get("status") or "").strip().lower()
        event_type = str(payload.get("event_type") or "").strip().lower()
        if event_status == "claimed":
            return False
        action_taken = str(payload.get("action_taken") or "").strip().lower()
        tool_status = str(payload.get("tool_status") or "").strip().lower()
        if action_taken == "run_shell_continued_after_approval" and event_status == "completed":
            if tool_status in {"", "background_running", "running", "approval_blocked"}:
                return False
        if normalized_token and tracked.get("token") == normalized_token:
            return True
        if normalized_tool_call_id and str(payload.get("tool_call_id") or "").strip() == normalized_tool_call_id:
            return True
        if normalized_tool_call_id and str(tracked.get("tool_call_id") or "").strip() == normalized_tool_call_id:
            return True
        return False

    query_session = object_session(task_run)
    if query_session is not None:
        query = (
            query_session.query(TaskRunEvent)
            .filter(
                TaskRunEvent.task_run_id == task_run.id,
                TaskRunEvent.event_type.in_(event_types),
            )
        )
        if not normalized_token and not normalized_tool_call_id:
            return query.first() is not None
        for event in query.all():
            payload = _task_run_event_payload(event)
            if _event_matches(payload):
                return True
        return False

    for event in list(getattr(task_run, "events", []) or []):
        if event.event_type not in event_types:
            continue
        payload = _task_run_event_payload(event)
        if not normalized_token and not normalized_tool_call_id:
            return True
        if _event_matches(payload):
            return True
    return False


def _task_run_has_terminal_tool_round_result(
    task_run: TaskRun | None,
    *,
    tool_call_id: str | None = None,
    token: str | None = None,
) -> bool:
    if task_run is None:
        return False
    normalized_tool_call_id = str(tool_call_id or "").strip()
    normalized_token = str(token or "").strip()
    if not normalized_tool_call_id and not normalized_token:
        return False

    def _result_matches(result: Any) -> bool:
        if not isinstance(result, dict):
            return False
        if str(result.get("tool_name") or "").strip().lower() != "run_shell":
            return False
        status = str(result.get("status") or "").strip().lower()
        if status in {"", "background_running", "running", "approval_blocked"}:
            return False
        if bool(result.get("blocked")):
            return False
        if normalized_tool_call_id and str(result.get("tool_call_id") or "").strip() == normalized_tool_call_id:
            return True
        metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        tracked = metadata.get("tracked_process") if isinstance(metadata.get("tracked_process"), dict) else {}
        if normalized_token and str(tracked.get("token") or "").strip() == normalized_token:
            return True
        if normalized_tool_call_id and str(tracked.get("tool_call_id") or "").strip() == normalized_tool_call_id:
            return True
        return False

    def _event_matches(event: TaskRunEvent) -> bool:
        payload = _task_run_event_payload(event)
        turn_state = payload.get("turn_local_state") if isinstance(payload.get("turn_local_state"), dict) else {}
        tool_results = turn_state.get("tool_results") if isinstance(turn_state.get("tool_results"), list) else []
        return any(_result_matches(result) for result in tool_results)

    query_session = object_session(task_run)
    if query_session is not None:
        query = (
            query_session.query(TaskRunEvent)
            .filter(
                TaskRunEvent.task_run_id == task_run.id,
                TaskRunEvent.event_type == EventType.TOOL_ROUND_RECORDED,
            )
        )
        return any(_event_matches(event) for event in query.all())

    return any(
        event.event_type == EventType.TOOL_ROUND_RECORDED and _event_matches(event)
        for event in list(getattr(task_run, "events", []) or [])
    )


def _task_run_has_delegated_result_report(task_run: TaskRun | None, *, token: str | None = None) -> bool:
    if task_run is None:
        return False
    query_session = object_session(task_run)
    events: list[TaskRunEvent]
    if query_session is not None:
        events = (
            query_session.query(TaskRunEvent)
            .filter(
                TaskRunEvent.task_run_id == task_run.id,
                TaskRunEvent.event_type == EventType.DELEGATED_TASK_RESULT_REPORTED,
            )
            .all()
        )
    else:
        events = [
            event
            for event in list(getattr(task_run, "events", []) or [])
            if getattr(event, "event_type", None) == EventType.DELEGATED_TASK_RESULT_REPORTED
        ]
    if not token:
        return bool(events)
    for event in events:
        payload = _task_run_event_payload(event)
        tracked = payload.get("tracked_process") if isinstance(payload.get("tracked_process"), dict) else {}
        if tracked.get("token") == token:
            return True
    return False


def _find_delegated_parent_context(db: Session, child_task_run: TaskRun) -> dict[str, Any] | None:
    child_client_turn_id = str(getattr(child_task_run, "client_turn_id", "") or "").strip()
    if not child_client_turn_id.startswith("delegate-"):
        return None

    rows = (
        db.query(Message)
        .filter(Message.chatroom_id == child_task_run.chatroom_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(200)
        .all()
    )
    for row in rows:
        metadata = _load_jsonish_payload(getattr(row, "metadata_json", None))
        if str(metadata.get("client_turn_id") or "").strip() != child_client_turn_id:
            continue
        delegated_task = metadata.get("delegated_task") if isinstance(metadata.get("delegated_task"), dict) else {}
        parent_task_run_id = metadata.get("parent_task_run_id") or delegated_task.get("parent_task_run_id")
        if not parent_task_run_id:
            continue
        parent_task_run = get_task_run(db, parent_task_run_id)
        if parent_task_run is None:
            continue
        return {
            "parent_task_run": parent_task_run,
            "task_id": delegated_task.get("task_id"),
            "task_title": delegated_task.get("task_title") or child_task_run.title,
            "task_description": delegated_task.get("task_description"),
            "delegator": delegated_task.get("delegator") or parent_task_run.target_agent_name,
            "target_agent_name": delegated_task.get("target_agent_name") or child_task_run.target_agent_name,
            "child_client_turn_id": child_client_turn_id,
            "required_outputs": delegated_task.get("required_outputs") if isinstance(delegated_task.get("required_outputs"), list) else [],
        }

    parent_events = (
        db.query(TaskRunEvent)
        .filter(TaskRunEvent.event_type == "delegated_task_dispatched")
        .order_by(TaskRunEvent.id.desc())
        .limit(500)
        .all()
    )
    for event in parent_events:
        payload = _task_run_event_payload(event)
        if str(payload.get("child_client_turn_id") or "").strip() != child_client_turn_id:
            continue
        parent_task_run = get_task_run(db, getattr(event, "task_run_id", None))
        if parent_task_run is None:
            continue
        return {
            "parent_task_run": parent_task_run,
            "task_id": payload.get("task_id"),
            "task_title": payload.get("task_title") or child_task_run.title,
            "task_description": payload.get("task_description"),
            "delegator": payload.get("from_agent") or parent_task_run.target_agent_name,
            "target_agent_name": payload.get("target_agent_name") or payload.get("to_agent") or child_task_run.target_agent_name,
            "child_client_turn_id": child_client_turn_id,
            "required_outputs": payload.get("required_outputs") if isinstance(payload.get("required_outputs"), list) else [],
        }
    return None


def _parse_run_shell_arguments(arguments: str) -> dict[str, str]:
    try:
        parsed_arguments = json.loads(arguments or "{}")
        if isinstance(parsed_arguments, dict):
            return {
                "command": str(parsed_arguments.get("command") or "").strip(),
                "cwd": str(parsed_arguments.get("cwd") or "").strip(),
            }
    except Exception:
        pass
    return {"command": "", "cwd": ""}


def _build_required_test_report(
    *,
    child_task_run: TaskRun,
    parent_context: dict[str, Any],
    tool_result: Any,
    arguments: str,
) -> dict[str, Any]:
    parsed_arguments = _parse_run_shell_arguments(arguments)
    raw_result = str(getattr(tool_result, "result", "") or "")
    runner_result = normalize_test_runner_result(
        command=parsed_arguments.get("command"),
        cwd=parsed_arguments.get("cwd"),
        status=getattr(tool_result, "status", None),
        success=getattr(tool_result, "success", None),
        result_text=raw_result,
    )
    counts = runner_result.get("counts") if isinstance(runner_result.get("counts"), dict) else {}
    status = str(runner_result.get("test_status") or getattr(tool_result, "status", None) or "unknown").strip()
    success = bool(getattr(tool_result, "success", False))
    failed_count = counts.get("failed") or 0
    error_count = counts.get("errors") or 0
    valid_test_result = is_valid_test_runner_result(runner_result)
    severity = "blocker" if (valid_test_result and (not success or failed_count or error_count)) else "none"
    return {
        "kind": "test_report",
        "version": 1,
        "runner_result": runner_result,
        "task_id": parent_context.get("task_id"),
        "task_title": parent_context.get("task_title") or child_task_run.title,
        "producer": {
            "agent_name": parent_context.get("target_agent_name") or child_task_run.target_agent_name,
            "agent_type": "tester",
        },
        "command": parsed_arguments.get("command"),
        "cwd": parsed_arguments.get("cwd"),
        "status": status,
        "success": success,
        "counts": counts,
        "severity": severity,
        "blocked": severity == "blocker",
        "failure_summary": extract_pytest_failure_summary(raw_result),
        "raw_output_preview": _compact_runtime_text(raw_result, limit=3000),
        "suggested_next_step": (
            "Open a blocker/bug item and let the parent owner decide whether to route to Developer or adjust scope."
            if severity == "blocker"
            else "Parent owner may accept the test result or request additional validation."
        ),
    }


def _delegated_test_report_context(
    *,
    child_task_run: TaskRun,
    parent_context: dict[str, Any],
    test_report: dict[str, Any],
) -> tuple[str, str]:
    task_title = str(test_report.get("task_title") or child_task_run.title or "delegated task").strip()
    child_agent = str(parent_context.get("target_agent_name") or child_task_run.target_agent_name or "Tester").strip()
    parent_agent = str(parent_context.get("delegator") or "owner").strip()
    status = str(test_report.get("status") or "unknown").strip()
    success = bool(test_report.get("success"))
    counts = test_report.get("counts") if isinstance(test_report.get("counts"), dict) else {}
    summary = f"{child_agent} produced required test_report for '{task_title}' and reported it to {parent_agent}: {status}."
    lines = [
        f"@{parent_agent} Test report from {child_agent}.",
        "",
        f"Task: {task_title}",
        "Required output: test_report",
        f"Status: {status}",
        f"Success: {str(success).lower()}",
        f"Severity: {test_report.get('severity') or 'unknown'}",
        f"Blocked: {str(bool(test_report.get('blocked'))).lower()}",
    ]
    if test_report.get("command"):
        lines.append(f"Command: {test_report.get('command')}")
    if test_report.get("cwd"):
        lines.append(f"Working directory: {test_report.get('cwd')}")
    count_parts = []
    for key in ("passed", "failed", "skipped", "errors"):
        if counts.get(key) is not None:
            count_parts.append(f"{key}={counts.get(key)}")
    if count_parts:
        lines.append(f"Counts: {', '.join(count_parts)}")
    lines.extend(
        [
            "",
            "Failure summary:",
            str(test_report.get("failure_summary") or "(none)"),
            "",
            "Suggested next step:",
            str(test_report.get("suggested_next_step") or "Parent owner decides next action."),
            "",
            f"{parent_agent} owns the next-step decision.",
        ]
    )
    return summary, "\n".join(lines)


def _persist_test_report_asset(
    db: Session,
    *,
    child_task_run: TaskRun,
    parent_context: dict[str, Any],
    test_report: dict[str, Any],
    report_content: str,
) -> Asset | None:
    project_id = getattr(child_task_run, "project_id", None)
    if not isinstance(project_id, int):
        return None
    project = getattr(child_task_run, "project", None)
    child_agent_name = str(parent_context.get("target_agent_name") or child_task_run.target_agent_name or "Tester").strip() or "Tester"
    task_title = str(test_report.get("task_title") or child_task_run.title or "Test report").strip() or "Test report"
    task_ref = str(parent_context.get("task_id") or getattr(child_task_run, "client_turn_id", None) or child_task_run.id)
    pipeline_run_id = getattr(child_task_run, "pipeline_run_id", None)
    counts = test_report.get("counts") if isinstance(test_report.get("counts"), dict) else {}
    failure_summary = str(test_report.get("failure_summary") or "").strip()
    front_matter = [
        "# Test Report",
        "",
        f"- Task: {task_title}",
        f"- Producer: {child_agent_name}",
        f"- Status: {test_report.get('status') or 'unknown'}",
        f"- Success: {str(bool(test_report.get('success'))).lower()}",
        f"- Severity: {test_report.get('severity') or 'unknown'}",
        f"- Blocked: {str(bool(test_report.get('blocked'))).lower()}",
    ]
    if test_report.get("command"):
        front_matter.append(f"- Command: `{test_report.get('command')}`")
    if test_report.get("cwd"):
        front_matter.append(f"- Working directory: `{test_report.get('cwd')}`")
    count_parts = [f"{key}={counts.get(key)}" for key in ("passed", "failed", "skipped", "errors") if counts.get(key) is not None]
    if count_parts:
        front_matter.append(f"- Counts: {', '.join(count_parts)}")
    markdown = "\n".join(front_matter) + "\n\n## Report\n\n" + report_content.strip()
    if failure_summary:
        markdown += "\n\n## Failure Summary\n\n```text\n" + failure_summary + "\n```"
    storage_path = build_timestamped_artifact_path(
        directory="reports/tests",
        subject=slug_artifact_subject(task_title) or "test-report",
        run_ref=str(pipeline_run_id) if pipeline_run_id is not None else None,
        task_ref=task_ref,
        extension=".md",
    )
    _materialize_project_asset_workspace_file(project, storage_path, markdown)
    asset = Asset(
        project_id=project_id,
        asset_type="document.test_report",
        title=f"Test report - {task_title}",
        summary=str(test_report.get("suggested_next_step") or "").strip() or f"Structured test report for {task_title}.",
        content_json=json.dumps(test_report, ensure_ascii=False),
        content_markdown=markdown,
        status="current",
        is_current=True,
        owner_agent=child_agent_name,
        source_input_refs_json=json.dumps(
            [
                str(parent_context.get("task_id") or ""),
                str(getattr(child_task_run, "client_turn_id", None) or ""),
            ],
            ensure_ascii=False,
        ),
        storage_path=storage_path,
    )
    db.add(asset)
    db.flush()
    return asset


def _materialize_project_asset_workspace_file(
    project: Project | None,
    relative_path: str,
    content: str,
) -> str | None:
    workspace_path = str(getattr(project, "workspace_path", "") or "").strip()
    normalized_relative = str(relative_path or "").replace("\\", "/").strip().lstrip("/")
    if not workspace_path or not normalized_relative:
        return None
    try:
        workspace = Path(workspace_path).expanduser().resolve()
        if not workspace.exists() or not workspace.is_dir():
            return None
        target = (workspace / normalized_relative).resolve()
        try:
            target.relative_to(workspace)
        except ValueError:
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        archive_workspace_artifact_snapshot(workspace, normalized_relative, next_content=content)
        target.write_text(content, encoding="utf-8")
        return normalized_relative
    except OSError:
        logger.warning("Failed to materialize project asset at %s", normalized_relative, exc_info=True)
        return None


def _save_chat_message_in_session(
    db: Session,
    *,
    chatroom_id: int,
    agent_id: int | None,
    content: str,
    message_type: str,
    metadata: dict[str, Any] | None = None,
) -> Message:
    message = Message(
        chatroom_id=chatroom_id,
        agent_id=agent_id,
        content=content,
        message_type=message_type,
        metadata_json=json.dumps(metadata or {}, ensure_ascii=False),
    )
    db.add(message)
    db.flush()
    db.refresh(message)
    return message


async def _report_delegated_child_result_to_parent(
    db: Session,
    child_task_run: TaskRun,
    *,
    parent_context: dict[str, Any],
    tool_result: Any,
    arguments: str,
    tracked_process: dict[str, Any],
) -> bool:
    parent_task_run = parent_context.get("parent_task_run")
    if parent_task_run is None:
        return False
    parent_status = str(getattr(parent_task_run, "status", "") or "").strip().lower()
    if parent_status == "cancelled":
        return False
    token = str(tracked_process.get("token") or "").strip() or None
    if _task_run_has_delegated_result_report(child_task_run, token=token):
        return True

    required_outputs = parent_context.get("required_outputs") if isinstance(parent_context.get("required_outputs"), list) else []
    test_report = (
        _build_required_test_report(
            child_task_run=child_task_run,
            parent_context=parent_context,
            tool_result=tool_result,
            arguments=arguments,
        )
        if "test_report" in required_outputs
        or str(parent_context.get("target_agent_name") or child_task_run.target_agent_name or "").strip().lower() == "tester"
        else None
    )
    if test_report is not None:
        runner_result = test_report.get("runner_result") if isinstance(test_report.get("runner_result"), dict) else {}
        if not is_valid_test_runner_result(runner_result):
            append_task_event(
                db,
                child_task_run,
                EventType.TEST_RUNNER_RESULT_CLASSIFIED,
                agent_name=parent_context.get("target_agent_name") or child_task_run.target_agent_name,
                summary="Tester classified the tool result as a runner issue, not a valid test result.",
                payload={
                    "task_id": parent_context.get("task_id"),
                    "task_title": parent_context.get("task_title") or child_task_run.title,
                    "runner_result": runner_result,
                },
            )
            return False
        summary, report_content = _delegated_test_report_context(
            child_task_run=child_task_run,
            parent_context=parent_context,
            test_report=test_report,
        )
        test_report_asset = _persist_test_report_asset(
            db,
            child_task_run=child_task_run,
            parent_context=parent_context,
            test_report=test_report,
            report_content=report_content,
        )
    else:
        test_report_asset = None
        parsed = _parse_run_shell_arguments(arguments)
        test_report = {
            "kind": "delegated_tool_result",
            "version": 1,
            "task_title": parent_context.get("task_title") or child_task_run.title,
            "command": parsed.get("command"),
            "cwd": parsed.get("cwd"),
            "status": getattr(tool_result, "status", None),
            "success": getattr(tool_result, "success", None),
            "result_preview": _compact_runtime_text(getattr(tool_result, "result", ""), limit=3000),
        }
        parent_agent = str(parent_context.get("delegator") or "owner").strip()
        child_agent = str(parent_context.get("target_agent_name") or child_task_run.target_agent_name or "Agent").strip()
        summary = (
            f"{child_agent} reported delegated task '{test_report['task_title']}' result to {parent_agent}: "
            f"{test_report.get('status') or 'unknown'}."
        )
        report_lines = [
            f"@{parent_agent} Delegated task result from {child_agent}.",
            "",
            f"Task: {test_report['task_title']}",
            f"Status: {test_report.get('status') or 'unknown'}",
            f"Success: {str(bool(test_report.get('success'))).lower()}",
        ]
        if test_report.get("command"):
            report_lines.append(f"Command: {test_report.get('command')}")
        if test_report.get("cwd"):
            report_lines.append(f"Working directory: {test_report.get('cwd')}")
        report_lines.extend(["", "Result:", test_report["result_preview"] or "(no output)", "", f"{parent_agent} owns the next-step decision."])
        report_content = "\n".join(report_lines)
    parsed_arguments = _parse_run_shell_arguments(arguments)
    summary_for_payload = summary
    result_payload_preview = _compact_runtime_text(getattr(tool_result, "result", ""), limit=3000)
    child_agent_name = str(parent_context.get("target_agent_name") or child_task_run.target_agent_name or "").strip()
    child_agent = _find_db_agent_by_type(db, child_agent_name)
    parent_agent_name = str(parent_context.get("delegator") or parent_task_run.target_agent_name or "").strip()
    metadata = {
        "client_turn_id": getattr(parent_task_run, "client_turn_id", None),
        "delegated_task_result": {
            "task_id": parent_context.get("task_id"),
            "task_title": parent_context.get("task_title") or child_task_run.title,
            "parent_task_run_id": getattr(parent_task_run, "id", None),
            "child_task_run_id": getattr(child_task_run, "id", None),
            "child_client_turn_id": getattr(child_task_run, "client_turn_id", None),
            "from_agent": child_agent_name or child_task_run.target_agent_name,
            "to_agent": parent_context.get("delegator") or parent_task_run.target_agent_name,
            "tool_name": getattr(tool_result, "tool_name", None),
            "tool_status": getattr(tool_result, "status", None),
            "tool_success": getattr(tool_result, "success", None),
            "tracked_process": tracked_process,
            "required_outputs": required_outputs,
            "produced_outputs": ["test_report"] if test_report.get("kind") == "test_report" else [],
            "artifact_asset_id": getattr(test_report_asset, "id", None),
        },
        "test_report": test_report if test_report.get("kind") == "test_report" else None,
    }
    if test_report_asset is not None:
        metadata["test_report_artifact"] = {
            "asset_id": test_report_asset.id,
            "asset_type": test_report_asset.asset_type,
            "title": test_report_asset.title,
            "storage_path": test_report_asset.storage_path,
        }
    if metadata["test_report"] is None:
        metadata.pop("test_report", None)
    saved_message = _save_chat_message_in_session(
        db,
        chatroom_id=child_task_run.chatroom_id,
        agent_id=getattr(child_agent, "id", None),
        content=report_content,
        message_type="text",
        metadata=metadata,
    )
    db.commit()
    await publish_saved_chat_message(
        db,
        child_task_run.chatroom_id,
        message_id=saved_message.id,
        content=report_content,
        agent_name=child_agent_name or child_task_run.target_agent_name,
        message_type=saved_message.message_type,
        created_at=saved_message.created_at,
        metadata=metadata,
    )
    if parent_agent_name:
        await _publish_agent_handoff_card(
            chatroom_id=child_task_run.chatroom_id,
            from_agent=child_agent_name or child_task_run.target_agent_name or "agent",
            to_agent=parent_agent_name,
            content=report_content,
            client_turn_id=getattr(parent_task_run, "client_turn_id", None) or getattr(child_task_run, "client_turn_id", None) or "",
            extra_metadata={
                "handoff_kind": "delegated_task_result",
                "message_id": getattr(saved_message, "id", None),
                "parent_task_run_id": getattr(parent_task_run, "id", None),
                "child_task_run_id": getattr(child_task_run, "id", None),
                "required_outputs": required_outputs,
            },
        )
    event_payload = {
        "task_id": parent_context.get("task_id"),
        "task_title": parent_context.get("task_title") or child_task_run.title,
        "parent_task_run_id": getattr(parent_task_run, "id", None),
        "child_task_run_id": getattr(child_task_run, "id", None),
        "child_client_turn_id": getattr(child_task_run, "client_turn_id", None),
        "message_id": getattr(saved_message, "id", None),
        "tool_name": getattr(tool_result, "tool_name", None),
        "tool_status": getattr(tool_result, "status", None),
        "tool_success": getattr(tool_result, "success", None),
        "tool_command": parsed_arguments.get("command"),
        "tool_cwd": parsed_arguments.get("cwd"),
        "tool_result_preview": result_payload_preview,
        "tracked_process": tracked_process,
        "required_outputs": required_outputs,
        "produced_outputs": ["test_report"] if test_report.get("kind") == "test_report" else [],
        "test_report": test_report if test_report.get("kind") == "test_report" else None,
    }
    if event_payload["test_report"] is None:
        event_payload.pop("test_report", None)
    if test_report.get("kind") == "test_report":
        append_task_event(
            db,
            child_task_run,
            EventType.TEST_REPORT_PRODUCED,
            agent_name=child_agent_name or child_task_run.target_agent_name,
            message_id=getattr(saved_message, "id", None),
            summary=f"{child_agent_name or child_task_run.target_agent_name} produced required test_report.",
            payload={
                **event_payload,
                "output_kind": "test_report",
            },
        )
    append_task_event(
        db,
        child_task_run,
        EventType.DELEGATED_TASK_RESULT_REPORTED,
        agent_name=child_agent_name or child_task_run.target_agent_name,
        message_id=getattr(saved_message, "id", None),
        summary=summary_for_payload,
        payload=event_payload,
    )
    append_task_event(
        db,
        parent_task_run,
        EventType.DELEGATED_TASK_RESULT_REPORTED,
        agent_name=child_agent_name or child_task_run.target_agent_name,
        message_id=getattr(saved_message, "id", None),
        summary=summary_for_payload,
        payload=event_payload,
    )
    complete_task_run(db, child_task_run, status="completed", summary=summary_for_payload)
    _reopen_task_run_for_followup(db, parent_task_run)
    await trigger_agent_response(
        child_task_run.chatroom_id,
        "",
        getattr(parent_task_run, "client_turn_id", None),
        task_run_id=getattr(parent_task_run, "id", None),
        extra_context=(
            f"A new agent handoff message from {child_agent_name or child_task_run.target_agent_name or 'agent'} "
            f"to {parent_agent_name or 'the parent owner'} was just saved in chat history. "
            "Read that message and decide the next step."
        ),
        checkpoint_snapshot=build_task_run_checkpoint_snapshot(parent_task_run),
    )
    return True


async def _continue_agent_after_tracked_run_shell_async(task_run_id: int, next_card: Dict[str, Any]) -> None:
    db = SessionLocal()
    try:
        task_run = get_task_run(db, task_run_id)
        if task_run is None:
            return
        if str(getattr(task_run, "status", "") or "").strip().lower() != "running":
            return
        tracked_process = next_card.get("tracked_process") if isinstance(next_card.get("tracked_process"), dict) else {}
        token = str(tracked_process.get("token") or "").strip() or None
        tool_call_id = str(next_card.get("tool_call_id") or tracked_process.get("tool_call_id") or "").strip() or None
        if _task_run_has_tracked_shell_followup(task_run, token=token, tool_call_id=tool_call_id):
            return
        if _task_run_has_terminal_tool_round_result(task_run, token=token, tool_call_id=tool_call_id):
            return

        arguments = str(next_card.get("arguments") or "{}")
        try:
            turn = max(1, int(next_card.get("turn") or tracked_process.get("turn") or 1))
        except (TypeError, ValueError):
            turn = 1
        result_payload = {
            "__catown_tool_result__": True,
            "tool_name": "run_shell",
            "success": bool(next_card.get("success")),
            "status": str(next_card.get("status") or ("succeeded" if next_card.get("success") else "failed")),
            "blocked": bool(next_card.get("blocked")),
            "blocked_kind": next_card.get("blocked_kind"),
            "blocked_reason": next_card.get("blocked_reason"),
            "result": str(next_card.get("result") or "").strip() or "Tracked run_shell finished without output.",
        }
        tool_result = build_tool_result_record(
            tool_call_id=next_card.get("tool_call_id") or tracked_process.get("tool_call_id"),
            tool_name="run_shell",
            arguments=arguments,
            result=result_payload,
            success=bool(next_card.get("success")),
            max_result_chars=50000,
        )
        record_runner_tool_round(
            db,
            task_run,
            agent_name=(task_run.target_agent_name or next_card.get("agent") or "").strip() or "agent",
            turn=turn,
            tool_names=["run_shell"],
            tool_results=[tool_result],
            summary="Tracked run_shell completed; returning result to agent for analysis.",
            payload={
                "source": "tracked_run_shell_reconcile",
                "tracked_process": tracked_process,
            },
        )
        parent_context = _find_delegated_parent_context(db, task_run)
        if parent_context is not None:
            reported = await _report_delegated_child_result_to_parent(
                db,
                task_run,
                parent_context=parent_context,
                tool_result=tool_result,
                arguments=arguments,
                tracked_process=tracked_process,
            )
            if reported:
                return
        append_task_event(
            db,
            task_run,
            EventType.TRACKED_RUN_SHELL_FOLLOWUP_QUEUED,
            agent_name=(task_run.target_agent_name or next_card.get("agent") or "").strip() or "agent",
            summary="Queued agent follow-up after tracked run_shell completed.",
            payload={
                "task_run_id": task_run.id,
                "tool_name": "run_shell",
                "tool_call_id": tool_result.tool_call_id,
                "tool_status": tool_result.status,
                "tool_success": tool_result.success,
                "tracked_process": tracked_process,
            },
        )
        replay_result = SimpleNamespace(
            tool_name=tool_result.tool_name,
            tool_call_id=tool_result.tool_call_id,
            result=tool_result.result,
            success=tool_result.success,
            status=tool_result.status,
            blocked=tool_result.blocked,
            blocked_kind=tool_result.blocked_kind,
            blocked_reason=tool_result.blocked_reason,
        )
        fake_item = SimpleNamespace(
            id=None,
            task_run_id=task_run.id,
            chatroom_id=task_run.chatroom_id,
            project_id=task_run.project_id,
            agent_name=task_run.target_agent_name,
            target_name="run_shell",
            target_kind="tool",
        )
        followup_context = build_tool_replay_followup_context(fake_item, replay_result, result_preview_limit=2000)
        await trigger_agent_response(
            task_run.chatroom_id,
            task_run.user_request or "",
            task_run.client_turn_id,
            task_run_id=task_run.id,
            extra_context=followup_context,
            checkpoint_snapshot=build_task_run_checkpoint_snapshot(task_run),
        )
    except Exception as exc:
        logger.exception("Tracked run_shell agent follow-up failed for task_run_id=%s: %s", task_run_id, exc)
        task_run = get_task_run(db, task_run_id)
        append_task_event(
            db,
            task_run,
            EventType.TRACKED_RUN_SHELL_FOLLOWUP_FAILED,
            agent_name=getattr(task_run, "target_agent_name", None),
            summary="Agent follow-up failed after tracked run_shell completed.",
            payload={"error": str(exc), "task_run_id": task_run_id},
        )
        # P0-4: Terminalize TaskRun so it doesn't hang in "running".
        if task_run and (task_run.status or "").strip().lower() == "running":
            terminalize_task_run(
                db, task_run,
                status="failed",
                error=exc,
                summary=f"Followup after run_shell failed: {exc}",
                agent_name=getattr(task_run, "target_agent_name", None),
            )
    finally:
        db.close()


def _spawn_tracked_run_shell_followup(task_run_id: int, next_card: Dict[str, Any]) -> None:
    def _runner() -> None:
        try:
            asyncio.run(_continue_agent_after_tracked_run_shell_async(task_run_id, next_card))
        except Exception as exc:
            logger.exception("Tracked run_shell follow-up worker crashed for task_run_id=%s: %s", task_run_id, exc)

    thread = threading.Thread(
        target=_runner,
        name=f"tracked-run-shell-followup-{task_run_id}",
        daemon=True,
    )
    thread.start()


def reconcile_tracked_run_shell_runtime_cards(db: Session, chatroom_id: int | None = None, *, limit: int = 500) -> int:
    query = db.query(Message).filter(Message.message_type == "runtime_card")
    if chatroom_id is not None:
        query = query.filter(Message.chatroom_id == chatroom_id)
    rows = query.order_by(Message.created_at.desc(), Message.id.desc()).limit(max(1, limit)).all()

    updated = 0
    for row in rows:
        metadata = _load_jsonish_payload(row.metadata_json)
        card = metadata.get("card")
        if not isinstance(card, dict) or not _runtime_card_is_running_tracked_run_shell(card):
            continue
        record = load_tracked_run_shell_handle(card.get("tracked_process"))
        if record is None:
            continue
        if not tracked_run_shell_has_exit(record) and tracked_run_shell_is_active(record):
            continue
        if not tracked_run_shell_has_exit(record) and not tracked_run_shell_is_active(record):
            continue
        next_card = _tracked_run_shell_final_card_payload(card, record)
        if next_card is None:
            continue
        metadata["card"] = next_card
        row.metadata_json = json.dumps(metadata, ensure_ascii=False)
        db.add(row)
        updated += 1

        task_run_id = next_card.get("run_id") or record.get("task_run_id")
        task_run = get_task_run(db, task_run_id)
        if _append_tracked_run_shell_completed_event(db, task_run, next_card):
            _spawn_tracked_run_shell_followup(task_run.id, dict(next_card))
    if updated:
        db.commit()
    return updated


async def _store_recovered_run_shell_final_card(
    task_run: TaskRun,
    *,
    result: dict[str, Any],
    tracked_handle: dict[str, Any],
    arguments: str,
    turn: int,
) -> None:
    if not isinstance(getattr(task_run, "chatroom_id", None), int):
        return
    await store_runtime_card(
        task_run.chatroom_id,
        {
            "type": "tool_call",
            "source": "recovery",
            "agent": (task_run.target_agent_name or "").strip() or "agent",
            "tool": "run_shell",
            "arguments": arguments,
            "success": bool(result.get("success")),
            "status": str(result.get("status") or ("succeeded" if result.get("success") else "failed")),
            "blocked": bool(result.get("blocked")),
            "blocked_kind": result.get("blocked_kind"),
            "blocked_reason": result.get("blocked_reason"),
            "result": str(result.get("result") or "").strip() or "Recovered run_shell finished without output.",
            "pid": tracked_handle.get("pid"),
            "tracked_process": {
                "token": tracked_handle.get("token"),
                "pid": tracked_handle.get("pid"),
                "worker_pid": tracked_handle.get("worker_pid"),
                "pgid": tracked_handle.get("pgid"),
                "status": tracked_handle.get("status"),
                "finished_at": tracked_handle.get("finished_at"),
                "task_run_id": tracked_handle.get("task_run_id"),
                "client_turn_id": tracked_handle.get("client_turn_id"),
                "tool_call_id": tracked_handle.get("tool_call_id"),
            },
            "tool_call_id": tracked_handle.get("tool_call_id"),
            "client_turn_id": task_run.client_turn_id,
            "run_id": task_run.id,
            "turn": turn,
        },
    )


async def _recover_orphaned_single_agent_run_shell_task_run(
    db: Session,
    task_run: TaskRun,
    *,
    trigger: str,
) -> bool:
    if str(task_run.status or "").strip().lower() != "running":
        return False

    tracked_handle, source_payload = _find_tracked_run_shell_for_task_run(db, task_run)
    if tracked_handle is None:
        return False
    if tracked_run_shell_has_exit(tracked_handle) or tracked_run_shell_is_active(tracked_handle):
        return False

    result = build_tracked_run_shell_result(tracked_handle, max_chars=50000)
    arguments = json.dumps(
        {
            "command": tracked_handle.get("command") or "",
            "cwd": tracked_handle.get("cwd") or ".",
            "timeout_seconds": tracked_handle.get("timeout_seconds"),
        },
        ensure_ascii=False,
    )
    turn = max(1, int(tracked_handle.get("turn") or 1))
    tool_result = build_tool_result_record(
        tool_call_id=tracked_handle.get("tool_call_id"),
        tool_name="run_shell",
        arguments=arguments,
        result=result,
        success=False,
    )
    record_runner_tool_round(
        db,
        task_run,
        agent_name=(task_run.target_agent_name or tracked_handle.get("agent_name") or "").strip() or "agent",
        turn=turn,
        tool_names=["run_shell"],
        tool_results=[tool_result],
        summary="Recovered interrupted run_shell result after backend restart.",
        payload={
            "recovery": True,
            "recovery_kind": "orphaned_run_shell_tracked_process",
            "tracked_process": {
                "token": tracked_handle.get("token"),
                "pid": tracked_handle.get("pid"),
                "worker_pid": tracked_handle.get("worker_pid"),
                "status": tracked_handle.get("status"),
                "has_exit": tracked_run_shell_has_exit(tracked_handle),
                "is_active": tracked_run_shell_is_active(tracked_handle),
            },
            **source_payload,
        },
    )
    summary = str(tool_result.result or "").strip() or "Recovered run_shell stopped before completion."
    complete_task_run(db, task_run, status="failed", summary=summary)
    append_task_event(
        db,
        task_run,
        EventType.TASK_RUN_RECOVERY_COMPLETED,
        agent_name=task_run.target_agent_name,
        summary="Recovered interrupted run_shell state after backend restart.",
        payload={
            "task_run_id": task_run.id,
            "trigger": trigger,
            "recovery_kind": "orphaned_run_shell_tracked_process",
            **source_payload,
        },
    )
    await _store_recovered_run_shell_final_card(
        task_run,
        result=result,
        tracked_handle=tracked_handle,
        arguments=arguments,
        turn=turn,
    )
    return True


async def _recover_tracked_single_agent_run_shell_task_run(
    db: Session,
    task_run: TaskRun,
    *,
    trigger: str,
) -> bool:
    if str(task_run.status or "").strip().lower() != "running":
        return False
    tracked_handle, source_payload = _find_tracked_run_shell_for_task_run(db, task_run)
    if tracked_handle is None:
        return False
    if tracked_run_shell_is_active(tracked_handle) and not tracked_run_shell_has_exit(tracked_handle):
        return False
    token = str(tracked_handle.get("token") or "").strip() or None
    tool_call_id = str(tracked_handle.get("tool_call_id") or "").strip() or None
    if _task_run_has_tracked_shell_followup(
        task_run,
        token=token,
        tool_call_id=tool_call_id,
        include_completed_marker=True,
    ):
        return False

    arguments = json.dumps(
        {
            "command": tracked_handle.get("command") or "",
            "cwd": tracked_handle.get("cwd") or ".",
            "timeout_seconds": tracked_handle.get("timeout_seconds"),
        },
        ensure_ascii=False,
    )
    turn = max(1, int(tracked_handle.get("turn") or 1))

    append_task_event(
        db,
        task_run,
        EventType.TASK_RUN_RECOVERY_STARTED,
        agent_name=task_run.target_agent_name,
        summary="Reattached to tracked run_shell process after backend restart.",
        payload={
            "task_run_id": task_run.id,
            "trigger": trigger,
            "recovery_kind": "run_shell_tracked_process",
            "tracked_process": tracked_handle,
            "checkpoint_snapshot": build_task_run_checkpoint_snapshot(task_run),
            **source_payload,
        },
    )

    if tracked_handle.get("finished_at"):
        result = build_tracked_run_shell_result(tracked_handle, max_chars=50000)
    else:
        result = await wait_for_tracked_run_shell(
            tracked_handle,
            timeout_seconds=None,
            progress_interval_seconds=2.0,
            tail_chars=4000,
            result_chars=50000,
        )

    tool_result = build_tool_result_record(
        tool_call_id=tracked_handle.get("tool_call_id"),
        tool_name="run_shell",
        arguments=arguments,
        result=result,
        success=bool(result.get("success")) if isinstance(result, dict) and result.get("__catown_tool_result__") is True else False,
    )
    record_runner_tool_round(
        db,
        task_run,
        agent_name=(task_run.target_agent_name or "").strip() or "agent",
        turn=turn,
        tool_names=["run_shell"],
        tool_results=[tool_result],
        summary="Recovered tracked run_shell result after backend restart.",
        payload={
            "recovery": True,
            "recovery_kind": "run_shell_tracked_process",
            "tracked_process": tracked_handle,
            **source_payload,
        },
    )

    recovery_replay_result = SimpleNamespace(
        tool_name=tool_result.tool_name,
        tool_call_id=tool_result.tool_call_id,
        result=tool_result.result,
        success=tool_result.success,
        status=tool_result.status,
        blocked=tool_result.blocked,
        blocked_kind=tool_result.blocked_kind,
        blocked_reason=tool_result.blocked_reason,
    )
    fake_item = SimpleNamespace(
        id=None,
        task_run_id=task_run.id,
        chatroom_id=task_run.chatroom_id,
        project_id=task_run.project_id,
        agent_name=task_run.target_agent_name,
        target_name="run_shell",
        target_kind="tool",
    )
    followup_payload = await _continue_runtime_after_approved_tool_replay(
        db,
        fake_item,
        {},
        recovery_replay_result,
    )
    append_task_event(
        db,
        get_task_run(db, task_run.id),
        EventType.TASK_RUN_RECOVERY_COMPLETED,
        agent_name=task_run.target_agent_name,
        summary="Recovered tracked run_shell result after backend restart.",
        payload={
            "task_run_id": task_run.id,
            "trigger": trigger,
            "recovery_kind": "run_shell_tracked_process",
            "tracked_process": tracked_handle,
            "followup_status": followup_payload.get("followup_status"),
            "followup_reason": followup_payload.get("followup_reason"),
            **source_payload,
        },
    )
    return True


async def _resume_interrupted_single_agent_task_run(
    db: Session,
    task_run: TaskRun,
    *,
    trigger: str,
) -> bool:
    if str(task_run.status or "").strip().lower() != "running":
        return False
    if not _task_run_has_interrupted_approval_followup(task_run):
        return False

    checkpoint_snapshot = build_task_run_checkpoint_snapshot(task_run)
    latest_event = _latest_task_run_event(task_run)
    recovery_continuation_state = describe_checkpoint_continuation_state(checkpoint_snapshot)
    append_task_event(
        db,
        task_run,
        EventType.TASK_RUN_RECOVERY_STARTED,
        agent_name=task_run.target_agent_name,
        summary="Resuming interrupted single-agent follow-up after backend restart.",
        payload={
            "task_run_id": task_run.id,
            "trigger": trigger,
            "recovery_kind": "single_agent_followup_interrupted",
            "checkpoint_snapshot": checkpoint_snapshot,
            "recovery_continuation_state": recovery_continuation_state,
            "latest_event_type": latest_event.event_type if latest_event is not None else None,
            "latest_event_at": latest_event.created_at.isoformat() if latest_event is not None and latest_event.created_at else None,
        },
    )
    result = await trigger_agent_response(
        task_run.chatroom_id,
        task_run.user_request or "",
        task_run.client_turn_id,
        task_run_id=task_run.id,
        checkpoint_snapshot=checkpoint_snapshot,
    )
    append_task_event(
        db,
        get_task_run(db, task_run.id),
        EventType.TASK_RUN_RECOVERY_COMPLETED,
        agent_name=task_run.target_agent_name,
        summary="Resumed interrupted single-agent follow-up after backend restart.",
        payload={
            "task_run_id": task_run.id,
            "trigger": trigger,
            "recovery_kind": "single_agent_followup_interrupted",
            "recovery_continuation_state": recovery_continuation_state,
            "followup_status": (
                "completed"
                if result.get("completed")
                else "awaiting_tool_approval"
                if result.get("awaiting_tool_approval")
                else "awaiting_background_tool"
                if result.get("awaiting_background_tool")
                else str(result.get("outcome") or "incomplete")
            ),
            "result": result,
        },
    )
    return True


def _recover_orchestration_agent_names(task_run: TaskRun) -> List[str]:
    schedule_event = next((event for event in task_run.events if event.event_type == EventType.SCHEDULER_PLAN_CREATED), None)
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

    orchestration_event = next((event for event in task_run.events if event.event_type == EventType.ORCHESTRATION_STARTED), None)
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
        if event.event_type == EventType.AGENT_TURN_COMPLETED and event.message_id
    ]
    if message_ids:
        for message in db.query(Message).filter(Message.id.in_(message_ids)).all():
            messages_by_id[message.id] = message

    for event in task_run.events:
        if event.event_type != EventType.AGENT_TURN_COMPLETED:
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
    ensure_runtime_chatroom_collaborators(
        agents=agents,
        chatroom_id=chatroom_id,
        agent_name_resolver=_agent_type,
    )

@lru_cache(maxsize=1)
def _build_orchestration_turn_runtime_profile():
    return build_orchestration_turn_runtime_profile(
        ensure_collaboration_context=_ensure_collaboration_context,
        prepare_chat_turn_runtime=prepare_chat_turn_runtime,
        assemble_chat_messages=assemble_runtime_chat_messages,
        build_context_compaction_callback=_build_context_compaction_callback,
        save_message=chatroom_manager.send_message,
        message_metadata=_message_metadata_with_turn,
        schedule_memory_extraction=lambda agent, request, response: schedule_agent_memory_extraction(
            extract_agent_memories,
            agent_id=agent.id,
            agent_type=_agent_type(agent),
            user_message=request,
            agent_response=response,
        ),
        build_llm_card_payload=_build_llm_card_payload,
        snapshot_messages=_snapshot_llm_messages,
        preview_tool_calls=_preview_tool_calls,
        format_prompt_messages=_format_json_block,
        tool_result_success=_tool_result_succeeded,
        max_tool_iterations=MAX_TOOL_ITERATIONS,
    )


@lru_cache(maxsize=1)
def _build_orchestration_session_runtime_profile():
    return build_orchestration_session_runtime_profile(
        turn_profile=_build_orchestration_turn_runtime_profile(),
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
        log_agent_type=_agent_type,
    )


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
    orchestration_session_profile = _build_orchestration_session_runtime_profile()
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
        await orchestration_session_profile.run_nonstream(
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
            build_step_context=lambda step, agent, agent_label: {"extra_context": extra_context},
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
        if hasattr(prepared_recovery, "reason"):
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
        orchestration_session_profile = _build_orchestration_session_runtime_profile()
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
        recovery_result = await orchestration_session_profile.run_recovery(
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
            describe_recovery_continuation_state=_describe_recovery_continuation_state,
            rebuild_recovery_state=_rebuild_orchestration_recovery_state,
            renew_lease=_before_recovery_step,
            recovery_owner=RECOVERY_INSTANCE_ID,
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
                event_type=EventType.TASK_RUN_RECOVERY_FAILED,
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
        reconcile_tracked_run_shell_runtime_cards(db, limit=1000)
        recoverable_run_kinds = RECOVERABLE_ORCHESTRATION_RUN_KINDS | INTERRUPTIBLE_SINGLE_AGENT_RUN_KINDS
        pending_runs = (
            db.query(TaskRun)
            .filter(
                TaskRun.status == "running",
                TaskRun.run_kind.in_(sorted(recoverable_run_kinds)),
            )
            .order_by(TaskRun.created_at.asc(), TaskRun.id.asc())
            .limit(max(1, limit))
            .all()
        )
        run_refs = [(run.id, str(run.run_kind or "").strip()) for run in pending_runs]
    finally:
        db.close()

    recovered = 0
    failed = 0
    skipped = 0
    interrupted = 0
    for run_id, run_kind in run_refs:
        try:
            if run_kind in RECOVERABLE_ORCHESTRATION_RUN_KINDS:
                result = await _resume_interrupted_orchestration_task_run(run_id, trigger="startup")
                if result.resumed:
                    recovered += 1
                elif result.reason in {"leased", "not_running", "not_recoverable", "not_found"}:
                    skipped += 1
                else:
                    failed += 1
            elif run_kind in INTERRUPTIBLE_SINGLE_AGENT_RUN_KINDS:
                db = SessionLocal()
                try:
                    task_run = db.query(TaskRun).filter(TaskRun.id == run_id).first()
                    if task_run is None or str(task_run.status or "").strip().lower() != "running":
                        skipped += 1
                    elif await _recover_orphaned_single_agent_run_shell_task_run(db, task_run, trigger="startup"):
                        recovered += 1
                    elif await _recover_tracked_single_agent_run_shell_task_run(db, task_run, trigger="startup"):
                        recovered += 1
                    elif await _resume_interrupted_single_agent_task_run(db, task_run, trigger="startup"):
                        recovered += 1
                    elif _task_run_has_tracked_shell_followup(task_run, include_completed_marker=True):
                        skipped += 1
                    elif _terminalize_interrupted_single_agent_task_run(db, task_run, trigger="startup"):
                        interrupted += 1
                    else:
                        skipped += 1
                finally:
                    db.close()
            else:
                skipped += 1
        except Exception:
            failed += 1
    return {
        "detected": len(run_refs),
        "recovered": recovered,
        "failed": failed,
        "skipped": skipped,
        "interrupted": interrupted,
    }


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

    orchestration_session_profile = _build_orchestration_session_runtime_profile()

    async for runtime_event in orchestration_session_profile.iter_stream_session_events(
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
        set_active_agent=set_active_agent,
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


class ProjectBrowserFileInfo(BaseModel):
    path: str
    name: str
    kind: str
    size: Optional[int] = None
    mtime: Optional[float] = None


class ProjectBrowserArtifactInfo(BaseModel):
    path: str
    name: str
    type: str
    agent_name: Optional[str] = None
    size: Optional[int] = None
    mtime: Optional[float] = None


class ProjectBrowserInfo(BaseModel):
    workspace_path: str
    files: List[ProjectBrowserFileInfo]
    artifacts: List[ProjectBrowserArtifactInfo]
    truncated: bool = False


class ProjectBrowserWatchEvent(BaseModel):
    type: str
    workspace_path: str
    changed: bool = False
    reason: Optional[str] = None
    changed_paths: List[str] = Field(default_factory=list)
    added_paths: List[str] = Field(default_factory=list)
    updated_paths: List[str] = Field(default_factory=list)
    files: List[ProjectBrowserFileInfo] = Field(default_factory=list)
    artifacts: List[ProjectBrowserArtifactInfo] = Field(default_factory=list)
    removed_paths: List[str] = Field(default_factory=list)
    truncated: bool = False
    snapshot_id: Optional[str] = None


class ChatProcessNodeInfo(BaseModel):
    id: str
    label: str
    kind: str
    detail: str = ""
    agent_name: Optional[str] = None
    status: Optional[str] = None
    parent_id: Optional[str] = None
    timestamp: Optional[str] = None
    pid: Optional[int] = None
    output: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    children: List["ChatProcessNodeInfo"] = Field(default_factory=list)


ChatProcessNodeInfo.model_rebuild()


class ProjectFileReadInfo(BaseModel):
    path: str
    name: str
    size: int
    mtime: float
    content: str = ""
    encoding: str = "utf-8"
    truncated: bool = False
    binary: bool = False
    preview_limit: int


class ProjectFileWriteRequest(BaseModel):
    path: str
    content: str
    expected_mtime: Optional[float] = None


class ProjectUpdate(BaseModel):
    name: str


class ProjectReorderRequest(BaseModel):
    project_ids: List[int]


class MessageRequest(BaseModel):
    content: str
    client_turn_id: Optional[str] = None
    attachments: Optional[List[Dict[str, Any]]] = None


class MessageAttachment(BaseModel):
    """Attachment metadata returned after upload."""
    file_path: str
    file_name: str
    file_size: int
    mime_type: Optional[str] = None
    upload_time: str


class MessageResponse(BaseModel):
    id: int
    content: str
    agent_name: Optional[str]
    message_type: str
    created_at: str
    client_turn_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    runtime_summary: Optional[Dict[str, Any]] = None


class ApprovalQueueDecisionRequest(BaseModel):
    note: Optional[str] = None
    rollback_to: Optional[str] = None
    resolved_by: Optional[str] = "user"
    remember: bool = False
    remember_scope: Optional[str] = None
    remember_matcher: Optional[str] = None

    @field_validator("remember_scope")
    @classmethod
    def _validate_remember_scope(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = str(value).strip().lower()
        if not normalized:
            return None
        if normalized not in {AUTH_SCOPE_PROJECT, AUTH_SCOPE_CHATROOM, AUTH_SCOPE_GLOBAL}:
            raise ValueError("remember_scope must be one of: project, chatroom, global")
        return normalized

    @field_validator("remember_matcher")
    @classmethod
    def _validate_remember_matcher(cls, value: Optional[str]) -> Optional[str]:
        resolved = _normalize_authorization_matcher_config(value)
        if resolved is None and (value is None or not str(value).strip()):
            return None
        if resolved is None:
            raise ValueError("remember_matcher must be one of: command_fingerprint, shell_bin, tool_target, all_tools")
        return resolved


class TaskRunCancelRequest(BaseModel):
    note: Optional[str] = None
    cancelled_by: Optional[str] = "user"


def _message_metadata_with_turn(client_turn_id: Optional[str], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    metadata = dict(extra or {})
    if client_turn_id:
        metadata["client_turn_id"] = client_turn_id
    return metadata


async def _publish_agent_handoff_card(
    *,
    chatroom_id: int,
    from_agent: str,
    to_agent: str,
    content: str,
    client_turn_id: str,
    extra_metadata: Optional[Dict[str, Any]] = None,
) -> None:
    await store_runtime_card(
        chatroom_id,
        {
            "type": "agent_message",
            "source": "chatroom",
            "from_agent": from_agent,
            "to_agent": to_agent,
            "content": content,
            "client_turn_id": client_turn_id,
            **(extra_metadata or {}),
        },
    )


def _build_assistant_auto_handoff_callback(
    *,
    chatroom_id: int,
    source_agent_name: str,
) -> Callable[[Any, str, Dict[str, Any]], Awaitable[Any]]:
    async def _callback(saved_message: Any, resolved_content: str, metadata: Dict[str, Any]) -> Any:
        return await maybe_schedule_assistant_handoff(
            chatroom_id=chatroom_id,
            content=resolved_content,
            agent_name=source_agent_name,
            metadata=metadata,
            saved_message_id=getattr(saved_message, "id", None),
            publish_agent_message_card=_publish_agent_handoff_card,
            trigger_agent_response=trigger_agent_response,
        )

    return _callback


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


PROJECT_BROWSER_IGNORED_DIRS = {
    ".cache",
    ".catown",
    ".git",
    ".hg",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".turbo",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "vendor",
    "venv",
}
PROJECT_BROWSER_MAX_FILES = 800
PROJECT_BROWSER_MAX_DIRS = 400
PROJECT_BROWSER_BATCH_SIZE = 50
PROJECT_FILE_READ_MAX_BYTES = 512 * 1024
PROJECT_FILE_WRITE_MAX_CHARS = 1024 * 1024
CHAT_PROCESSES_LIMIT = 12
CHAT_PROCESS_SHELL_TAIL_MAX_CHARS = 5000
CHAT_PROCESS_SHELL_TAIL_MAX_LINES = 28
CHAT_PROCESS_TASK_STALE_SECONDS = 120


def _project_browser_artifact_type(path: str) -> Optional[str]:
    return classify_workspace_artifact_path(path)


def _project_browser_file_records(workspace: Path, file_path: Path) -> tuple[ProjectBrowserFileInfo, Optional[ProjectBrowserArtifactInfo]] | None:
    try:
        if not file_path.is_file():
            return None
        stat = file_path.stat()
        relative_path = file_path.relative_to(workspace).as_posix()
    except (OSError, ValueError):
        return None

    file_info = ProjectBrowserFileInfo(
        path=relative_path,
        name=file_path.name,
        kind="file",
        size=stat.st_size,
        mtime=stat.st_mtime,
    )
    artifact_type = _project_browser_artifact_type(relative_path)
    artifact_info = (
        ProjectBrowserArtifactInfo(
            path=relative_path,
            name=file_path.name,
            type=artifact_type,
            size=stat.st_size,
            mtime=stat.st_mtime,
        )
        if artifact_type
        else None
    )
    return file_info, artifact_info


def _iter_project_browser_batches(workspace_path: str, batch_size: int = PROJECT_BROWSER_BATCH_SIZE):
    workspace = Path(workspace_path).expanduser().resolve()
    if not workspace.exists() or not workspace.is_dir():
        raise HTTPException(status_code=404, detail="Workspace path not found")

    yield {
        "type": "start",
        "workspace_path": str(workspace),
        "files": [],
        "artifacts": [],
        "truncated": False,
    }

    file_batch: List[ProjectBrowserFileInfo] = []
    artifact_batch: List[ProjectBrowserArtifactInfo] = []
    truncated = False
    visited_dirs = 0
    emitted_files = 0

    for root, dirnames, filenames in os.walk(workspace):
        root_path = Path(root)
        visited_dirs += 1
        if visited_dirs > PROJECT_BROWSER_MAX_DIRS:
            truncated = True
            dirnames[:] = []
            break

        dirnames[:] = sorted(
            dirname for dirname in dirnames
            if dirname not in PROJECT_BROWSER_IGNORED_DIRS and not dirname.startswith(".catown-")
        )

        for filename in sorted(filenames):
            if filename.startswith(".") and filename not in {".env.example"}:
                continue
            file_path = root_path / filename
            records = _project_browser_file_records(workspace, file_path)
            if not records:
                continue

            file_info, artifact_info = records
            file_batch.append(file_info)
            if artifact_info:
                artifact_batch.append(artifact_info)
            emitted_files += 1

            if len(file_batch) >= batch_size:
                yield {
                    "type": "batch",
                    "workspace_path": str(workspace),
                    "files": [item.model_dump() for item in file_batch],
                    "artifacts": [item.model_dump() for item in artifact_batch],
                    "truncated": False,
                }
                file_batch = []
                artifact_batch = []

            if emitted_files >= PROJECT_BROWSER_MAX_FILES:
                truncated = True
                dirnames[:] = []
                break

        if truncated:
            break

    if file_batch or artifact_batch:
        yield {
            "type": "batch",
            "workspace_path": str(workspace),
            "files": [item.model_dump() for item in file_batch],
            "artifacts": [item.model_dump() for item in artifact_batch],
            "truncated": False,
        }

    yield {
        "type": "done",
        "workspace_path": str(workspace),
        "files": [],
        "artifacts": [],
        "truncated": truncated,
    }


def _stream_project_browser(workspace_path: str):
    for batch in _iter_project_browser_batches(workspace_path):
        yield json.dumps(batch, ensure_ascii=False) + "\n"


def _scan_project_browser(workspace_path: str) -> ProjectBrowserInfo:
    files: List[ProjectBrowserFileInfo] = []
    artifacts: List[ProjectBrowserArtifactInfo] = []
    workspace = ""
    truncated = False

    for batch in _iter_project_browser_batches(workspace_path, batch_size=PROJECT_BROWSER_MAX_FILES):
        workspace = batch.get("workspace_path") or workspace
        truncated = bool(batch.get("truncated") or truncated)
        files.extend(ProjectBrowserFileInfo(**item) for item in batch.get("files", []))
        artifacts.extend(ProjectBrowserArtifactInfo(**item) for item in batch.get("artifacts", []))

    files.sort(key=lambda item: item.path.lower())
    artifacts.sort(key=lambda item: (item.type.lower(), item.path.lower()))
    return ProjectBrowserInfo(
        workspace_path=workspace,
        files=files,
        artifacts=artifacts,
        truncated=truncated,
    )


def _project_browser_watch_snapshot(workspace_path: str) -> dict[str, Any]:
    workspace = Path(workspace_path).expanduser().resolve()
    if not workspace.exists() or not workspace.is_dir():
        raise HTTPException(status_code=404, detail="Workspace path not found")

    files: Dict[str, tuple[int, int]] = {}
    visited_dirs = 0
    emitted_files = 0
    truncated = False

    for root, dirnames, filenames in os.walk(workspace):
        root_path = Path(root)
        visited_dirs += 1
        if visited_dirs > PROJECT_BROWSER_MAX_DIRS:
            truncated = True
            dirnames[:] = []
            break

        dirnames[:] = sorted(
            dirname for dirname in dirnames
            if dirname not in PROJECT_BROWSER_IGNORED_DIRS and not dirname.startswith(".catown-")
        )

        for filename in sorted(filenames):
            if filename.startswith(".") and filename not in {".env.example"}:
                continue
            file_path = root_path / filename
            try:
                if not file_path.is_file():
                    continue
                stat = file_path.stat()
                relative_path = file_path.relative_to(workspace).as_posix()
            except (OSError, ValueError):
                continue
            files[relative_path] = (int(stat.st_mtime_ns), int(stat.st_size))
            emitted_files += 1
            if emitted_files >= PROJECT_BROWSER_MAX_FILES:
                truncated = True
                dirnames[:] = []
                break
        if truncated:
            break

    snapshot_hash = hashlib.sha1()
    for path, (mtime_ns, size) in sorted(files.items()):
        snapshot_hash.update(path.encode("utf-8", errors="ignore"))
        snapshot_hash.update(b"\0")
        snapshot_hash.update(str(mtime_ns).encode("ascii"))
        snapshot_hash.update(b"\0")
        snapshot_hash.update(str(size).encode("ascii"))
        snapshot_hash.update(b"\0")
    snapshot_hash.update(b"truncated:")
    snapshot_hash.update(b"1" if truncated else b"0")

    return {
        "workspace_path": str(workspace),
        "files": files,
        "truncated": truncated,
        "snapshot_id": snapshot_hash.hexdigest(),
    }


def _diff_project_browser_watch_snapshots(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    previous_files = previous.get("files", {})
    current_files = current.get("files", {})
    changed_paths = sorted(
        path
        for path in set(previous_files.keys()) | set(current_files.keys())
        if previous_files.get(path) != current_files.get(path)
    )
    added_paths = [path for path in changed_paths if path not in previous_files]
    updated_paths = [path for path in changed_paths if path in previous_files and path in current_files]
    removed_paths = [path for path in changed_paths if path not in current_files]
    changed = bool(changed_paths) or bool(previous.get("truncated")) != bool(current.get("truncated"))
    workspace = Path(current.get("workspace_path") or "").expanduser().resolve()
    file_updates: list[ProjectBrowserFileInfo] = []
    artifact_updates: list[ProjectBrowserArtifactInfo] = []

    for path in changed_paths:
        if path not in current_files:
            continue
        records = _project_browser_file_records(workspace, workspace / path)
        if not records:
            continue
        file_info, artifact_info = records
        file_updates.append(file_info)
        if artifact_info is not None:
            artifact_updates.append(artifact_info)

    return {
        "changed": changed,
        "changed_paths": changed_paths[:64],
        "added_paths": added_paths[:64],
        "updated_paths": updated_paths[:64],
        "files": [item.model_dump() for item in file_updates],
        "artifacts": [item.model_dump() for item in artifact_updates],
        "removed_paths": removed_paths[:64],
        "truncated": bool(current.get("truncated")),
        "snapshot_id": current.get("snapshot_id"),
    }


async def _stream_project_browser_watch_events(
    workspace_path: str,
    request: Request,
    poll_interval_seconds: float = 2.0,
    max_events: Optional[int] = None,
):
    interval = min(max(poll_interval_seconds, 0.75), 10.0)
    emitted_events = 0
    previous = _project_browser_watch_snapshot(workspace_path)
    yield json.dumps(
        ProjectBrowserWatchEvent(
            type="ready",
            workspace_path=previous["workspace_path"],
            changed=False,
            reason="initial_snapshot",
            changed_paths=[],
            added_paths=[],
            updated_paths=[],
            files=[],
            artifacts=[],
            removed_paths=[],
            truncated=bool(previous.get("truncated")),
            snapshot_id=previous.get("snapshot_id"),
        ).model_dump(),
        ensure_ascii=False,
    ) + "\n"
    emitted_events += 1
    if max_events is not None and emitted_events >= max_events:
        return

    while True:
        if runtime_is_shutting_down() or await request.is_disconnected():
            break
        await asyncio.sleep(interval)
        if runtime_is_shutting_down() or await request.is_disconnected():
            break
        current = _project_browser_watch_snapshot(workspace_path)
        diff = _diff_project_browser_watch_snapshots(previous, current)
        if diff["changed"]:
            yield json.dumps(
                ProjectBrowserWatchEvent(
                    type="refresh_needed",
                    workspace_path=current["workspace_path"],
                    changed=True,
                    reason="workspace_changed",
                    changed_paths=diff["changed_paths"],
                    added_paths=diff.get("added_paths", []),
                    updated_paths=diff.get("updated_paths", []),
                    files=[ProjectBrowserFileInfo(**item) for item in diff.get("files", [])],
                    artifacts=[ProjectBrowserArtifactInfo(**item) for item in diff.get("artifacts", [])],
                    removed_paths=diff.get("removed_paths", []),
                    truncated=diff["truncated"],
                    snapshot_id=diff["snapshot_id"],
                ).model_dump(),
                ensure_ascii=False,
            ) + "\n"
            emitted_events += 1
            previous = current
            if max_events is not None and emitted_events >= max_events:
                break


def _one_line_preview(value: Any, fallback: str, limit: int = 96) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    normalized = " ".join(text.split())
    return normalized if len(normalized) <= limit else f"{normalized[:limit].rstrip()}..."


def _parse_json_object(value: Any) -> Dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _read_shell_command_preview(arguments_text: Any) -> str:
    raw = arguments_text.strip() if isinstance(arguments_text, str) else ""
    if not raw:
        return ""

    parsed = _parse_json_object(raw)
    command = str(parsed.get("command") or "").strip()
    cwd = str(parsed.get("cwd") or "").strip()
    if command and cwd and cwd != ".":
        return f"{_one_line_preview(command, '', 160)} · cwd {_one_line_preview(cwd, '', 60)}"
    if command:
        return _one_line_preview(command, "", 180)
    return _one_line_preview(raw, "", 180)


def _trim_shell_output_tail(output: Any) -> str:
    normalized = str(output or "").replace("\r\n", "\n").rstrip()
    if not normalized:
        return ""
    char_clipped = normalized[-CHAT_PROCESS_SHELL_TAIL_MAX_CHARS:]
    lines = char_clipped.split("\n")
    line_clipped = len(lines) > CHAT_PROCESS_SHELL_TAIL_MAX_LINES
    tail = "\n".join(lines[-CHAT_PROCESS_SHELL_TAIL_MAX_LINES:]) if line_clipped else char_clipped
    clipped = len(normalized) > len(char_clipped) or line_clipped
    return f"... showing latest shell output\n{tail}" if clipped else tail


def _is_internal_tool_pause(card: Dict[str, Any]) -> bool:
    if card.get("type") != "tool_call":
        return False
    if not bool(card.get("blocked")):
        return False
    blocked_kind = str(card.get("blocked_kind") or "").strip().lower()
    status = str(card.get("status") or "").strip().lower()
    return blocked_kind == "approval" or status == "approval_blocked"


def _is_shell_process_card(card: Dict[str, Any]) -> bool:
    tool = str(card.get("tool") or "").strip().lower()
    return tool == "run_shell" and not _is_internal_tool_pause(card)


def _pid_is_alive(pid: Any) -> bool:
    try:
        parsed = int(pid)
    except (TypeError, ValueError):
        return False
    if parsed <= 0:
        return False
    try:
        os.kill(parsed, 0)
    except OSError:
        return False
    return True


def _running_shell_card_is_active(card: Dict[str, Any], observed_at: Any = None) -> bool:
    tracked = card.get("tracked_process")
    record = load_tracked_run_shell_handle(tracked) if isinstance(tracked, dict) else None
    if record is not None:
        if tracked_run_shell_has_exit(record):
            return False
        return tracked_run_shell_is_active(record)
    return _pid_is_alive(card.get("pid"))


def _shell_process_card_status(card: Dict[str, Any], observed_at: Any = None) -> str:
    status = str(card.get("status") or "").strip().lower()
    if status == "running" and _running_shell_card_is_active(card, observed_at):
        return "running"
    return "terminated"


def _running_shell_card_process_snapshot(card: Dict[str, Any]) -> tuple[Optional[int], str]:
    tracked = card.get("tracked_process")
    record = load_tracked_run_shell_handle(tracked) if isinstance(tracked, dict) else None
    if record is not None:
        pid = record.get("pid") if isinstance(record.get("pid"), int) else None
        output = read_tracked_run_shell_tail(record, max_chars=CHAT_PROCESS_SHELL_TAIL_MAX_CHARS)
        return pid, output
    return card.get("pid") if isinstance(card.get("pid"), int) else None, str(card.get("result") or "")


def _running_shell_process_entry_id(card: Dict[str, Any], row_id: Any) -> str:
    tracked = card.get("tracked_process")
    if isinstance(tracked, dict):
        token = str(tracked.get("token") or "").strip()
        if token:
            return f"shell:tracked:{token}"
    tool_call_id = str(card.get("tool_call_id") or "").strip()
    if tool_call_id:
        return f"shell:tool-call:{tool_call_id}"
    pid = card.get("pid")
    if pid is not None:
        return f"shell:pid:{pid}"
    return f"shell:card:{row_id}"


def _running_shell_process_parent_id(card: Dict[str, Any]) -> Optional[str]:
    tracked = card.get("tracked_process")
    if isinstance(tracked, dict):
        task_run_id = tracked.get("task_run_id")
        if task_run_id is not None:
            return f"task-run:{task_run_id}"
    run_id = card.get("run_id")
    if run_id is not None:
        return f"task-run:{run_id}"
    return None


def _should_render_inline_task_run(task_run: TaskRun) -> bool:
    pending_approval_count = len([
        item for item in getattr(task_run, "approval_queue_items", [])
        if str(getattr(item, "status", "") or "").lower() == "pending"
    ])
    if pending_approval_count > 0:
        return True
    client_turn_id = str(getattr(task_run, "client_turn_id", "") or "").strip().lower()
    if client_turn_id.startswith("delegate-"):
        return True
    run_kind = str(getattr(task_run, "run_kind", "") or "").strip().lower()
    return "pipeline" in run_kind or "orchestration" in run_kind


def _is_running_browser_task_run(task_run: TaskRun) -> bool:
    if str(getattr(task_run, "status", "") or "").strip().lower() != "running":
        return False
    if _should_render_inline_task_run(task_run):
        return False
    now = datetime.now()
    lease_expires_at = getattr(task_run, "recovery_lease_expires_at", None)
    if lease_expires_at is not None and lease_expires_at > now:
        return True
    updated_at = getattr(task_run, "updated_at", None) or getattr(task_run, "created_at", None)
    if updated_at is not None and (now - updated_at).total_seconds() > CHAT_PROCESS_TASK_STALE_SECONDS:
        return False
    checkpoint = build_task_run_checkpoint_snapshot(task_run)
    cursor = checkpoint.get("continuation_cursor")
    blocked_kind = str((cursor or {}).get("blocked_kind") or "").strip().lower() if isinstance(cursor, dict) else ""
    return blocked_kind != "approval"


def _dedupe_process_entries(entries: List[ChatProcessNodeInfo]) -> List[ChatProcessNodeInfo]:
    by_id: Dict[str, ChatProcessNodeInfo] = {}
    for entry in entries:
        by_id.setdefault(entry.id, entry)
    return list(by_id.values())


def _build_chat_process_entries(db: Session, chatroom_id: int, *, limit: int = CHAT_PROCESSES_LIMIT) -> List[ChatProcessNodeInfo]:
    entries: List[ChatProcessNodeInfo] = []

    card_rows = (
        db.query(Message)
        .filter(Message.chatroom_id == chatroom_id, Message.message_type == "runtime_card")
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(200)
        .all()
    )
    for row in card_rows:
        try:
            metadata = json.loads(row.metadata_json or "{}")
        except json.JSONDecodeError:
            metadata = {}
        card = metadata.get("card")
        if not isinstance(card, dict):
            continue
        card_payload = public_runtime_card_payload(dict(card))
        if not _is_shell_process_card(card_payload):
            continue
        created_at = getattr(row, "created_at", None)
        process_status = _shell_process_card_status(card_payload, created_at)
        command = _read_shell_command_preview(card_payload.get("arguments")) or str(card_payload.get("display_name") or "run_shell")
        pid, output = _running_shell_card_process_snapshot(card_payload)
        entries.append(ChatProcessNodeInfo(
            id=_running_shell_process_entry_id(card_payload, row.id),
            label=command,
            kind="command",
            detail=str(card_payload.get("summary") or _one_line_preview(output or card_payload.get("result"), "Shell process is running.", 140)),
            agent_name=str(card_payload.get("agent") or "").strip() or None,
            status=process_status,
            parent_id=_running_shell_process_parent_id(card_payload),
            timestamp=created_at.isoformat() if hasattr(created_at, "isoformat") else None,
            pid=pid,
            output=_trim_shell_output_tail(output or card_payload.get("result")) or None,
        ))

    task_runs = (
        db.query(TaskRun)
        .filter(TaskRun.chatroom_id == chatroom_id, TaskRun.status == "running")
        .order_by(TaskRun.updated_at.desc(), TaskRun.created_at.desc(), TaskRun.id.desc())
        .limit(100)
        .all()
    )
    for run in task_runs:
        if not _is_running_browser_task_run(run):
            continue
        timestamp = run.updated_at or run.created_at
        summary = serialize_task_run_summary(run)
        checkpoint = summary.get("checkpoint_snapshot") if isinstance(summary.get("checkpoint_snapshot"), dict) else {}
        active_subagent_handle = checkpoint.get("active_subagent_handle") if isinstance(checkpoint.get("active_subagent_handle"), dict) else None
        if active_subagent_handle is None:
            activity = build_task_activity_projection(run)
            active_subagent_handle = activity.get("active_subagent_handle")
        entries.append(ChatProcessNodeInfo(
            id=f"task-run:{run.id}",
            label=run.title,
            kind="task",
            detail=run.summary or run.user_request or f"{len(getattr(run, 'events', []) or [])} events",
            agent_name=(run.target_agent_name or "").strip() or None,
            status="running",
            timestamp=timestamp.isoformat() if hasattr(timestamp, "isoformat") else None,
        ))
        if isinstance(active_subagent_handle, dict):
            handle_step_id = str(active_subagent_handle.get("step_id") or "").strip()
            handle_agent = str(
                active_subagent_handle.get("agent_name")
                or active_subagent_handle.get("agent_type")
                or active_subagent_handle.get("requested_name")
                or "subagent"
            ).strip()
            dispatch_kind = str(active_subagent_handle.get("dispatch_kind") or "subagent").strip()
            control_state = str(active_subagent_handle.get("control_state") or active_subagent_handle.get("status") or "running").strip()
            response_preview = str(active_subagent_handle.get("response_preview") or "").strip()
            summary_text = str(active_subagent_handle.get("summary_text") or "").strip()
            entries.append(
                ChatProcessNodeInfo(
                    id=f"subagent:{handle_step_id or run.id}",
                    label=f"{handle_agent} ({dispatch_kind})",
                    kind="subagent",
                    detail=summary_text or response_preview or control_state.replace("_", " "),
                    agent_name=handle_agent or None,
                    status="running" if control_state in {"await_dependency", "await_dispatch", "await_completion"} else control_state,
                    parent_id=f"task-run:{run.id}",
                    timestamp=timestamp.isoformat() if hasattr(timestamp, "isoformat") else None,
                    metadata={
                        "step_id": active_subagent_handle.get("step_id"),
                        "dispatch_kind": active_subagent_handle.get("dispatch_kind"),
                        "control_state": active_subagent_handle.get("control_state"),
                        "available_actions": active_subagent_handle.get("available_actions"),
                        "source": active_subagent_handle.get("source"),
                        "summary_text": active_subagent_handle.get("summary_text"),
                    },
                )
            )

    return sorted(
        _dedupe_process_entries(entries),
        key=lambda entry: entry.timestamp or "",
        reverse=True,
    )[:limit]


def _process_node_timestamp(node: ChatProcessNodeInfo) -> str:
    timestamps = [node.timestamp or ""]
    timestamps.extend(_process_node_timestamp(child) for child in node.children)
    return max(timestamps)


def _process_node_has_running(node: ChatProcessNodeInfo) -> bool:
    return node.status == "running" or any(_process_node_has_running(child) for child in node.children)


def _build_process_node_children(entries: List[ChatProcessNodeInfo], chat_node_id: str) -> List[ChatProcessNodeInfo]:
    nodes: Dict[str, ChatProcessNodeInfo] = {
        entry.id: entry.model_copy(update={"children": []})
        for entry in entries
    }
    roots: List[ChatProcessNodeInfo] = []

    for entry in entries:
        node = nodes.get(entry.id)
        if node is None:
            continue
        parent_id = (entry.parent_id or "").strip()
        parent = nodes.get(parent_id) if parent_id else None
        if parent is not None and parent.id != node.id:
            parent.children.append(node)
        else:
            node.parent_id = chat_node_id
            roots.append(node)

    def sort_children(items: List[ChatProcessNodeInfo]) -> None:
        items.sort(key=lambda item: (_process_node_has_running(item), _process_node_timestamp(item)), reverse=True)
        for item in items:
            sort_children(item.children)

    sort_children(roots)
    return roots


def _build_chat_process_tree(db: Session, chatroom: Chatroom, *, limit: int = CHAT_PROCESSES_LIMIT) -> ChatProcessNodeInfo:
    project = _resolve_chatroom_project(db, chatroom)
    project_node_id = f"project:{project.id}" if project is not None else "project:none"
    chat_node_id = f"chat:{chatroom.id}"
    process_entries = _build_chat_process_entries(db, chatroom.id, limit=limit)
    chat_children = _build_process_node_children(process_entries, chat_node_id)
    latest_timestamp = max([chatroom.created_at.isoformat() if getattr(chatroom, "created_at", None) else ""] + [
        _process_node_timestamp(child)
        for child in chat_children
    ])
    chat_node = ChatProcessNodeInfo(
        id=chat_node_id,
        label=chatroom.title or f"Chat {chatroom.id}",
        kind="chat",
        detail="Current chat",
        parent_id=project_node_id,
        timestamp=latest_timestamp or None,
        children=chat_children,
    )
    return ChatProcessNodeInfo(
        id=project_node_id,
        label=(project.name if project is not None else "Standalone"),
        kind="project",
        detail="Project root" if project is not None else "Standalone chat root",
        timestamp=latest_timestamp or None,
        children=[chat_node],
    )


def _resolve_project_workspace_file(workspace_path: str, relative_path: str) -> Path:
    workspace = Path(workspace_path).expanduser().resolve()
    if not workspace.exists() or not workspace.is_dir():
        raise HTTPException(status_code=404, detail="Workspace path not found")

    normalized = (relative_path or "").replace("\\", "/").strip()
    if not normalized or normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise HTTPException(status_code=400, detail="Invalid workspace file path")

    resolved = (workspace / normalized).resolve()
    try:
        resolved.relative_to(workspace)
    except ValueError:
        raise HTTPException(status_code=400, detail="File path escapes workspace")

    if not resolved.exists():
        raise HTTPException(status_code=404, detail="Workspace file not found")
    if not resolved.is_file():
        raise HTTPException(status_code=400, detail="Workspace path is not a file")
    return resolved


def _read_project_workspace_file(workspace_path: str, relative_path: str) -> ProjectFileReadInfo:
    workspace = Path(workspace_path).expanduser().resolve()
    file_path = _resolve_project_workspace_file(workspace_path, relative_path)
    stat = file_path.stat()
    with file_path.open("rb") as handle:
        raw = handle.read(PROJECT_FILE_READ_MAX_BYTES + 1)
    truncated = len(raw) > PROJECT_FILE_READ_MAX_BYTES
    preview = raw[:PROJECT_FILE_READ_MAX_BYTES]
    relative = file_path.relative_to(workspace).as_posix()

    if b"\x00" in preview:
        return ProjectFileReadInfo(
            path=relative,
            name=file_path.name,
            size=stat.st_size,
            mtime=stat.st_mtime,
            content="",
            truncated=truncated or stat.st_size > PROJECT_FILE_READ_MAX_BYTES,
            binary=True,
            preview_limit=PROJECT_FILE_READ_MAX_BYTES,
        )

    try:
        content = preview.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        content = preview.decode("utf-8", errors="replace")
        encoding = "utf-8-replace"

    return ProjectFileReadInfo(
        path=relative,
        name=file_path.name,
        size=stat.st_size,
        mtime=stat.st_mtime,
        content=content,
        encoding=encoding,
        truncated=truncated or stat.st_size > PROJECT_FILE_READ_MAX_BYTES,
        binary=False,
        preview_limit=PROJECT_FILE_READ_MAX_BYTES,
    )


def _write_project_workspace_file(workspace_path: str, request: ProjectFileWriteRequest) -> ProjectFileReadInfo:
    if len(request.content) > PROJECT_FILE_WRITE_MAX_CHARS:
        raise HTTPException(status_code=400, detail="File content is too large to save from chat")

    workspace = Path(workspace_path).expanduser().resolve()
    file_path = _resolve_project_workspace_file(workspace_path, request.path)
    stat = file_path.stat()
    if request.expected_mtime is not None and abs(stat.st_mtime - request.expected_mtime) > 0.0001:
        raise HTTPException(status_code=409, detail="Workspace file changed after it was opened")

    current_preview = file_path.read_bytes()[:PROJECT_FILE_READ_MAX_BYTES]
    if b"\x00" in current_preview:
        raise HTTPException(status_code=400, detail="Binary files cannot be edited from chat")

    archive_workspace_artifact_snapshot(workspace, request.path, next_content=request.content)
    file_path.write_text(request.content, encoding="utf-8")
    return _read_project_workspace_file(workspace_path, request.path)


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
    
    memories = (
        db.query(Memory)
        .filter(Memory.agent_id == agent_id)
        .order_by(Memory.importance.desc(), Memory.created_at.desc())
        .all()
    )
    
    return {
        "agent_name": agent.name,
        "memory_count": len(memories),
        "memories": [
            {
                "id": m.id,
                "type": m.memory_type,
                "content": m.content,
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


def _valid_agent_names(db: Session | None = None) -> List[str]:
    registry = get_registry()
    names = {normalize_agent_type(name) for name in registry.list_agents()}
    if db is not None:
        from models import database as db_models

        rows = db.query(db_models.Agent).filter(db_models.Agent.is_active == True).all()
        for row in rows:
            names.add(normalize_agent_type(getattr(row, "agent_type", None) or getattr(row, "name", "")))
    return sorted(name for name in names if name)


def _validate_agent_names(agent_names: List[str], db: Session | None = None) -> None:
    valid_agent_names = _valid_agent_names(db)

    for agent_name in agent_names:
        normalized = normalize_agent_type(agent_name)
        if normalized not in valid_agent_names:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid agent type: {agent_name}. Valid agents: {valid_agent_names}",
            )


def _subagent_runtime_control_http_error(exc: Exception) -> HTTPException | None:
    status_code = getattr(exc, "status_code", None)
    detail = getattr(exc, "detail", None)
    if isinstance(status_code, int) and detail is not None:
        return HTTPException(status_code=status_code, detail=str(detail))
    return None


def _normalize_agent_names(agent_names: List[str] | None) -> List[str]:
    normalized = [normalize_agent_type(agent_name) for agent_name in (agent_names or [DEFAULT_AGENT_TYPE]) if agent_name]
    deduped = list(dict.fromkeys(normalized))
    return deduped or [DEFAULT_AGENT_TYPE]


@router.post("/projects", response_model=ProjectInfo)
async def create_project(project_create: ProjectCreate, db: Session = Depends(get_db)):
    """创建新项目"""
    agent_names = _normalize_agent_names(project_create.agent_names)
    _validate_agent_names(agent_names, db)
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
    _validate_agent_names(agent_names, db)
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
    _validate_agent_names(agent_names, db)
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


@router.get("/projects/{project_id}/browser", response_model=ProjectBrowserInfo)
async def get_project_browser(project_id: int, db: Session = Depends(get_db)):
    """Return a bounded workspace file/artifact index for the project browser."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.workspace_path:
        raise HTTPException(status_code=400, detail="Project has no workspace path")
    return _scan_project_browser(project.workspace_path)


@router.get("/projects/{project_id}/browser/stream")
async def stream_project_browser(project_id: int, db: Session = Depends(get_db)):
    """Stream workspace file/artifact batches as newline-delimited JSON."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.workspace_path:
        raise HTTPException(status_code=400, detail="Project has no workspace path")
    return StreamingResponse(_stream_project_browser(project.workspace_path), media_type="application/x-ndjson")


@router.get("/projects/{project_id}/browser/watch")
async def watch_project_browser(
    project_id: int,
    request: Request,
    poll_interval: float = Query(2.0, ge=0.5, le=10.0),
    max_events: Optional[int] = Query(None, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Stream bounded workspace change notifications for the project browser."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.workspace_path:
        raise HTTPException(status_code=400, detail="Project has no workspace path")
    return StreamingResponse(
        _stream_project_browser_watch_events(
            project.workspace_path,
            request=request,
            poll_interval_seconds=poll_interval,
            max_events=max_events,
        ),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/projects/{project_id}/files/read", response_model=ProjectFileReadInfo)
async def read_project_file(project_id: int, path: str, db: Session = Depends(get_db)):
    """Return a bounded, workspace-scoped read-only preview of a project file."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.workspace_path:
        raise HTTPException(status_code=400, detail="Project has no workspace path")
    return _read_project_workspace_file(project.workspace_path, path)


@router.put("/projects/{project_id}/files/write", response_model=ProjectFileReadInfo)
async def write_project_file(project_id: int, payload: ProjectFileWriteRequest, db: Session = Depends(get_db)):
    """Save a text file inside the project workspace with optimistic mtime conflict detection."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.workspace_path:
        raise HTTPException(status_code=400, detail="Project has no workspace path")
    return _write_project_workspace_file(project.workspace_path, payload)


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
            metadata=getattr(msg, "metadata", {}) or {},
            runtime_summary=_build_message_runtime_summary(
                db,
                chatroom_id=chatroom_id,
                client_turn_id=_message_client_turn_id(msg),
            ),
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

    reconcile_tracked_run_shell_runtime_cards(db, chatroom_id, limit=max(limit, 200))

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


@router.get("/chatrooms/{chatroom_id}/timeline")
async def get_chatroom_timeline(chatroom_id: int, limit: int = 50, db: Session = Depends(get_db)):
    """Return the backend-owned canonical timeline for a chatroom."""
    chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
    if not chatroom:
        raise HTTPException(status_code=404, detail="Chatroom not found")

    bounded_limit = max(1, min(limit, 200))
    task_runs = (
        db.query(TaskRun)
        .filter(TaskRun.chatroom_id == chatroom_id)
        .order_by(TaskRun.created_at.desc(), TaskRun.id.desc())
        .limit(bounded_limit)
        .all()
    )
    return build_chatroom_timeline_projection(list(reversed(task_runs)), chatroom_id=chatroom_id)


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


@router.get("/chatrooms/{chatroom_id}/processes", response_model=ChatProcessNodeInfo)
async def list_chat_processes(chatroom_id: int, limit: int = CHAT_PROCESSES_LIMIT, db: Session = Depends(get_db)):
    """Return backend-owned running process/task projection for the chat sidebar."""
    chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
    if not chatroom:
        raise HTTPException(status_code=404, detail="Chatroom not found")

    bounded_limit = max(1, min(limit, 50))
    return _build_chat_process_tree(db, chatroom, limit=bounded_limit)


@router.get("/task-runs/{task_run_id}/subagents")
async def list_task_run_subagents(task_run_id: int, db: Session = Depends(get_db)):
    """Return the current subagent lifecycle + handle projection for one task run."""
    try:
        return list_runtime_task_run_subagents(db, task_run_id)
    except Exception as exc:
        http_error = _subagent_runtime_control_http_error(exc)
        if http_error is not None:
            raise http_error
        raise


@router.get("/task-runs/{task_run_id}/subagents/{step_id}/wait")
async def wait_task_run_subagent(
    task_run_id: int,
    step_id: str,
    since_event_index: int | None = None,
    timeout_ms: int | None = None,
):
    """Observe one subagent handle and report whether its state changed since an event cursor."""
    timeout_seconds = wait_timeout_seconds(timeout_ms, default_ms=0, max_ms=5000)
    poll_interval_seconds = 0.1
    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    handle = None
    wait_result = None
    response_status = None

    while True:
        db = SessionLocal()
        try:
            observed = observe_runtime_task_run_subagent(
                db,
                task_run_id,
                step_id=step_id,
                since_event_index=since_event_index,
            )
            handle = observed["subagent_handle"]
            wait_result = observed["wait_result"]
            response_status = observed["status"]
        except Exception as exc:
            http_error = _subagent_runtime_control_http_error(exc)
            if http_error is not None:
                raise http_error
            raise
        finally:
            db.close()

        if wait_result.get("state_changed"):
            break
        if timeout_seconds <= 0:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        await asyncio.sleep(min(poll_interval_seconds, remaining))

    if wait_result is None or handle is None:
        raise HTTPException(status_code=500, detail="Failed to observe subagent handle.")

    wait_result = dict(wait_result)
    wait_result["timed_out"] = timed_out
    if timed_out and not wait_result.get("state_changed"):
        wait_result["suggested_poll"] = "timeout"
    return {
        "task_run_id": task_run_id,
        "status": response_status,
        "step_id": step_id,
        "subagent_handle": handle,
        "wait_result": wait_result,
    }


@router.get("/task-runs/{task_run_id}")
async def get_task_run_detail(
    task_run_id: int,
    event_limit: int = Query(0, ge=0, le=500),
    db: Session = Depends(get_db),
):
    """Get a single orchestration/task run with ordered ledger events."""
    task_run = (
        db.query(TaskRun)
        .filter(TaskRun.id == task_run_id)
        .first()
    )
    if not task_run:
        raise HTTPException(status_code=404, detail="Task run not found")
    reconcile_tracked_run_shell_runtime_cards(db, task_run.chatroom_id, limit=200)
    db.expire_all()
    task_run = (
        db.query(TaskRun)
        .filter(TaskRun.id == task_run_id)
        .first()
    )
    if not task_run:
        raise HTTPException(status_code=404, detail="Task run not found")
    return serialize_task_run_detail(task_run, event_limit=event_limit or None)


@router.get("/task-runs/{task_run_id}/activity")
async def get_task_run_activity(task_run_id: int, db: Session = Depends(get_db)):
    """Return the chat-facing activity projection for one task run."""
    task_run = (
        db.query(TaskRun)
        .filter(TaskRun.id == task_run_id)
        .first()
    )
    if not task_run:
        raise HTTPException(status_code=404, detail="Task run not found")
    return build_task_activity_projection(task_run)


@router.get("/task-runs/{task_run_id}/timeline")
async def get_task_run_timeline(task_run_id: int, db: Session = Depends(get_db)):
    """Return the backend-owned canonical timeline for one task run."""
    task_run = (
        db.query(TaskRun)
        .filter(TaskRun.id == task_run_id)
        .first()
    )
    if not task_run:
        raise HTTPException(status_code=404, detail="Task run not found")
    return build_task_run_timeline_projection(task_run)


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
        EventType.TASK_RUN_MANUAL_RESUME_REQUESTED,
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


@router.post("/task-runs/{task_run_id}/subagents/{step_id}/cancel")
async def cancel_task_run_subagent(
    task_run_id: int,
    step_id: str,
    req: TaskRunCancelRequest | None = None,
    db: Session = Depends(get_db),
):
    """Cancel one projected subagent handle without immediately cancelling the whole task run."""
    cancelled_by = ((req.cancelled_by if req else None) or "user").strip() or "user"
    note = ((req.note if req else None) or "").strip()
    try:
        return cancel_runtime_task_run_subagent(
            db,
            task_run_id,
            step_id=step_id,
            cancelled_by=cancelled_by,
            note=note,
        )
    except Exception as exc:
        http_error = _subagent_runtime_control_http_error(exc)
        if http_error is not None:
            raise http_error
        raise


@router.post("/task-runs/{task_run_id}/subagents/{step_id}/close")
async def close_task_run_subagent(
    task_run_id: int,
    step_id: str,
    req: TaskRunCancelRequest | None = None,
    db: Session = Depends(get_db),
):
    """Close one terminal subagent handle and archive it from the active handle set."""
    closed_by = ((req.cancelled_by if req else None) or "user").strip() or "user"
    note = ((req.note if req else None) or "").strip()
    try:
        return close_runtime_task_run_subagent(
            db,
            task_run_id,
            step_id=step_id,
            closed_by=closed_by,
            note=note,
        )
    except Exception as exc:
        http_error = _subagent_runtime_control_http_error(exc)
        if http_error is not None:
            raise http_error
        raise


@router.post("/task-runs/{task_run_id}/cancel")
async def cancel_task_run(
    task_run_id: int,
    req: TaskRunCancelRequest | None = None,
    db: Session = Depends(get_db),
):
    """Cancel a running task run and terminalize any non-terminal subagent states."""
    cancelled_by = ((req.cancelled_by if req else None) or "user").strip() or "user"
    note = ((req.note if req else None) or "").strip()
    try:
        return cancel_runtime_task_run(
            db,
            task_run_id,
            cancelled_by=cancelled_by,
            note=note,
        )
    except Exception as exc:
        http_error = _subagent_runtime_control_http_error(exc)
        if http_error is not None:
            raise http_error
        raise


async def _replay_runtime_blocked_tool_queue_item(
    db: Session,
    item: Any,
    request_payload: Dict[str, Any],
):
    from tools import tool_registry
    from tools.file_operations import reset_active_workspace, set_active_workspace

    tool_name = resolve_replay_tool_name(item, request_payload)
    arguments_text = resolve_replay_arguments_text(request_payload)
    if not tool_name:
        return build_replay_tool_result_record(
            item,
            tool_name=getattr(item, "target_name", "tool"),
            arguments=arguments_text,
            result="Error executing blocked tool replay: missing tool_name.",
            success=False,
            request_payload=request_payload,
        )

    loaded_arguments, arguments_error = parse_replay_arguments(arguments_text)
    if arguments_error is not None:
        return build_replay_tool_result_record(
            item,
            tool_name=tool_name,
            arguments=arguments_text,
            result=f"Error executing blocked tool replay: invalid arguments ({arguments_error}).",
            success=False,
            request_payload=request_payload,
        )

    chatroom = db.query(Chatroom).filter(Chatroom.id == getattr(item, "chatroom_id", None)).first()
    if chatroom is None:
        return build_replay_tool_result_record(
            item,
            tool_name=tool_name,
            arguments=arguments_text,
            result="Error executing blocked tool replay: chatroom no longer exists.",
            success=False,
            request_payload=request_payload,
        )

    project = _resolve_chatroom_project(db, chatroom)
    agents = _serialize_project_agents(db, project.id) if project else _list_global_agents(db)
    agent = find_agent_by_type(agents, getattr(item, "agent_name", None))
    task_run = get_task_run(db, getattr(item, "task_run_id", None))
    runtime_kwargs = build_tool_runtime_kwargs(
        agent,
        chatroom.id,
        project,
        task_run_id=getattr(item, "task_run_id", None),
        client_turn_id=getattr(task_run, "client_turn_id", None) if task_run is not None else None,
    )
    workspace_token = set_active_workspace(project.workspace_path if project and project.workspace_path else None)
    logger.info(
        "[ApprovalFlow] replay-runtime-start queue_item_id=%s task_run_id=%s tool=%s chatroom_id=%s project_id=%s workspace=%s",
        getattr(item, "id", None),
        getattr(item, "task_run_id", None),
        tool_name,
        getattr(item, "chatroom_id", None),
        getattr(item, "project_id", None),
        project.workspace_path if project and getattr(project, "workspace_path", None) else None,
    )

    async def emit_tool_progress(progress: dict[str, Any]) -> None:
        chatroom_id = getattr(item, "chatroom_id", None)
        if not isinstance(chatroom_id, int):
            return
        task_run = get_task_run(db, getattr(item, "task_run_id", None))
        await store_runtime_card(
            chatroom_id,
            {
                "type": "tool_call",
                "source": "approval_replay",
                "agent": (getattr(item, "agent_name", None) or "").strip() or "agent",
                "tool": tool_name,
                "arguments": arguments_text,
                "success": None,
                "status": "running",
                "blocked": False,
                "result": str(progress.get("tail_output") or "").strip() or "Tool is running.",
                "duration_ms": progress.get("duration_ms"),
                "pid": progress.get("pid"),
                "tracked_process": progress.get("tracked_process"),
                "tool_call_id": request_payload.get("tool_call_id"),
                "client_turn_id": getattr(task_run, "client_turn_id", None) if task_run is not None else None,
                "run_id": getattr(task_run, "id", None) if task_run is not None else None,
                "turn": request_payload.get("turn"),
            },
        )

    try:
        tool_result = await tool_registry.execute(
            tool_name,
            **loaded_arguments,
            **runtime_kwargs,
            __catown_approval_granted=True,
            tool_call_id=request_payload.get("tool_call_id"),
            turn=request_payload.get("turn"),
            progress_callback=emit_tool_progress if tool_name == "run_shell" else None,
        )
        tool_success = _tool_result_succeeded(tool_result)
    except Exception as exc:
        tool_result = f"Error executing {tool_name}: {exc}"
        tool_success = False
    finally:
        reset_active_workspace(workspace_token)
    logger.info(
        "[ApprovalFlow] replay-runtime-finished queue_item_id=%s tool=%s success=%s result_preview=%s",
        getattr(item, "id", None),
        tool_name,
        tool_success,
        _compact_runtime_text(_tool_result_text(tool_result), limit=200),
    )

    return build_replay_tool_result_record(
        item,
        tool_name=tool_name,
        arguments=arguments_text,
        result=tool_result,
        success=tool_success,
        request_payload=request_payload,
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


async def _start_approved_run_shell_queue_item(
    db: Session,
    item: Any,
    request_payload: Dict[str, Any],
):
    from tools import tool_registry
    from tools.file_operations import reset_active_workspace, set_active_workspace

    tool_name = "run_shell"
    arguments_text = resolve_replay_arguments_text(request_payload)
    loaded_arguments, arguments_error = parse_replay_arguments(arguments_text)
    if arguments_error is not None or loaded_arguments is None:
        return build_replay_tool_result_record(
            item,
            tool_name=tool_name,
            arguments=arguments_text,
            result=f"Error starting approved run_shell: invalid arguments ({arguments_error}).",
            success=False,
            request_payload=request_payload,
        )

    chatroom = db.query(Chatroom).filter(Chatroom.id == getattr(item, "chatroom_id", None)).first()
    if chatroom is None:
        return build_replay_tool_result_record(
            item,
            tool_name=tool_name,
            arguments=arguments_text,
            result="Error starting approved run_shell: chatroom no longer exists.",
            success=False,
            request_payload=request_payload,
        )

    project = _resolve_chatroom_project(db, chatroom)
    agents = _serialize_project_agents(db, project.id) if project else _list_global_agents(db)
    agent = find_agent_by_type(agents, getattr(item, "agent_name", None))
    task_run = get_task_run(db, getattr(item, "task_run_id", None))
    runtime_kwargs = build_tool_runtime_kwargs(
        agent,
        chatroom.id,
        project,
        task_run_id=getattr(item, "task_run_id", None),
        client_turn_id=getattr(task_run, "client_turn_id", None) if task_run is not None else None,
    )
    workspace_token = set_active_workspace(project.workspace_path if project and project.workspace_path else None)
    logger.info(
        "[ApprovalFlow] run-shell-approved-start queue_item_id=%s task_run_id=%s chatroom_id=%s project_id=%s workspace=%s",
        getattr(item, "id", None),
        getattr(item, "task_run_id", None),
        getattr(item, "chatroom_id", None),
        getattr(item, "project_id", None),
        project.workspace_path if project and getattr(project, "workspace_path", None) else None,
    )

    async def emit_tool_progress(progress: dict[str, Any]) -> None:
        chatroom_id = getattr(item, "chatroom_id", None)
        if not isinstance(chatroom_id, int):
            return
        task_run = get_task_run(db, getattr(item, "task_run_id", None))
        await store_runtime_card(
            chatroom_id,
            {
                "type": "tool_call",
                "source": "approval_continuation",
                "agent": (getattr(item, "agent_name", None) or "").strip() or "agent",
                "tool": tool_name,
                "arguments": arguments_text,
                "success": None,
                "status": "running",
                "blocked": False,
                "result": str(progress.get("tail_output") or "").strip() or "run_shell is running.",
                "duration_ms": progress.get("duration_ms"),
                "pid": progress.get("pid"),
                "tracked_process": progress.get("tracked_process"),
                "tool_call_id": request_payload.get("tool_call_id"),
                "client_turn_id": getattr(task_run, "client_turn_id", None) if task_run is not None else None,
                "run_id": getattr(task_run, "id", None) if task_run is not None else None,
                "turn": request_payload.get("turn"),
            },
        )

    try:
        tool_result = await tool_registry.execute(
            tool_name,
            **loaded_arguments,
            **runtime_kwargs,
            __catown_approval_granted=True,
            tool_call_id=request_payload.get("tool_call_id"),
            turn=request_payload.get("turn"),
            progress_callback=emit_tool_progress,
        )
        tool_success = _tool_result_succeeded(tool_result)
    except Exception as exc:
        tool_result = f"Error starting approved run_shell: {exc}"
        tool_success = False
    finally:
        reset_active_workspace(workspace_token)
    logger.info(
        "[ApprovalFlow] run-shell-approved-finished queue_item_id=%s success=%s result_preview=%s",
        getattr(item, "id", None),
        tool_success,
        _compact_runtime_text(_tool_result_text(tool_result), limit=200),
    )
    return build_replay_tool_result_record(
        item,
        tool_name=tool_name,
        arguments=arguments_text,
        result=tool_result,
        success=tool_success,
        request_payload=request_payload,
    )


def _tracked_run_shell_tokens_match(left: Any, right: Any) -> bool:
    left_token = str((left or {}).get("token") or "").strip() if isinstance(left, dict) else ""
    right_token = str((right or {}).get("token") or "").strip() if isinstance(right, dict) else ""
    return bool(left_token and right_token and left_token == right_token)


def _active_tracked_run_shell_records_for_task_run(task_run_id: Any) -> list[dict[str, Any]]:
    try:
        resolved_task_run_id = int(task_run_id)
    except (TypeError, ValueError):
        return []

    active: list[dict[str, Any]] = []
    for entry in list_tracked_run_shell_processes(limit=500, tail_chars=0):
        if entry.get("task_run_id") != resolved_task_run_id:
            continue
        record = load_tracked_run_shell_handle(entry)
        if record is None:
            continue
        if tracked_run_shell_has_exit(record):
            continue
        if tracked_run_shell_is_active(record):
            active.append(record)
    return active


def _latest_run_shell_continuation_claim(task_run: Any) -> dict[str, Any] | None:
    if task_run is None:
        return None
    events = list(getattr(task_run, "events", []) or [])
    for event in reversed(events):
        if getattr(event, "event_type", None) != EventType.RUN_SHELL_CONTINUATION_CLAIMED:
            continue
        payload = _load_jsonish_payload(getattr(event, "payload_json", None))
        if not isinstance(payload, dict):
            continue
        status = str(payload.get("status") or "").strip().lower()
        if status in {"completed", "skipped", "failed"}:
            return None
        return payload
    return None


def _run_shell_conflict_detail(
    reason: str,
    *,
    cursor: Dict[str, Any] | None = None,
    active_records: list[dict[str, Any]] | None = None,
    active_claim: dict[str, Any] | None = None,
) -> dict[str, Any]:
    detail: dict[str, Any] = {"reason": reason}
    if cursor is not None:
        detail["continuation_cursor"] = cursor
    if active_claim is not None:
        detail["active_claim"] = active_claim
    if active_records:
        detail["active_tracked_processes"] = [
            {
                "token": record.get("token"),
                "pid": record.get("pid"),
                "worker_pid": record.get("worker_pid"),
                "tool_call_id": record.get("tool_call_id"),
                "status": record.get("status"),
            }
            for record in active_records
        ]
    return detail


def _validate_run_shell_approval_can_continue(
    db: Session,
    item: Any,
    request_payload: Dict[str, Any],
) -> None:
    if (getattr(item, "target_kind", None) or "") != "tool":
        return
    tool_name = str(request_payload.get("tool_name") or getattr(item, "target_name", "") or "").strip().lower()
    if tool_name != "run_shell":
        return
    if not bool(request_payload.get("resume_supported")):
        return

    task_run = get_task_run(db, getattr(item, "task_run_id", None))
    if task_run is None:
        return
    checkpoint_snapshot = build_task_run_checkpoint_snapshot(task_run)
    cursor = checkpoint_snapshot.get("continuation_cursor")
    cursor = cursor if isinstance(cursor, dict) else {}
    active_records = _active_tracked_run_shell_records_for_task_run(getattr(item, "task_run_id", None))
    active_claim = _latest_run_shell_continuation_claim(task_run)

    if active_claim is not None and active_claim.get("queue_item_id") != getattr(item, "id", None):
        raise HTTPException(
            status_code=409,
            detail=_run_shell_conflict_detail(
                "run_shell_continuation_already_claimed",
                cursor=cursor,
                active_records=active_records,
                active_claim=active_claim,
            ),
        )
    cursor_queue_item_id = cursor.get("queue_item_id")
    if cursor_queue_item_id != getattr(item, "id", None):
        raise HTTPException(
            status_code=409,
            detail=_run_shell_conflict_detail(
                "run_shell_approval_not_current_task_cursor",
                cursor=cursor,
                active_records=active_records,
            ),
        )

    if active_records:
        raise HTTPException(
            status_code=409,
            detail=_run_shell_conflict_detail(
                "task_run_already_has_active_run_shell",
                cursor=cursor,
                active_records=active_records,
            ),
        )


def _is_resumable_run_shell_queue_item(item: Any, request_payload: Dict[str, Any]) -> bool:
    return (
        (getattr(item, "target_kind", None) or "") == "tool"
        and str(request_payload.get("tool_name") or getattr(item, "target_name", "") or "").strip().lower() == "run_shell"
        and bool(request_payload.get("resume_supported"))
    )


def _append_run_shell_continuation_claimed_event(
    db: Session,
    item: Any,
    request_payload: Dict[str, Any],
    *,
    resolved_by: str,
) -> None:
    task_run = get_task_run(db, getattr(item, "task_run_id", None))
    if task_run is None:
        return
    append_task_event(
        db,
        task_run,
        EventType.RUN_SHELL_CONTINUATION_CLAIMED,
        agent_name=getattr(item, "agent_name", None),
        summary=f"run_shell continuation claimed by approval item {getattr(item, 'id', None)}.",
        payload={
            "queue_item_id": getattr(item, "id", None),
            "tool_name": "run_shell",
            "tool_call_id": request_payload.get("tool_call_id"),
            "blocked_kind": str(request_payload.get("blocked_kind") or "").strip().lower() or None,
            "action_taken": "run_shell_continued_after_approval",
            "status": "claimed",
            "resolved_by": resolved_by,
            "tracked_process": (
                request_payload.get("metadata", {}).get("tracked_process")
                if isinstance(request_payload.get("metadata"), dict)
                else None
            ),
        },
    )


def _describe_recovery_continuation_state(checkpoint_snapshot: Any) -> Dict[str, Any]:
    return describe_checkpoint_continuation_state(checkpoint_snapshot)


def _reopen_task_run_for_followup(db: Session, task_run: Optional[TaskRun]) -> Optional[TaskRun]:
    if task_run is None:
        return None
    validate_transition(task_run.status, "running")
    task_run.status = "running"
    task_run.completed_at = None
    task_run.blocked_by_queue_item_id = None
    db.add(task_run)
    db.commit()
    db.refresh(task_run)
    return task_run


def _finalize_noncontinuable_approval_task_run(
    db: Session,
    task_run: Optional[TaskRun],
    *,
    item: Any,
    replay_result: Any,
    resolution_payload: Dict[str, Any],
) -> None:
    if task_run is None:
        return

    followup_status = str(resolution_payload.get("followup_status") or "").strip().lower()
    if followup_status == "continued":
        return
    if followup_status == "interrupted":
        return
    if followup_status == "skipped" and str(resolution_payload.get("followup_reason") or "").strip().lower() == "background_running":
        return

    tool_name = str(
        getattr(replay_result, "tool_name", None)
        or getattr(item, "target_name", None)
        or "tool"
    ).strip() or "tool"
    if followup_status == "skipped":
        followup_reason = str(resolution_payload.get("followup_reason") or "").strip() or "follow_up_skipped"
        if followup_reason == "replay_not_actionable":
            replay_status = str(
                resolution_payload.get("replay_status")
                or getattr(replay_result, "status", None)
                or "failed"
            ).strip() or "failed"
            replay_preview = _compact_runtime_text(
                str(
                    resolution_payload.get("replay_result_preview")
                    or getattr(replay_result, "result", "")
                    or ""
                ),
                limit=220,
            )
            summary = (
                f"{tool_name} continuation {replay_status}: {replay_preview}"
                if replay_preview
                else f"{tool_name} continuation {replay_status}."
            )
        else:
            summary = f"{tool_name} continuation could not continue: {followup_reason}."
    elif followup_status == "failed":
        followup_error = _compact_runtime_text(
            str(resolution_payload.get("followup_error") or "follow-up failed"),
            limit=220,
        )
        summary = f"{tool_name} continuation follow-up failed: {followup_error}"
    elif followup_status == "interrupted":
        followup_reason = _compact_runtime_text(
            str(resolution_payload.get("followup_reason") or "follow-up interrupted"),
            limit=220,
        )
        summary = f"{tool_name} continuation interrupted: {followup_reason}"
    else:
        summary = f"{tool_name} continuation did not continue."

    complete_task_run(
        db,
        task_run,
        status="failed",
        summary=summary,
    )


def _parse_authorization_rule_arguments(request_payload: Dict[str, Any]) -> dict[str, Any]:
    arguments_text = resolve_replay_arguments_text(request_payload)
    loaded_arguments, arguments_error = parse_replay_arguments(arguments_text)
    if arguments_error is not None or loaded_arguments is None:
        return {}
    return loaded_arguments


def _build_authorization_rule_preview(tool_name: str, request_payload: Dict[str, Any]) -> str:
    arguments = _parse_authorization_rule_arguments(request_payload)
    if tool_name == "run_shell":
        command = str(arguments.get("command") or "").strip()
        cwd = str(arguments.get("cwd") or ".").strip() or "."
        if command:
            return f"{command} @ {cwd}"
    return str(request_payload.get("tool_name") or tool_name or "tool").strip() or "tool"


def _approval_decision_remember_scope(item: Any, req: ApprovalQueueDecisionRequest) -> str | None:
    if req.remember_scope:
        return req.remember_scope
    if req.remember:
        return str(_effective_permissions_config().get("remember_default_scope") or AUTH_SCOPE_PROJECT)
    return None


def _persist_authorization_rule_for_queue_item(
    db: Session,
    item: Any,
    request_payload: Dict[str, Any],
    *,
    decision_kind: str,
    requested_scope: str | None,
    requested_matcher: str | None = None,
) -> dict[str, Any] | None:
    tool_name = str(request_payload.get("tool_name") or getattr(item, "target_name", "") or "").strip().lower()
    if not tool_name:
        return None

    scope = normalize_authorization_scope(
        requested_scope,
        project_id=getattr(item, "project_id", None),
        chatroom_id=getattr(item, "chatroom_id", None),
    )
    matcher_pairs = authorization_matchers_for_tool(
        tool_name,
        _parse_authorization_rule_arguments(request_payload),
    )
    if not matcher_pairs:
        matcher_pairs = [(AUTH_MATCHER_TOOL_TARGET, build_tool_target_matcher_value(tool_name))]

    normalized_matcher = str(requested_matcher or "").strip().lower()
    if not normalized_matcher:
        configured_matcher = _effective_permissions_config().get("remember_default_matcher")
        normalized_matcher = str(configured_matcher or "").strip().lower()
    if not normalized_matcher:
        normalized_matcher = AUTH_MATCHER_COMMAND_FINGERPRINT if any(
            matcher_type == AUTH_MATCHER_COMMAND_FINGERPRINT
            for matcher_type, _ in matcher_pairs
        ) else AUTH_MATCHER_TOOL_TARGET
    matcher_pair = next(
        ((matcher_type, matcher_value) for matcher_type, matcher_value in matcher_pairs if matcher_type == normalized_matcher),
        None,
    )
    if matcher_pair is None and normalized_matcher == AUTH_MATCHER_ALL_TOOLS:
        matcher_pair = (AUTH_MATCHER_ALL_TOOLS, "*")
    if matcher_pair is None and normalized_matcher == AUTH_MATCHER_TOOL_TARGET:
        matcher_pair = (AUTH_MATCHER_TOOL_TARGET, build_tool_target_matcher_value(tool_name))
    if matcher_pair is None:
        matcher_pair = matcher_pairs[0]
    matcher_type, matcher_value = matcher_pair
    constraints: dict[str, Any] = {}
    if decision_kind == AUTH_DECISION_ALLOW_NO_TIMEOUT:
        constraints["allow_timeout_bypass"] = True
    constraints["created_from_queue_item_id"] = getattr(item, "id", None)
    constraints["matcher_selection"] = matcher_type
    rule = upsert_authorization_rule(
        db,
        tool_name=tool_name,
        scope=scope,
        matcher_type=matcher_type,
        matcher_value=matcher_value,
        decision_kind=decision_kind,
        project_id=getattr(item, "project_id", None),
        chatroom_id=getattr(item, "chatroom_id", None),
        preference_kind=AUTH_PREFERENCE_KIND,
        preference_value="denied" if decision_kind == AUTH_DECISION_DENY else "granted",
        constraints=constraints,
        command_preview=_build_authorization_rule_preview(tool_name, request_payload),
    )
    return serialize_authorization_rule(rule)


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
    non_actionable_reason = replay_result_non_actionable_reason(replay_result)
    if non_actionable_reason is not None:
        if non_actionable_reason == "background_running" and str(getattr(replay_result, "tool_name", "") or "").strip().lower() == "run_shell":
            _spawn_background_tracked_run_shell_watch(
                task_run.id,
                {
                    "type": "tool_call",
                    "source": "approval_continuation",
                    "agent": (item.agent_name or "").strip() or "agent",
                    "tool": "run_shell",
                    "arguments": resolve_replay_arguments_text(request_payload),
                    "success": None,
                    "status": "running",
                    "blocked": False,
                    "result": str(getattr(replay_result, "result", "") or "").strip() or "run_shell is running.",
                    "tracked_process": (
                        getattr(replay_result, "metadata", {}).get("tracked_process")
                        if isinstance(getattr(replay_result, "metadata", None), dict)
                        else {}
                    ) or {},
                    "tool_call_id": getattr(replay_result, "tool_call_id", None),
                    "client_turn_id": getattr(task_run, "client_turn_id", None),
                    "run_id": getattr(task_run, "id", None),
                    "turn": request_payload.get("turn"),
                },
            )
        logger.info(
            "[ApprovalFlow] runtime-followup-skipped queue_item_id=%s tool=%s reason=%s replay_status=%s replay_blocked=%s",
            getattr(item, "id", None),
            getattr(replay_result, "tool_name", None),
            non_actionable_reason,
            getattr(replay_result, "status", None),
            getattr(replay_result, "blocked", None),
        )
        return build_followup_skipped_payload(non_actionable_reason)

    followup_context = build_tool_replay_followup_context(item, replay_result)
    _reopen_task_run_for_followup(db, task_run)
    append_task_event(
        db,
        task_run,
        EventType.APPROVAL_QUEUE_ITEM_FOLLOWUP_TRIGGERED,
        agent_name=item.agent_name,
        summary=f"Continuing agent turn after approved continuation of {getattr(replay_result, 'tool_name', item.target_name or 'tool')}.",
        payload=build_followup_triggered_event_payload(
            item,
            replay_result,
        ),
    )
    logger.info(
        "[ApprovalFlow] runtime-followup-triggered queue_item_id=%s task_run_id=%s tool=%s",
        getattr(item, "id", None),
        getattr(task_run, "id", None),
        getattr(replay_result, "tool_name", None),
    )
    followup_snapshot = build_task_run_checkpoint_snapshot(task_run)
    try:
        followup_result = await trigger_agent_response(
            getattr(item, "chatroom_id", None),
            task_run.user_request or "",
            getattr(task_run, "client_turn_id", None),
            task_run_id=task_run.id,
            extra_context=followup_context,
            checkpoint_snapshot=followup_snapshot,
        )
    except asyncio.CancelledError as exc:
        logger.warning(
            "[ApprovalFlow] runtime-followup-interrupted queue_item_id=%s task_run_id=%s tool=%s reason=cancelled",
            getattr(item, "id", None),
            getattr(task_run, "id", None),
            getattr(replay_result, "tool_name", None),
        )
        append_task_event(
            db,
            task_run,
            EventType.APPROVAL_QUEUE_ITEM_FOLLOWUP_INTERRUPTED,
            agent_name=item.agent_name,
            summary=f"Approved continuation follow-up interrupted for {getattr(replay_result, 'tool_name', item.target_name or 'tool')}.",
            payload={
                "queue_item_id": getattr(item, "id", None),
                "tool_name": getattr(replay_result, "tool_name", None),
                "reason": "cancelled",
                "task_run_id": getattr(task_run, "id", None),
            },
        )
        return build_followup_interrupted_payload("cancelled")
    except Exception as exc:
        logger.exception(
            "[ApprovalFlow] runtime-followup-failed queue_item_id=%s task_run_id=%s tool=%s error=%s",
            getattr(item, "id", None),
            getattr(task_run, "id", None),
            getattr(replay_result, "tool_name", None),
            exc,
        )
        append_task_event(
            db,
            task_run,
            EventType.APPROVAL_QUEUE_ITEM_FOLLOWUP_FAILED,
            agent_name=item.agent_name,
            summary=f"Approved continuation follow-up failed for {getattr(replay_result, 'tool_name', item.target_name or 'tool')}.",
            payload=build_followup_failed_event_payload(item, replay_result, exc),
        )
        return build_followup_failed_payload(exc)

    followup_completed = bool(followup_result.get("completed"))
    awaiting_tool_approval = bool(followup_result.get("awaiting_tool_approval"))
    awaiting_background_tool = bool(followup_result.get("awaiting_background_tool"))
    followup_outcome = str(followup_result.get("outcome") or "").strip().lower()
    if followup_completed:
        return build_followup_continued_payload(
            followup_outcome=followup_outcome or "completed",
        )
    if awaiting_tool_approval:
        return build_followup_continued_payload(
            followup_outcome=followup_outcome or "awaiting_tool_approval",
        )
    if awaiting_background_tool:
        return build_followup_continued_payload(
            followup_outcome=followup_outcome or "awaiting_background_tool",
        )
    if followup_outcome == "empty":
        return build_followup_failed_payload(
            "non_stream_followup_empty",
            followup_reason="empty_followup_result",
        )
    return build_followup_interrupted_payload(
        followup_outcome or "incomplete_followup_result",
    )


async def _continue_pipeline_after_approved_tool_replay(
    db: Session,
    item: Any,
    request_payload: Dict[str, Any],
    replay_result: Any,
):
    pipeline_run_id = getattr(item, "pipeline_run_id", None) or request_payload.get("pipeline_run_id")
    if not pipeline_run_id:
        return build_followup_skipped_payload("pipeline_run_missing")
    non_actionable_reason = replay_result_non_actionable_reason(replay_result)
    if non_actionable_reason is not None:
        return build_followup_skipped_payload(non_actionable_reason)

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
        EventType.APPROVAL_QUEUE_ITEM_FOLLOWUP_TRIGGERED,
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
        try:
            db.rollback()
        except Exception:
            pass
        item_agent_name = getattr(item, "agent_name", None)
        append_task_event(
            db,
            task_run,
            EventType.APPROVAL_QUEUE_ITEM_FOLLOWUP_FAILED,
            agent_name=item_agent_name,
            summary=f"Approved continuation follow-up failed for pipeline tool {getattr(replay_result, 'tool_name', item.target_name or 'tool')}.",
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


async def _finalize_approved_queue_item_followup_async(
    item_id: int,
    *,
    request_payload: Dict[str, Any],
    resolved_by: str,
    resolution_note: str,
) -> None:
    db = SessionLocal()
    try:
        item = get_approval_queue_item(db, item_id)
        if item is None:
            return
        if (item.status or "").lower() != "approved":
            return
        if (item.target_kind or "") != "tool" or not bool(request_payload.get("resume_supported")):
            return
        if runtime_is_shutting_down():
            resolution_payload = load_approval_queue_request_payload(getattr(item, "resolution_payload_json", None))
            resolution_payload.update(build_followup_skipped_payload("runtime_shutting_down"))
            item.resolution_payload_json = json.dumps(resolution_payload, ensure_ascii=False)
            item.resolution_note = resolution_note or item.resolution_note
            item.resolved_by = resolved_by or item.resolved_by
            db.add(item)
            db.commit()
            db.refresh(item)
            task_run = get_task_run(db, item.task_run_id)
            append_task_event(
                db,
                task_run,
                EventType.APPROVAL_QUEUE_ITEM_RESOLVED,
                agent_name=item.agent_name,
                summary=f"Approved queue item follow-up skipped during shutdown for {item.target_name or item.target_kind}.",
                payload=build_approval_queue_item_resolved_event_payload(
                    item,
                    status="approved",
                    resolved_by=resolved_by,
                    request_payload=request_payload,
                    resolution_payload=resolution_payload,
                ),
            )
            logger.info("[ApprovalFlow] followup-skipped-during-shutdown queue_item_id=%s", item_id)
            return

        if str(request_payload.get("tool_name") or getattr(item, "target_name", "") or "").strip().lower() == "run_shell":
            replay_result = await _start_approved_run_shell_queue_item(db, item, request_payload)
            action_taken = "run_shell_continued_after_approval"
        else:
            replay_result = await _replay_blocked_tool_queue_item(db, item, request_payload)
            action_taken = "tool_replayed"
        task_run = get_task_run(db, item.task_run_id)
        try:
            replay_turn = max(1, int(request_payload.get("turn") or 1))
        except (TypeError, ValueError):
            replay_turn = 1
        tool_name = str(getattr(replay_result, "tool_name", None) or getattr(item, "target_name", None) or "tool").strip() or "tool"
        tool_arguments = resolve_replay_arguments_text(request_payload)
        if action_taken == "tool_replayed":
            record_tool_call_started(
                db,
                task_run,
                agent_name=(item.agent_name or "").strip() or "agent",
                turn=replay_turn,
                tool_name=tool_name,
                arguments=tool_arguments,
                payload={
                    "tool_call_id": getattr(replay_result, "tool_call_id", None),
                    "replay": True,
                    "replay_of_queue_item_id": getattr(item, "id", None),
                    "resumed_after_approval": True,
                },
            )
            chatroom_id = getattr(item, "chatroom_id", None)
            if isinstance(chatroom_id, int):
                await store_runtime_card(
                    chatroom_id,
                    {
                        "type": "tool_call",
                        "source": "approval_replay",
                        "agent": (item.agent_name or "").strip() or "agent",
                        "tool": tool_name,
                        "arguments": tool_arguments,
                        "success": None,
                        "status": "running",
                        "blocked": False,
                        "result": "Resumed after approval. Tool is running.",
                        "pid": getattr(replay_result, "pid", None),
                        "tool_call_id": getattr(replay_result, "tool_call_id", None),
                        "client_turn_id": getattr(task_run, "client_turn_id", None) if task_run is not None else None,
                        "run_id": getattr(task_run, "id", None) if task_run is not None else None,
                        "turn": replay_turn,
                    },
                )
        record_runner_tool_round(
            db,
            task_run,
            agent_name=(item.agent_name or "").strip() or "agent",
            turn=replay_turn,
            tool_names=[tool_name],
            tool_results=[replay_result],
            summary=f"Continued blocked tool {tool_name} after approval.",
            payload=build_approval_queue_replay_round_payload(item, request_payload),
        )
        resolution_payload = build_queue_replay_resolution_payload(
            request_payload=request_payload,
            replay_result=replay_result,
            action_taken=action_taken,
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

        item.resolution_payload_json = json.dumps(resolution_payload, ensure_ascii=False)
        item.resolution_note = resolution_note or item.resolution_note
        item.resolved_by = resolved_by or item.resolved_by
        db.add(item)
        db.commit()
        db.refresh(item)

        task_run = get_task_run(db, item.task_run_id)
        if action_taken == "run_shell_continued_after_approval":
            append_task_event(
                db,
                task_run,
                EventType.RUN_SHELL_CONTINUATION_CLAIMED,
                agent_name=item.agent_name,
                summary=f"run_shell continuation finished for approval item {getattr(item, 'id', None)}.",
                payload={
                    "queue_item_id": getattr(item, "id", None),
                    "tool_name": "run_shell",
                    "tool_call_id": getattr(replay_result, "tool_call_id", None),
                    "action_taken": action_taken,
                    "status": "completed",
                    "tool_status": getattr(replay_result, "status", None),
                    "tool_success": bool(getattr(replay_result, "success", False)),
                    "tool_blocked": bool(getattr(replay_result, "blocked", False)),
                    "tracked_process": (
                        getattr(replay_result, "metadata", {}).get("tracked_process")
                        if isinstance(getattr(replay_result, "metadata", None), dict)
                        else None
                    ),
                },
            )
        append_task_event(
            db,
            task_run,
            EventType.APPROVAL_QUEUE_ITEM_RESOLVED,
            agent_name=item.agent_name,
            summary=f"Approved queue item continuation updated for {item.target_name or item.target_kind}.",
            payload=build_approval_queue_item_resolved_event_payload(
                item,
                status="approved",
                resolved_by=resolved_by,
                request_payload=request_payload,
                resolution_payload=resolution_payload,
            ),
        )
        _finalize_noncontinuable_approval_task_run(
            db,
            task_run,
            item=item,
            replay_result=replay_result,
            resolution_payload=resolution_payload,
        )
    except Exception as exc:
        logger.exception("Approval queue async follow-up failed for item %s: %s", item_id, exc)
        try:
            item = get_approval_queue_item(db, item_id)
            task_run = get_task_run(db, getattr(item, "task_run_id", None) if item is not None else None)
            if (
                item is not None
                and str(request_payload.get("tool_name") or getattr(item, "target_name", "") or "").strip().lower() == "run_shell"
            ):
                append_task_event(
                    db,
                    task_run,
                    EventType.RUN_SHELL_CONTINUATION_CLAIMED,
                    agent_name=getattr(item, "agent_name", None),
                    summary=f"run_shell continuation failed for approval item {item_id}.",
                    payload={
                        "queue_item_id": item_id,
                        "tool_name": "run_shell",
                        "tool_call_id": request_payload.get("tool_call_id"),
                        "status": "failed",
                        "error": str(exc),
                    },
                )
        except Exception:
            logger.exception("Failed to release run_shell continuation claim for item %s", item_id)
    finally:
        db.close()


def _spawn_approval_followup_worker(
    item_id: int,
    *,
    request_payload: Dict[str, Any],
    resolved_by: str,
    resolution_note: str,
) -> None:
    def _runner() -> None:
        try:
            asyncio.run(
                _finalize_approved_queue_item_followup_async(
                    item_id,
                    request_payload=request_payload,
                    resolved_by=resolved_by,
                    resolution_note=resolution_note,
                )
            )
        except Exception as exc:
            logger.exception("Approval follow-up worker crashed for item %s: %s", item_id, exc)

    thread = threading.Thread(
        target=_runner,
        name=f"approval-followup-{item_id}",
        daemon=True,
    )
    thread.start()


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
    request_payload_for_audit = {**request_payload, "request_key": getattr(item, "request_key", None)}
    resolution_note = ((req.note if req else None) or "").strip()
    resolved_by = ((req.resolved_by if req else None) or "user").strip() or "user"
    logger.info(
        "[ApprovalFlow] approve-request queue_item_id=%s task_run_id=%s target=%s status=%s resolved_by=%s resume_supported=%s",
        item_id,
        getattr(item, "task_run_id", None),
        getattr(item, "target_name", None),
        getattr(item, "status", None),
        resolved_by,
        bool(request_payload.get("resume_supported")),
    )
    if not claim_approval_queue_resolution_lease(
        db,
        item,
        owner=f"approval-api:{resolved_by}:{item_id}",
    ):
        raise HTTPException(status_code=409, detail="Approval queue item is leased by another resolver.")

    requires_run_shell_continuation = (
        (item.target_kind or "") == "tool"
        and bool(request_payload.get("resume_supported"))
        and getattr(item, "task_run_id", None) is not None
        and str(request_payload.get("tool_name") or getattr(item, "target_name", "") or "").strip().lower() == "run_shell"
    )
    if requires_run_shell_continuation:
        try:
            _validate_run_shell_approval_can_continue(db, item, request_payload)
        except HTTPException:
            item.resolution_owner = None
            item.resolution_lease_expires_at = None
            db.add(item)
            db.commit()
            raise

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
        record_approval_audit(
            db,
            event_kind="queue_item_approved",
            decision="approve",
            source="manual",
            resolved_by=resolved_by,
            queue_item=refreshed,
            tool_name=str(request_payload.get("tool_name") or getattr(refreshed, "target_name", "") or "").strip() or None,
            reason=resolution_note or getattr(refreshed, "summary", None),
            request_payload=request_payload_for_audit,
            resolution_payload=load_approval_queue_request_payload(getattr(refreshed, "resolution_payload_json", None)),
        )
        return serialize_approval_queue_item(refreshed)

    resolution_payload = build_queue_replay_resolution_payload(
        request_payload=request_payload,
        action_taken="queue_resolved_only",
    )
    remembered_rule = None
    remember_scope = _approval_decision_remember_scope(item, req) if req is not None else None
    if remember_scope:
        remembered_rule = _persist_authorization_rule_for_queue_item(
            db,
            item,
            request_payload,
            decision_kind=AUTH_DECISION_ALLOW,
            requested_scope=remember_scope,
            requested_matcher=req.remember_matcher,
        )
        if remembered_rule is not None:
            resolution_payload["remembered_rule"] = remembered_rule

    resolved = resolve_approval_queue_item(
        db,
        item,
        status="approved",
        resolved_by=resolved_by,
        resolution_note=resolution_note or f"Approved {item.target_kind or 'action'} from the API.",
        resolution_payload=resolution_payload,
    )
    record_approval_audit(
        db,
        event_kind="queue_item_approved",
        decision="approve",
        source="manual",
        resolved_by=resolved_by,
        queue_item=resolved or item,
        preference=remembered_rule,
        tool_name=str(request_payload.get("tool_name") or getattr(item, "target_name", "") or "").strip() or None,
        reason=resolution_note or getattr(item, "summary", None),
        request_payload=request_payload_for_audit,
        resolution_payload=resolution_payload,
    )
    if remembered_rule is not None:
        record_approval_audit(
            db,
            event_kind="authorization_rule_saved",
            decision=str(remembered_rule.get("decision_kind") or "allow"),
            source="remember",
            resolved_by=resolved_by,
            queue_item=resolved or item,
            preference=remembered_rule,
            tool_name=remembered_rule.get("tool_name"),
            scope=remembered_rule.get("scope"),
            matcher_type=remembered_rule.get("matcher_type"),
            matcher_value=remembered_rule.get("matcher_value"),
            command_preview=remembered_rule.get("command_preview"),
            reason=resolution_note or getattr(item, "summary", None),
            request_payload=request_payload_for_audit,
            resolution_payload={"remembered_rule": remembered_rule},
        )
    task_run = get_task_run(db, item.task_run_id)
    append_task_event(
        db,
        task_run,
        EventType.APPROVAL_QUEUE_ITEM_RESOLVED,
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
    logger.info(
        "[ApprovalFlow] approve-resolved queue_item_id=%s task_run_id=%s target=%s immediate_action=%s",
        item_id,
        getattr(item, "task_run_id", None),
        getattr(item, "target_name", None),
        resolution_payload.get("action_taken"),
    )
    if (item.target_kind or "") == "tool" and bool(request_payload.get("resume_supported")) and getattr(resolved or item, "task_run_id", None) is not None:
        if runtime_is_shutting_down():
            shutdown_followup = build_followup_skipped_payload("runtime_shutting_down")
            resolution_payload.update(shutdown_followup)
            resolved.resolution_payload_json = json.dumps(resolution_payload, ensure_ascii=False)
            db.add(resolved)
            db.commit()
            db.refresh(resolved)
            append_task_event(
                db,
                task_run,
                EventType.APPROVAL_QUEUE_ITEM_RESOLVED,
                agent_name=item.agent_name,
                summary=f"Approved queue item follow-up skipped during shutdown for {item.target_name or item.target_kind}.",
                payload=build_approval_queue_item_resolved_event_payload(
                    resolved,
                    status="approved",
                    resolved_by=resolved_by,
                    request_payload=request_payload,
                    resolution_payload=resolution_payload,
                ),
            )
            logger.info(
                "[ApprovalFlow] approve-followup-skipped-during-shutdown queue_item_id=%s task_run_id=%s target=%s",
                item_id,
                getattr(item, "task_run_id", None),
                getattr(item, "target_name", None),
            )
        else:
            if str(request_payload.get("tool_name") or getattr(item, "target_name", "") or "").strip().lower() == "run_shell":
                task_run = _reopen_task_run_for_followup(db, task_run)
                _append_run_shell_continuation_claimed_event(
                    db,
                    resolved or item,
                    request_payload,
                    resolved_by=resolved_by,
                )
            else:
                task_run = _reopen_task_run_for_followup(db, task_run)
            _spawn_approval_followup_worker(
                item.id,
                request_payload=request_payload,
                resolved_by=resolved_by,
                resolution_note=resolution_note or f"Approved {item.target_kind or 'action'} from the API.",
            )
            logger.info(
                "[ApprovalFlow] approve-followup-dispatched queue_item_id=%s task_run_id=%s target=%s",
                item_id,
                getattr(item, "task_run_id", None),
                getattr(item, "target_name", None),
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
    request_payload_for_audit = {**request_payload, "request_key": getattr(item, "request_key", None)}
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
        record_approval_audit(
            db,
            event_kind="queue_item_rejected",
            decision="reject",
            source="manual",
            resolved_by=resolved_by,
            queue_item=refreshed,
            tool_name=str(request_payload.get("tool_name") or getattr(refreshed, "target_name", "") or "").strip() or None,
            reason=resolution_note or getattr(refreshed, "summary", None),
            request_payload=request_payload_for_audit,
            resolution_payload=load_approval_queue_request_payload(getattr(refreshed, "resolution_payload_json", None)),
        )
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
    rejection_payload = _load_jsonish_payload(getattr(resolved, "resolution_payload_json", None))
    remembered_rule = None
    remember_scope = _approval_decision_remember_scope(item, req) if req is not None else None
    if remember_scope:
        remembered_rule = _persist_authorization_rule_for_queue_item(
            db,
            item,
            request_payload,
            decision_kind=AUTH_DECISION_DENY,
            requested_scope=remember_scope,
            requested_matcher=req.remember_matcher,
        )
        if remembered_rule is not None:
            resolved.resolution_payload_json = json.dumps(
                {
                    **(_load_jsonish_payload(resolved.resolution_payload_json)),
                    "remembered_rule": remembered_rule,
                },
                ensure_ascii=False,
            )
            db.add(resolved)
            db.commit()
            db.refresh(resolved)
            rejection_payload = _load_jsonish_payload(getattr(resolved, "resolution_payload_json", None))
    record_approval_audit(
        db,
        event_kind="queue_item_rejected",
        decision="reject",
        source="manual",
        resolved_by=resolved_by,
        queue_item=resolved or item,
        preference=remembered_rule,
        tool_name=str(request_payload.get("tool_name") or getattr(item, "target_name", "") or "").strip() or None,
        reason=resolution_note or getattr(item, "summary", None),
        request_payload=request_payload_for_audit,
        resolution_payload=rejection_payload,
    )
    if remembered_rule is not None:
        record_approval_audit(
            db,
            event_kind="authorization_rule_saved",
            decision=str(remembered_rule.get("decision_kind") or "deny"),
            source="remember",
            resolved_by=resolved_by,
            queue_item=resolved or item,
            preference=remembered_rule,
            tool_name=remembered_rule.get("tool_name"),
            scope=remembered_rule.get("scope"),
            matcher_type=remembered_rule.get("matcher_type"),
            matcher_value=remembered_rule.get("matcher_value"),
            command_preview=remembered_rule.get("command_preview"),
            reason=resolution_note or getattr(item, "summary", None),
            request_payload=request_payload_for_audit,
            resolution_payload={"remembered_rule": remembered_rule},
        )
    task_run = get_task_run(db, item.task_run_id)
    append_task_event(
        db,
        task_run,
        EventType.APPROVAL_QUEUE_ITEM_RESOLVED,
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


@router.get("/tool-authorization-rules")
async def get_tool_authorization_rules(
    project_id: Optional[int] = None,
    chatroom_id: Optional[int] = None,
    tool_name: Optional[str] = None,
    include_revoked: bool = False,
    db: Session = Depends(get_db),
):
    rules = list_authorization_rules(
        db,
        project_id=project_id,
        chatroom_id=chatroom_id,
        tool_name=(tool_name or "").strip() or None,
        include_revoked=include_revoked,
    )
    return [serialize_authorization_rule(rule) for rule in rules]


@router.delete("/tool-authorization-rules/{rule_id}")
async def delete_tool_authorization_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.query(ToolExecutionPreference).filter(ToolExecutionPreference.id == rule_id).first()
    if rule is None:
        raise HTTPException(status_code=404, detail="Authorization rule not found")
    revoked = revoke_authorization_rule(db, rule)
    return {
        "message": "Authorization rule revoked",
        "rule": serialize_authorization_rule(revoked or rule),
    }


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


# ==================== 文件上传 ====================

# Supported image MIME types for upload
_UPLOAD_IMAGE_TYPES = {
    "image/png", "image/jpeg", "image/gif", "image/webp",
    "image/bmp", "image/svg+xml",
}
_UPLOAD_MAX_SIZE_BYTES = 20 * 1024 * 1024  # 20MB


@router.post("/chatrooms/{chatroom_id}/upload", response_model=MessageAttachment)
async def upload_file(
    chatroom_id: int,
    file: UploadFile = FastAPIFile(...),
    db: Session = Depends(get_db),
):
    """
    Upload a file (image) to a chatroom's project workspace.

    Saves the file to `projects/{id}/uploads/` and returns the file path
    and metadata for use as a message attachment.
    """
    chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
    if not chatroom:
        raise HTTPException(status_code=404, detail="Chatroom not found")

    project = _resolve_chatroom_project(db, chatroom)
    if not project:
        raise HTTPException(status_code=400, detail="Chatroom has no associated project")

    if not project.workspace_path:
        raise HTTPException(status_code=400, detail="Project has no workspace path")

    # Validate file type
    content_type = file.content_type or ""
    if content_type not in _UPLOAD_IMAGE_TYPES:
        # Try to guess from filename
        import mimetypes as _mimetypes
        guessed_type, _ = _mimetypes.guess_type(file.filename or "")
        if not guessed_type or guessed_type not in _UPLOAD_IMAGE_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type: {content_type}. "
                       f"Supported: {', '.join(sorted(_UPLOAD_IMAGE_TYPES))}"
            )
        content_type = guessed_type

    # Read file content
    content = await file.read()
    if len(content) > _UPLOAD_MAX_SIZE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File too large: {len(content)} bytes (max {_UPLOAD_MAX_SIZE_BYTES} bytes)"
        )

    # Determine file extension
    import mimetypes as _mimetypes
    ext = _mimetypes.guess_extension(content_type) or ""
    if not ext and file.filename:
        ext = Path(file.filename).suffix

    # Generate unique filename
    import uuid as _uuid
    upload_dir = Path(project.workspace_path) / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    safe_name = Path(file.filename or "upload").stem
    unique_name = f"{safe_name}_{_uuid.uuid4().hex[:8]}{ext}"
    file_path = upload_dir / unique_name

    # Write file
    file_path.write_bytes(content)

    # Return relative path from workspace
    workspace = Path(project.workspace_path).expanduser().resolve()
    relative_path = file_path.resolve().relative_to(workspace).as_posix()

    from datetime import datetime as _dt
    return MessageAttachment(
        file_path=relative_path,
        file_name=unique_name,
        file_size=len(content),
        mime_type=content_type,
        upload_time=_dt.utcnow().isoformat(),
    )


@router.post("/chatrooms/{chatroom_id}/messages", response_model=MessageResponse)
async def send_message(chatroom_id: int, message: MessageRequest, db: Session = Depends(get_db)):
    """发送消息到聊天室"""
    logger.info(f"[API] send_message called: chatroom_id={chatroom_id}, content={message.content[:50]}...")
    
    # 发送用户消息
    # Build metadata with attachments info
    msg_metadata = _message_metadata_with_turn(message.client_turn_id)
    if message.attachments:
        msg_metadata["attachments"] = message.attachments

    response_msg = await chatroom_manager.send_message(
        chatroom_id=chatroom_id,
        agent_id=None,  # None 表示用户
        content=message.content,
        message_type="text",
        metadata=msg_metadata,
    )
    await publish_saved_chat_message(
        db,
        chatroom_id,
        message_id=response_msg.id,
        content=response_msg.content,
        agent_name=None,
        message_type=response_msg.message_type,
        created_at=response_msg.created_at,
        metadata=msg_metadata,
    )
    
    logger.info(f"[API] User message saved: id={response_msg.id}")

    # Build extra context from attachments
    extra_context = ""
    if message.attachments:
        attachment_lines = []
        for att in message.attachments:
            att_path = att.get("file_path", "")
            att_mime = att.get("mime_type", "")
            if att_path:
                attachment_lines.append(f"- {att_path} ({att_mime})")
        if attachment_lines:
            extra_context = (
                "The user has attached the following files with their message:\n"
                + "\n".join(attachment_lines)
                + "\n\nTo analyze images, use the analyze_image tool with the file path above."
            )

    chatroom = db.query(Chatroom).filter(Chatroom.id == chatroom_id).first()
    project = _resolve_chatroom_project(db, chatroom) if chatroom else None
    task_run = create_task_run(
        db,
        chatroom_id=chatroom_id,
        project_id=project.id if project else None,
        origin_message_id=response_msg.id,
        client_turn_id=message.client_turn_id,
        run_kind=RunKind.CHAT_TURN,
        user_request=message.content,
        initiator="user",
    )
    append_task_event(
        db,
        task_run,
        EventType.USER_MESSAGE_SAVED,
        message_id=response_msg.id,
        summary="",
        payload={
            "message_id": response_msg.id,
            "content": message.content,
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
            extra_context=extra_context,
        )
        logger.info(f"[API] Agent response completed")
    except Exception as e:
        logger.error(f"[API] Agent response error: {e}")
        import traceback
        traceback.print_exc()
        # P0-1: Terminalize TaskRun on failure so it doesn't hang in "running".
        task_run = get_task_run(db, task_run.id)
        if task_run and (task_run.status or "").strip().lower() == "running":
            terminalize_task_run(
                db, task_run,
                status="failed",
                error=e,
                summary=f"Agent response failed: {e}",
                event_type=EventType.TASK_RUN_FAILED,
            )
    
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
            # 1. 保存用户消息（含附件元数据）
            stream_msg_metadata = _message_metadata_with_turn(message.client_turn_id)
            if message.attachments:
                stream_msg_metadata["attachments"] = message.attachments

            user_msg = await chatroom_manager.send_message(
                chatroom_id=chatroom_id,
                agent_id=None,
                content=message.content,
                message_type="text",
                metadata=stream_msg_metadata,
            )
            await publish_saved_chat_message(
                db,
                chatroom_id,
                message_id=user_msg.id,
                content=user_msg.content,
                agent_name=None,
                message_type=user_msg.message_type,
                created_at=user_msg.created_at,
                metadata=stream_msg_metadata,
            )

            # Build extra context from attachments
            stream_extra_context = ""
            if message.attachments:
                att_lines = []
                for att in message.attachments:
                    att_path = att.get("file_path", "")
                    att_mime = att.get("mime_type", "")
                    if att_path:
                        att_lines.append(f"- {att_path} ({att_mime})")
                if att_lines:
                    stream_extra_context = (
                        "The user has attached the following files with their message:\n"
                        + "\n".join(att_lines)
                        + "\n\nTo analyze images, use the analyze_image tool with the file path above."
                    )

            # Inject attachment context into user message for all stream paths
            effective_user_content = message.content
            if stream_extra_context:
                effective_user_content = message.content + "\n\n" + stream_extra_context

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
                run_kind=RunKind.CHAT_TURN_STREAM,
                user_request=message.content,
                initiator="user",
            )
            append_task_event(
                db,
                task_run,
                EventType.USER_MESSAGE_SAVED,
                message_id=user_msg.id,
                summary="",
                payload={
                    "message_id": user_msg.id,
                    "content": message.content,
                    "content_preview": _compact_runtime_text(message.content, limit=220),
                    "client_turn_id": message.client_turn_id,
                },
            )
            yield f"data: {_json.dumps({'type': 'user_saved', 'id': user_msg.id, 'client_turn_id': message.client_turn_id, 'task_run_id': task_run.id})}\n\n"
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
                    _record_target_agents_selected(
                        db,
                        task_run,
                        agent_names=mentioned_names,
                        client_turn_id=message.client_turn_id,
                        project_id=None,
                        run_kind=RunKind.MULTI_AGENT_ORCHESTRATION_STREAM,
                    )
                    _select_task_run_runtime_mode(
                        db,
                        task_run,
                        run_kind=RunKind.MULTI_AGENT_ORCHESTRATION_STREAM,
                        summary="",
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
                        user_message=effective_user_content,
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
                standalone_llm_client = get_llm_client_for_agent(standalone_agent_name) if standalone_target else get_default_llm_client()
                standalone_stream_policy = _build_single_agent_runner_policy(
                    run_kind=RunKind.STANDALONE_ASSISTANT_STREAM,
                    agent_name=standalone_agent_name,
                    project_id=None,
                    tool_names=[],
                    streaming=True,
                    standalone=True,
                )
                _select_task_run_runtime_mode(
                    db,
                    task_run,
                    run_kind=RunKind.STANDALONE_ASSISTANT_STREAM,
                    summary="",
                    project_id=None,
                    target_agent_name=standalone_agent_name,
                    runner_policy=standalone_stream_policy,
                )
                _record_target_agent_selected(
                    db,
                    task_run,
                    agent_name=standalone_agent_name,
                    model=getattr(standalone_llm_client, "model", None),
                    tool_names=[],
                    client_turn_id=message.client_turn_id,
                    project_id=None,
                    run_kind=RunKind.STANDALONE_ASSISTANT_STREAM,
                )
                async for chunk in _stream_standalone_assistant_response(
                    db=db,
                    chatroom_id=chatroom_id,
                    user_message=effective_user_content,
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
                    run_kind=RunKind.MULTI_AGENT_ORCHESTRATION_STREAM,
                    summary="",
                    project_id=project.id,
                    runner_policy=prepared_orchestration.runner_policy,
                    extra_payload={"agents": mentioned_names},
                )
                _record_target_agents_selected(
                    db,
                    task_run,
                    agent_names=mentioned_names,
                    client_turn_id=message.client_turn_id,
                    project_id=project.id,
                    run_kind=RunKind.MULTI_AGENT_ORCHESTRATION_STREAM,
                )
                async for chunk in _stream_multi_agent_orchestration(
                    db=db,
                    chatroom=chatroom,
                    project=project,
                    agents=agents,
                    agent_names=mentioned_names,
                    user_message=effective_user_content,
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
            target_agent_name = mentioned_names[0] if mentioned_names else (
                str(getattr(task_run, "target_agent_name", "") or "").strip() or None
            )

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
                    EventType.TASK_RUN_FAILED,
                    summary="No target agent resolved for streaming execution.",
                    payload={"project_id": project.id},
                )
                complete_task_run(db, task_run, status="failed", summary="No target agent resolved.")
                yield f"data: {_json.dumps({'type': 'error', 'error': 'No agent available'})}\n\n"
                return

            target_agent_label = agent_name_of(target_agent)
            active_agent_name = target_agent_label
            active_agent_id = target_agent.id
            available_tools = _resolve_agent_runtime_tools(target_agent)
            target_llm_client = get_llm_client_for_agent(_agent_type(target_agent))
            project_single_agent_stream_policy = _build_single_agent_runner_policy(
                run_kind=RunKind.PROJECT_SINGLE_AGENT_STREAM,
                agent_name=target_agent_label,
                project_id=project.id,
                tool_names=available_tools,
                streaming=True,
                standalone=False,
            )
            _select_task_run_runtime_mode(
                db,
                task_run,
                run_kind=RunKind.PROJECT_SINGLE_AGENT_STREAM,
                target_agent_name=target_agent_label,
                agent_name=target_agent_label,
                summary="",
                project_id=project.id,
                runner_policy=project_single_agent_stream_policy,
            )
            _record_target_agent_selected(
                db,
                task_run,
                agent_name=target_agent_label,
                model=getattr(target_llm_client, "model", None),
                tool_names=available_tools,
                client_turn_id=message.client_turn_id,
                project_id=project.id,
                run_kind=RunKind.PROJECT_SINGLE_AGENT_STREAM,
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
                summary="",
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
                    user_message=effective_user_content,
                    available_tools=runtime.available_tools,
                    tool_policy_pack=runtime.tool_policy_pack,
                    history_limit=6,
                    runtime_context=build_runtime_environment_context(project),
                    turn_state=current_turn_state,
                    on_compaction=compaction_callback,
                )

            async def _execute_single_agent_stream_tool(tool_name, tool_args, tool_args_str, tool_call_id, tool_index, turn_index):
                from tools import tool_registry

                async def emit_tool_progress(progress: dict[str, Any]) -> None:
                    await store_runtime_card(
                        chatroom.id,
                        {
                            "type": "tool_call",
                            "source": "chatroom",
                            "agent": target_agent_label,
                            "tool": tool_name,
                            "arguments": tool_args_str,
                            "success": None,
                            "status": "running",
                            "blocked": False,
                            "result": str(progress.get("tail_output") or "").strip() or "Tool is running.",
                            "duration_ms": progress.get("duration_ms"),
                            "pid": progress.get("pid"),
                            "tracked_process": progress.get("tracked_process"),
                            "tool_call_index": tool_index,
                            "tool_call_id": tool_call_id,
                            "client_turn_id": message.client_turn_id,
                            "run_id": getattr(task_run, "id", None) if task_run is not None else None,
                            "turn": turn_index,
                        },
                    )

                return await tool_registry.execute(
                    tool_name,
                    **tool_args,
                    **runtime.runtime_kwargs,
                    task_run_id=getattr(task_run, "id", None) if task_run is not None else None,
                    client_turn_id=message.client_turn_id,
                    tool_call_id=tool_call_id,
                    turn=turn_index,
                    progress_callback=emit_tool_progress if tool_name == "run_shell" else None,
                )

            async def _on_single_agent_stream_tool_round(frame, normalized_tool_calls, tool_results, current_turn_state):
                blocked_tool_result = getattr(frame, "blocked_tool_result", None)
                record_runner_tool_round(
                    db,
                    task_run,
                    agent_name=target_agent_label,
                    turn=frame.turn_index,
                    tool_names=[
                        tool_call["function"]["name"]
                        for tool_call in (
                            normalized_tool_calls
                            + ([{
                                "function": {"name": blocked_tool_result.tool_name},
                            }] if blocked_tool_result is not None else [])
                        )
                    ],
                    tool_results=tool_results,
                    blocked_tool_results=[blocked_tool_result] if blocked_tool_result is not None else None,
                    summary=f"{target_agent_label} completed a streaming tool round.",
                    assistant_content=frame.llm_content,
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
                user_message=effective_user_content,
                save_message=chatroom_manager.send_message,
                publish_message=publish_saved_chat_message,
                record_turn_completed=record_agent_turn_completed,
                message_metadata=_message_metadata_with_turn,
                compact_summary=lambda content: _compact_runtime_text(content, limit=280),
                completion_summary="",
                failure_summary=lambda error: f"Streaming execution failed: {error}",
                extract_memories=extract_agent_memories,
                stream_failure_message_metadata=_message_metadata_with_turn,
                post_publish_success=_build_assistant_auto_handoff_callback(chatroom_id=chatroom_id, source_agent_name=target_agent_label),
            )

            async for outcome in iter_managed_single_agent_stream_runtime_profile(
                build_single_agent_stream_chat_profile(
                    runtime_inputs=project_single_agent_stream_runtime_inputs,
                    llm_client=runtime.llm_client,
                    tools=runtime.tool_schemas,
                    turn_state=runtime.turn_state,
                    loop_callbacks=build_single_agent_stream_loop_callbacks(
                        assemble_messages=_assemble_single_agent_stream_messages,
                        execute_tool=_execute_single_agent_stream_tool,
                        build_llm_runtime_card=_build_single_agent_stream_llm_card,
                        snapshot_messages=_snapshot_llm_messages,
                        preview_tool_calls=_preview_tool_calls,
                        format_prompt_messages=_format_json_block,
                        tool_result_success=_tool_result_succeeded,
                        before_event=chain_before_event_callbacks(
                            _build_llm_fact_recorder(
                                db,
                                task_run,
                                agent_name=target_agent_label,
                                llm_client=runtime.llm_client,
                                client_turn_id=message.client_turn_id,
                            ),
                            make_stream_audit_before_event(
                                db=db,
                                run_id=getattr(task_run, "id", None),
                                stage_id=None,
                                agent_name=target_agent_label,
                            ),
                        ),
                        on_tool_round=_on_single_agent_stream_tool_round,
                    ),
                    transport=build_single_agent_stream_transport_context(
                        serialize_payload=lambda payload: _json.dumps(payload, ensure_ascii=False),
                        store_runtime_card=store_runtime_card,
                        public_runtime_card_payload=public_runtime_card_payload,
                    ),
                    max_turns=MAX_TOOL_ITERATIONS,
                    stream_failure=build_single_agent_stream_failure_policy(
                        failure_agent_name=active_agent_name or default_agent_name(DEFAULT_AGENT_TYPE),
                        failure_agent_id=active_agent_id,
                        detail_builder=traceback.format_exc,
                    ),
                )
            ):
                if outcome.chunk is not None:
                    yield outcome.chunk

        except Exception as persist_exc:
            logger.error(f"[SSE] Failed to drive streaming session: {persist_exc}")
            traceback.print_exc()
            yield render_sse_payload({"type": "error", "error": str(persist_exc)}, serialize_payload=lambda payload: _json.dumps(payload))
        finally:
            # P0-2: Terminalize TaskRun if still running (SSE disconnect / exception).
            if task_run is not None:
                try:
                    task_run = get_task_run(db, task_run.id)
                    if task_run and (task_run.status or "").strip().lower() == "running":
                        terminalize_task_run(
                            db, task_run,
                            status="failed",
                            summary="Stream interrupted before completion.",
                            event_type=EventType.TASK_RUN_INTERRUPTED,
                        )
                except Exception as cleanup_exc:
                    logger.warning(f"[SSE] Failed to terminalize task_run during cleanup: {cleanup_exc}")
            if workspace_token is not None:
                try:
                    reset_active_workspace(workspace_token)
                except ValueError:
                    logger.debug("[SSE] Active workspace context was already detached during stream shutdown.")
            db.close()

    async def event_generator():
        queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=256)
        client_connected = True

        async def producer():
            nonlocal client_connected, stream_failed, stream_error
            try:
                async for chunk in raw_event_generator():
                    if runtime_is_shutting_down() or await request.is_disconnected():
                        client_connected = False
                        break
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
            except asyncio.CancelledError:
                raise
            finally:
                if client_connected:
                    try:
                        queue.put_nowait(None)
                    except asyncio.QueueFull:
                        pass

        producer_task = asyncio.create_task(producer())

        try:
            while True:
                if runtime_is_shutting_down() or await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                if item is None:
                    break
                yield item
        except asyncio.CancelledError:
            client_connected = False
            raise
        finally:
            client_connected = False
            if not producer_task.done():
                producer_task.cancel()
                try:
                    await asyncio.wait_for(
                        asyncio.gather(producer_task, return_exceptions=True),
                        timeout=2.0,
                    )
                except asyncio.TimeoutError:
                    logger.warning("[SSE] Producer cancellation timed out during stream shutdown.")

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
    from tools import tool_registry

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
        "permissions": _effective_permissions_config(),
        "ui": _effective_ui_config(),
        "context": {
            "selector_profiles": effective_selector_profiles(),
            "default_selector_profiles": default_selector_profiles(),
        },
        "tools": {
            "tool_names": [],
            "tool_policies": [],
            "tool_policy_summary": {},
        },
        "skills_catalog": {},
        "agents": {},
        "agent_llm_configs": {}
    }

    # 从 agents.json 加载（唯一配置源）
    agents_config_file = Path(settings.AGENT_CONFIG_FILE)
    if agents_config_file.exists():
        try:
            with open(agents_config_file, 'r', encoding='utf-8-sig') as f:
                agents_config = json.load(f)

            # 全局 LLM 配置
            config["global_llm"] = agents_config.get("global_llm", {})
            config["orchestration"] = _effective_orchestration_config(agents_config)
            config["permissions"] = _effective_permissions_config(agents_config)
            config["ui"] = _effective_ui_config(agents_config)
            config["context"] = {
                "selector_profiles": effective_selector_profiles(agents_config),
                "default_selector_profiles": default_selector_profiles(),
            }

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
                agent_data["tools"] = canonical_tool_names(agent_data.get("tools"))
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

            available_tools = tool_registry.list_agent_tools()
            description_map = {
                tool_name: (tool_registry.get(tool_name).description if tool_registry.get(tool_name) else "")
                for tool_name in available_tools
            }
            config["tools"] = tool_registry.get_policy_pack(available_tools)
            config["system_tools"] = tool_registry.get_policy_pack(tool_registry.list_system_tools())
            for policy in config["tools"].get("tool_policies", []):
                if not policy.get("description"):
                    policy["description"] = description_map.get(policy.get("name", ""), "")
            config["agent_tools"] = {}
            for agent_name, agent_data in agents_data.items():
                effective_tool_names = resolve_agent_tool_names(
                    SimpleNamespace(tools=agent_data.get("tools")),
                    available_tools,
                )
                config["agent_tools"][agent_name] = tool_registry.get_policy_pack(effective_tool_names)
                for policy in config["agent_tools"][agent_name].get("tool_policies", []):
                    if not policy.get("description"):
                        policy["description"] = description_map.get(policy.get("name", ""), "")

            skills_config_file = Path(settings.SKILLS_CONFIG_FILE)
            if skills_config_file.exists():
                with open(skills_config_file, "r", encoding="utf-8-sig") as f:
                    skills_config = json.load(f)
                config["skills_catalog"] = skills_config if isinstance(skills_config, dict) else {}
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
            with open(config_file, 'r', encoding='utf-8-sig') as f:
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
            with open(config_file, 'r', encoding='utf-8-sig') as f:
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


@router.put("/config/permissions")
async def update_permissions_config(config: PermissionsConfigModel):
    """
    Update runtime permission policy config stored in agents.json.

    Request body:
    {
        "allow_read_only_tools_without_approval": true,
        "auto_approve_all": false,
        "remember_default_scope": "project",
        "remember_default_matcher": "command_fingerprint"
    }
    """
    from pathlib import Path

    config_file = Path(settings.AGENT_CONFIG_FILE)
    try:
        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8-sig') as f:
                data = json.load(f)
        else:
            data = {"agents": {}}

        data["permissions"] = config.model_dump()
        config_file.parent.mkdir(parents=True, exist_ok=True)

        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        return {"message": "Permissions config updated", "permissions": data["permissions"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update permissions config: {e}")


@router.put("/config/context")
async def update_context_config(config: ContextConfigModel):
    """
    Update runtime context-selector limits stored in agents.json.

    Request body:
    {
        "selector_profiles": {
            "chat_interactive": {
                "max_fragments": 12,
                "max_tokens_cap": 16000,
                "max_tokens_by_role": {"developer": 5000, "user": 11000},
                "max_tokens_by_scope": {"run": 9000, "turn": 2400}
            }
        }
    }
    """
    config_file = Path(settings.AGENT_CONFIG_FILE)
    try:
        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8-sig') as f:
                data = json.load(f)
        else:
            data = {"agents": {}}

        data["context"] = config.model_dump()
        config_file.parent.mkdir(parents=True, exist_ok=True)

        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        return {
            "message": "Context config updated",
            "context": {
                "selector_profiles": effective_selector_profiles(data),
                "default_selector_profiles": default_selector_profiles(),
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update context config: {e}")


@router.put("/config/ui")
async def update_ui_config(config: UiConfigModel):
    """Update runtime UI preferences stored in agents.json."""

    config_file = Path(settings.AGENT_CONFIG_FILE)
    try:
        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8-sig') as f:
                data = json.load(f)
        else:
            data = {"agents": {}}

        data["ui"] = config.model_dump()
        config_file.parent.mkdir(parents=True, exist_ok=True)
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        return {
            "message": "UI config updated",
            "ui": data["ui"],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update UI config: {e}")


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
            with open(config_file, 'r', encoding='utf-8-sig') as f:
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
                agents[agent_name][field_name] = canonical_tool_names(config[field_name]) if field_name == "tools" else config[field_name]

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
    for name in tool_registry.list_agent_tools():
        tool = tool_registry.get(name)
        if tool:
            tools.append({
                "name": tool.name,
                "description": tool.description,
                "system_only": bool(getattr(tool, "system_only", False)),
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
    status = get_runtime_collaboration_status()
    status["status"] = "active"
    return status


@router.get("/collaboration/chatrooms/{chatroom_id}/status")
async def get_chatroom_collaboration_status(chatroom_id: int):
    """获取聊天室的协作状态"""
    return get_runtime_chatroom_collaboration_status(chatroom_id)


@router.get("/collaboration/tasks/{task_id}")
async def get_task_status(task_id: str):
    """获取任务状态"""
    try:
        return get_runtime_collaboration_task(task_id)
    except CollaborationTaskRuntimeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/collaboration/delegate")
async def delegate_task_to_agent(
    target_agent_name: str,
    task_title: str,
    task_description: str,
    chatroom_id: int,
    db: Session = Depends(get_db)
):
    """委托任务给指定 Agent"""
    task, _ = await delegate_runtime_collaboration_task(
        db=db,
        target_agent_name=target_agent_name,
        task_title=task_title,
        task_description=task_description,
        chatroom_id=chatroom_id,
        created_by_agent_id=0,
        current_agent_name="user",
    )
    if task is None:
        normalized_target = normalize_agent_type(target_agent_name)
        raise HTTPException(status_code=404, detail=f"Agent '{normalized_target}' not found")
    
    return {
        "task_id": task.id,
        "status": "delegated",
        "assigned_to": (
            task.metadata.get("target_agent_name")
            if isinstance(task.metadata, dict) and task.metadata.get("target_agent_name")
            else normalize_agent_type(target_agent_name)
        )
    }


@router.get("/collaboration/tasks")
async def list_collaboration_tasks(chatroom_id: Optional[int] = None):
    """列出协作任务"""
    tasks = list_runtime_collaboration_tasks(chatroom_id=chatroom_id)

    return {
        "tasks": tasks,
        "count": len(tasks)
    }


# ---------------------------------------------------------------------------
# Choice Box API
# ---------------------------------------------------------------------------

@router.get("/choice-boxes")
async def list_choice_boxes(chatroom_id: Optional[int] = None):
    """List pending choice boxes, optionally filtered by chatroom."""
    from services.choice_box import get_choice_box_store
    store = get_choice_box_store()
    if chatroom_id is not None:
        boxes = store.pending_for_chatroom(chatroom_id)
    else:
        boxes = store.pending_boxes
    return {
        "choice_boxes": [b.to_dict() for b in boxes],
        "count": len(boxes),
    }


@router.get("/choice-boxes/{box_id}")
async def get_choice_box(box_id: str):
    """Get a specific choice box by ID."""
    from services.choice_box import get_choice_box_store
    store = get_choice_box_store()
    box = store.get(box_id)
    if not box:
        raise HTTPException(status_code=404, detail=f"Choice box '{box_id}' not found")
    return box.to_dict()


@router.post("/choice-boxes/{box_id}/respond")
async def respond_to_choice_box(
    box_id: str,
    value: str = Body(..., embed=True),
):
    """Respond to a choice box."""
    from services.choice_box import get_choice_box_store, ChoiceBoxStatus
    store = get_choice_box_store()
    box = store.respond(box_id, value)
    if not box:
        raise HTTPException(status_code=404, detail=f"Choice box '{box_id}' not found or already responded")

    # Publish the response as a chat message
    try:
        from services.chat_publish import publish_saved_chat_message
        from models.database import get_db as _get_db, Message as _Message
        db = next(_get_db())
        try:
            response_content = f"✅ 选择了: {value}"
            msg = _Message(
                chatroom_id=0,  # Will be resolved from context
                content=response_content,
                message_type="choice_response",
                metadata_json=json.dumps({
                    "choice_box_id": box_id,
                    "choice_value": value,
                    "source_agent": box.source_agent,
                }),
            )
            db.add(msg)
            db.commit()
        finally:
            db.close()
    except Exception as exc:
        logger.warning("[ChoiceBox] Failed to publish response: %s", exc)

    # Execute registered action handler (e.g., knowledge graph build on approval)
    action_handler = store.pop_action_handler(box_id)
    if action_handler and value in ("approve", "confirm", "allow_once", "allow_stage"):
        try:
            import asyncio
            if asyncio.iscoroutinefunction(action_handler):
                asyncio.create_task(action_handler())
            else:
                action_handler()
        except Exception as exc:
            logger.warning("[ChoiceBox] Action handler failed for %s: %s", box_id, exc)

    return box.to_dict()


@router.post("/choice-boxes/{box_id}/cancel")
async def cancel_choice_box(box_id: str):
    """Cancel a pending choice box."""
    from services.choice_box import get_choice_box_store
    store = get_choice_box_store()
    box = store.cancel(box_id)
    if not box:
        raise HTTPException(status_code=404, detail=f"Choice box '{box_id}' not found")
    return box.to_dict()


# ==================== Sleep Scheduler ====================

@router.get("/sleep-scheduler/status")
async def get_sleep_scheduler_status():
    """Get the current status of the sleep memory consolidation scheduler."""
    from services.sleep_scheduler import get_scheduler_status
    return get_scheduler_status()


@router.post("/sleep-scheduler/trigger")
async def trigger_sleep_consolidation(
    agent_type: Optional[str] = Body(None, embed=True),
):
    """Manually trigger memory consolidation for one or all agents."""
    from services.sleep_scheduler import trigger_consolidation
    return await trigger_consolidation(agent_type)


# ==================== Command System ====================

@router.get("/commands")
async def list_commands():
    """List all available chat commands for autocomplete."""
    from commands.executor import get_command_definitions
    return {"commands": get_command_definitions()}


@router.post("/commands/execute")
async def execute_chat_command(
    command: str = Body(..., embed=True),
    chatroom_id: Optional[int] = Body(None, embed=True),
    project_id: Optional[int] = Body(None, embed=True),
    db: Session = Depends(get_db),
):
    """Execute a read-only chat command."""
    from commands.executor import execute_command
    result = await execute_command(
        command,
        db=db,
        chatroom_id=chatroom_id,
        project_id=project_id,
    )
    return result


@router.get("/chat/history/{chatroom_id}")
async def get_chat_input_history(chatroom_id: int):
    """Get input history for a chatroom (for ↑/↓ recall)."""
    from pathlib import Path
    history_dir = Path.home() / ".catown" / "chat_history"
    history_file = history_dir / f"{chatroom_id}.json"

    if not history_file.exists():
        return {"chatroom_id": chatroom_id, "history": []}

    try:
        with open(history_file, "r", encoding="utf-8") as f:
            history = json.load(f)
        return {"chatroom_id": chatroom_id, "history": history[-50:]}
    except Exception:
        return {"chatroom_id": chatroom_id, "history": []}


@router.post("/chat/history/{chatroom_id}")
async def save_chat_input_history(
    chatroom_id: int,
    entry: str = Body(..., embed=True),
):
    """Save a message to input history (called after user sends)."""
    from pathlib import Path

    if not entry or not entry.strip():
        return {"ok": True}

    history_dir = Path.home() / ".catown" / "chat_history"
    history_dir.mkdir(parents=True, exist_ok=True)
    history_file = history_dir / f"{chatroom_id}.json"

    history = []
    if history_file.exists():
        try:
            with open(history_file, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = []

    # Avoid consecutive duplicates
    if not history or history[-1] != entry.strip():
        history.append(entry.strip())

    # Keep last 50
    history = history[-50:]

    with open(history_file, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False)

    return {"ok": True}
