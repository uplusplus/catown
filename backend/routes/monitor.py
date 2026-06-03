# -*- coding: utf-8 -*-
"""Catown backstage monitoring routes."""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import Integer, String, case, cast, desc, distinct, func, or_
from sqlalchemy.orm import Session

from models.audit import Event, LLMCall, MonitorNetworkRecord, ToolCall
from models.database import (
    ApprovalAuditLog,
    ApprovalQueueItem,
    Agent,
    Chatroom,
    Message,
    PipelineRun,
    PipelineStage,
    Project,
    RuntimeCardProjection,
    StageArtifact,
    TaskRun,
    TaskRunEvent,
    get_db,
    get_telemetry_db,
)
from services.agent_lifecycle_runtime import get_runtime_collaboration_status
from monitoring import monitor_log_buffer, monitor_network_buffer
from services.approval_audit import list_approval_audit_logs, serialize_approval_audit_log
from services.approval_queue import list_approval_queue_items
from services.context_optimization_evaluation import evaluate_context_budget_event_observations
from services.monitor_projection import (
    get_runtime_card_projection_health,
    serialize_monitor_approval_queue_item,
    serialize_monitor_context_budget_item,
    serialize_monitor_provider_compaction_item,
    serialize_monitor_runtime_detail,
    serialize_monitor_runtime_item,
)
from services.run_ledger import serialize_monitor_task_run_summary
from services.run_shell_processes import list_tracked_run_shell_processes

router = APIRouter(prefix="/api/monitor", tags=["monitor"])

INPUT_PRICE_PER_1K = 0.03
OUTPUT_PRICE_PER_1K = 0.06
LOG_STREAM_POLL_INTERVAL = 0.75
LOG_STREAM_LIMIT = 200
NETWORK_SCAN_LIMIT = 2000
USAGE_RANGES = {"1h", "6h", "24h", "7d", "30d"}
FILE_MONITOR_TOOLS = {"read_file", "write_file", "list_files", "delete_file", "search_files"}
FILE_MONITOR_ACTIONS = {
    "read_file": "read",
    "write_file": "write",
    "list_files": "list",
    "delete_file": "delete",
    "search_files": "search",
}


def _projection_health_payload(db: Session) -> dict[str, Any]:
    health = get_runtime_card_projection_health(db)
    return {
        **health,
        "status": "lagging" if health["missing"] > 0 else "healthy",
    }
TASK_RUN_STEP_SCAN_MULTIPLIER = 4
TASK_RUN_STEP_SCAN_MIN = 200
TASK_RUN_STEP_SCAN_MAX = 800
OVERVIEW_ACTIVITY_RUNTIME_LIMIT = 80
OVERVIEW_ACTIVITY_MESSAGE_LIMIT = 40
OVERVIEW_ACTIVITY_CONTEXT_BUDGET_LIMIT = 16
CONTEXT_BUDGET_EVENT_TYPE = "context_budget_event"
SEMANTIC_COMPACTION_EVENT_TYPE = "context_compaction"

monitor_log_buffer.install()


def _normalize_network_category(category: str | None) -> str:
    normalized = (category or "all").strip().lower()
    if normalized in {"frontend-backend", "frontend_backend", "fe-be"}:
        return "frontend_backend"
    if normalized in {"backend-llm", "backend_llm", "be-llm"}:
        return "backend_llm"
    if normalized in {"backend-other", "backend_other", "be-other"}:
        return "backend_other"
    if normalized in {"frontend-other", "frontend_other", "fe-other"}:
        return "frontend_other"
    return normalized


def _parse_metadata(metadata_json: str | None) -> dict[str, Any]:
    if not metadata_json:
        return {}
    try:
        payload = json.loads(metadata_json)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _is_monitor_page_network(entry: dict[str, Any]) -> bool:
    path = str(entry.get("path") or "").lower()
    url = str(entry.get("url") or "").lower()
    client_source = str(entry.get("client_source") or "").lower()
    from_entity = str(entry.get("from_entity") or "").lower()
    return (
        client_source == "monitor"
        or "frontend (monitor)" in from_entity
        or path.startswith("/api/monitor")
        or path == "/monitor"
        or path == "/monitor/"
        or path.endswith("/monitor.html")
        or "/monitor" in url
        or "/api/monitor" in url
    )


def _is_frontend_backend_heartbeat(entry: dict[str, Any]) -> bool:
    protocol = str(entry.get("protocol") or "").lower()
    raw_response = str(entry.get("raw_response") or "").strip()
    preview = str(entry.get("preview") or "").lower()
    path = str(entry.get("path") or "").lower()

    if "http" not in protocol:
        return False

    normalized_raw_response = raw_response.replace("\r", "")
    if raw_response and normalized_raw_response and all(
        line.strip() == ": ping" for line in normalized_raw_response.splitlines() if line.strip()
    ):
        return True

    if (
        (path.endswith("/stream") or " ping" in preview)
        and raw_response
        and '"type": "content"' not in raw_response
        and '"type":"content"' not in raw_response
        and '"type": "done"' not in raw_response
        and '"type":"done"' not in raw_response
        and (
            ": ping" in raw_response
            or '"type": "llm_wait"' in raw_response
            or '"type":"llm_wait"' in raw_response
            or '"type": "tool_wait"' in raw_response
            or '"type":"tool_wait"' in raw_response
        )
    ):
        return True

    return False


def _is_frontend_meta_request(entry: dict[str, Any]) -> bool:
    path = str(entry.get("path") or "").lower()
    url = str(entry.get("url") or "").lower()
    return path == "/api/frontend-meta" or "/api/frontend-meta" in url


def _is_frontend_backend_traffic(entry: dict[str, Any]) -> bool:
    return str(entry.get("category") or "").lower() == "frontend_backend"


def _is_legacy_backend_llm_app_event(entry: dict[str, Any]) -> bool:
    if str(entry.get("category") or "").lower() != "backend_llm":
        return False
    flow_kind = str(entry.get("flow_kind") or "").lower()
    metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
    frame_type = str(metadata.get("frame_type") or "").lower()
    if flow_kind == "llm_http":
        return False
    return flow_kind == "llm_stream" or frame_type in {
        "request_sent",
        "first_chunk",
        "first_content",
        "content",
        "tool_call_delta",
        "tool_call_ready",
        "done",
    }


def _include_network_entry(entry: dict[str, Any], *, include_internal: bool) -> bool:
    if not include_internal and (
        _is_frontend_backend_traffic(entry)
        or _is_monitor_page_network(entry)
        or _is_frontend_backend_heartbeat(entry)
        or _is_frontend_meta_request(entry)
    ):
        return False
    if _is_legacy_backend_llm_app_event(entry):
        return False
    return bool(entry.get("aggregated") is False or not entry.get("flow_id"))


def is_internal_network_entry(entry: dict[str, Any]) -> bool:
    return bool(
        _is_frontend_backend_traffic(entry)
        or _is_monitor_page_network(entry)
        or _is_frontend_backend_heartbeat(entry)
        or _is_frontend_meta_request(entry)
    )


def _persistable_network_entry(entry: dict[str, Any]) -> bool:
    if is_internal_network_entry(entry):
        return False
    return not _is_legacy_backend_llm_app_event(entry)


def _network_visibility_conditions(*, include_internal: bool):
    metadata_json = MonitorNetworkRecord.metadata_json
    flow_id_expr = func.json_extract(metadata_json, "$.flow_id")
    aggregated_expr = func.json_extract(metadata_json, "$.aggregated")
    visibility_filters = [
        or_(flow_id_expr.is_(None), flow_id_expr == "", aggregated_expr == 0, aggregated_expr == "false"),
    ]
    if not include_internal:
        visibility_filters.extend(
            [
                MonitorNetworkRecord.category != "frontend_backend",
                ~MonitorNetworkRecord.path.like("/api/monitor%"),
                MonitorNetworkRecord.path.notin_(["/monitor", "/monitor/", "/api/frontend-meta"]),
                ~MonitorNetworkRecord.url.like("%/monitor%"),
                ~MonitorNetworkRecord.url.like("%/api/monitor%"),
                ~MonitorNetworkRecord.url.like("%/api/frontend-meta%"),
                MonitorNetworkRecord.client_source != "monitor",
                ~func.lower(MonitorNetworkRecord.from_entity).like("%frontend (monitor)%"),
            ]
        )
    return visibility_filters


def _filter_network_entries(entries: list[dict[str, Any]], *, include_internal: bool, limit: int) -> list[dict[str, Any]]:
    visible = [entry for entry in entries if _include_network_entry(entry, include_internal=include_internal)]
    return visible[:limit] if limit > 0 else visible


def _compact_preview(value: Any, limit: int = 220) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except TypeError:
            text = str(value)
    compact = " ".join(text.strip().split())
    return compact[:limit]


def _parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _file_monitor_tool_path(tool_name: str, arguments: dict[str, Any]) -> str:
    if tool_name in {"read_file", "write_file", "delete_file"}:
        return str(arguments.get("file_path") or arguments.get("path") or "")
    if tool_name == "list_files":
        directory = str(arguments.get("directory") or ".")
        pattern = str(arguments.get("pattern") or "*")
        return directory if pattern == "*" else f"{directory.rstrip('/')}/{pattern}"
    if tool_name == "search_files":
        directory = str(arguments.get("directory") or ".")
        pattern = str(arguments.get("file_pattern") or "*")
        return directory if pattern == "*" else f"{directory.rstrip('/')}/{pattern}"
    return str(arguments.get("file_path") or arguments.get("path") or "")


def _file_monitor_result_size(result: Any) -> int | None:
    if isinstance(result, str):
        return len(result)
    if result is None:
        return None
    try:
        return len(json.dumps(result, ensure_ascii=False))
    except TypeError:
        return len(str(result))


def _file_monitor_item_from_runtime_card(
    *,
    message: Message,
    chatroom: Chatroom,
    project: Project | None,
    card: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any] | None:
    if str(card.get("type") or "") != "tool_call":
        return None
    tool_name = str(card.get("tool") or "")
    if tool_name not in FILE_MONITOR_TOOLS:
        return None
    arguments = _parse_json_object(card.get("arguments"))
    file_path = _file_monitor_tool_path(tool_name, arguments)
    return {
        "id": f"runtime-{message.id}",
        "source": "runtime_card",
        "runtime_message_id": message.id,
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "agent": card.get("agent") or card.get("from_agent") or "agent",
        "tool_name": tool_name,
        "action": FILE_MONITOR_ACTIONS.get(tool_name, "access"),
        "file_path": file_path,
        "project_id": project.id if project else None,
        "project_name": project.name if project else None,
        "chatroom_id": chatroom.id,
        "chat_title": chatroom.title,
        "success": card.get("success"),
        "status": card.get("status"),
        "blocked": card.get("blocked"),
        "duration_ms": int(card.get("duration_ms") or 0),
        "turn": int(card.get("turn") or 0) or None,
        "client_turn_id": _metadata_client_turn_id(metadata),
        "arguments": arguments,
        "arguments_preview": _compact_preview(card.get("arguments")),
        "result_preview": _compact_preview(card.get("result"), limit=320),
        "result_size": _file_monitor_result_size(card.get("result")),
    }


def _file_monitor_item_from_projection(
    *,
    proj: RuntimeCardProjection,
    chatroom: Chatroom,
    project: Project | None,
    card: dict[str, Any],
) -> dict[str, Any] | None:
    tool_name = proj.tool_name or str(card.get("tool") or "")
    if tool_name not in FILE_MONITOR_TOOLS:
        return None
    arguments = _parse_json_object(card.get("arguments"))
    file_path = _file_monitor_tool_path(tool_name, arguments)
    return {
        "id": f"runtime-{proj.message_id}",
        "source": "runtime_card",
        "runtime_message_id": proj.message_id,
        "created_at": proj.created_at.isoformat() if proj.created_at else None,
        "agent": proj.agent_name or card.get("agent") or card.get("from_agent") or "agent",
        "tool_name": tool_name,
        "action": FILE_MONITOR_ACTIONS.get(tool_name, "access"),
        "file_path": file_path,
        "project_id": project.id if project else None,
        "project_name": project.name if project else None,
        "chatroom_id": proj.chatroom_id,
        "chat_title": chatroom.title,
        "success": proj.success if proj.success is not None else card.get("success"),
        "status": card.get("status"),
        "blocked": card.get("blocked"),
        "duration_ms": proj.duration_ms or int(card.get("duration_ms") or 0),
        "turn": proj.turn or int(card.get("turn") or 0) or None,
        "client_turn_id": card.get("client_turn_id") if isinstance(card.get("client_turn_id"), str) else None,
        "arguments": arguments,
        "arguments_preview": _compact_preview(card.get("arguments")),
        "result_preview": _compact_preview(card.get("result"), limit=320),
        "result_size": _file_monitor_result_size(card.get("result")),
    }


def _file_monitor_agent_sql_expr():
    return func.coalesce(
        RuntimeCardProjection.agent_name,
        func.json_extract(RuntimeCardProjection.card_json, "$.agent"),
        func.json_extract(RuntimeCardProjection.card_json, "$.from_agent"),
        "agent",
    )


def _file_monitor_path_sql_expr():
    arguments_expr = func.coalesce(func.json_extract(RuntimeCardProjection.card_json, "$.arguments"), "{}")
    base_path_expr = func.coalesce(
        func.json_extract(arguments_expr, "$.file_path"),
        func.json_extract(arguments_expr, "$.path"),
        "",
    )
    directory_expr = func.coalesce(func.json_extract(arguments_expr, "$.directory"), ".")
    list_pattern_expr = func.coalesce(func.json_extract(arguments_expr, "$.pattern"), "*")
    search_pattern_expr = func.coalesce(func.json_extract(arguments_expr, "$.file_pattern"), "*")
    list_path_expr = case(
        (list_pattern_expr == "*", directory_expr),
        else_=func.rtrim(directory_expr, "/").op("||")("/").op("||")(list_pattern_expr),
    )
    search_path_expr = case(
        (search_pattern_expr == "*", directory_expr),
        else_=func.rtrim(directory_expr, "/").op("||")("/").op("||")(search_pattern_expr),
    )
    return case(
        (RuntimeCardProjection.tool_name.in_(["read_file", "write_file", "delete_file"]), base_path_expr),
        (RuntimeCardProjection.tool_name == "list_files", list_path_expr),
        (RuntimeCardProjection.tool_name == "search_files", search_path_expr),
        else_=base_path_expr,
    )


def _file_monitor_projection_query(
    db: Session,
    *,
    normalized_tool: str,
    query_text: str,
):
    proj_filters = [
        RuntimeCardProjection.card_type == "tool_call",
        RuntimeCardProjection.tool_name.in_(sorted(FILE_MONITOR_TOOLS)),
        RuntimeCardProjection.card_json.isnot(None),
        RuntimeCardProjection.card_json != "",
        func.json_valid(RuntimeCardProjection.card_json) == 1,
    ]
    if normalized_tool != "all":
        proj_filters.append(RuntimeCardProjection.tool_name == normalized_tool)

    projections = (
        db.query(RuntimeCardProjection, Chatroom, Project)
        .join(Chatroom, RuntimeCardProjection.chatroom_id == Chatroom.id)
        .outerjoin(Project, Chatroom.project_id == Project.id)
        .filter(*proj_filters)
    )
    if query_text:
        like_pattern = f"%{query_text}%"
        projections = projections.filter(
            or_(
                func.lower(func.coalesce(RuntimeCardProjection.agent_name, "")).like(like_pattern),
                func.lower(func.coalesce(RuntimeCardProjection.tool_name, "")).like(like_pattern),
                func.lower(func.coalesce(Chatroom.title, "")).like(like_pattern),
                func.lower(func.coalesce(Project.name, "")).like(like_pattern),
                func.lower(cast(RuntimeCardProjection.card_json, String)).like(like_pattern),
                func.lower(func.coalesce(RuntimeCardProjection.preview, "")).like(like_pattern),
                func.lower(func.coalesce(RuntimeCardProjection.response_preview, "")).like(like_pattern),
            )
        )
    return projections


def _metadata_client_turn_id(metadata: dict[str, Any]) -> str | None:
    client_turn_id = metadata.get("client_turn_id")
    if isinstance(client_turn_id, str) and client_turn_id:
        return client_turn_id

    card = metadata.get("card")
    if isinstance(card, dict):
        card_turn_id = card.get("client_turn_id")
        if isinstance(card_turn_id, str) and card_turn_id:
            return card_turn_id
    return None


def _runtime_entities(card: dict[str, Any]) -> tuple[str | None, str | None]:
    card_type = str(card.get("type") or "runtime")
    agent = card.get("agent")
    from_agent = card.get("from_agent")
    to_agent = card.get("to_agent")

    if card_type == "llm_call":
        return (str(agent) if agent else "agent", "LLM")
    if card_type == "tool_call":
        return (str(agent) if agent else "agent", str(card.get("tool") or "tool"))
    if card_type == "agent_message":
        return (str(from_agent) if from_agent else "agent", str(to_agent) if to_agent else "team")
    if card_type == "boss_instruction":
        return ("Boss", str(agent) if agent else "agent")
    return (str(agent) if agent else None, None)


def _extract_prompt_preview(card: dict[str, Any]) -> str:
    prompt_messages = card.get("prompt_messages")
    if not isinstance(prompt_messages, str) or not prompt_messages.strip():
        return ""

    try:
        parsed = json.loads(prompt_messages)
    except json.JSONDecodeError:
        return _compact_preview(prompt_messages)

    if isinstance(parsed, list):
        for message in reversed(parsed):
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return _compact_preview(content)
            if isinstance(content, list):
                chunks: list[str] = []
                for chunk in content:
                    if not isinstance(chunk, dict):
                        continue
                    text = chunk.get("text")
                    if isinstance(text, str) and text.strip():
                        chunks.append(text.strip())
                if chunks:
                    return _compact_preview(" ".join(chunks))

    return _compact_preview(parsed)



def _build_runtime_title(card: dict[str, Any]) -> str:
    card_type = str(card.get("type") or "runtime")
    agent = str(card.get("agent") or card.get("from_agent") or "system")

    if card_type == "llm_call":
        return f"{agent} -> LLM"
    if card_type == "tool_call":
        tool_name = str(card.get("tool") or "tool")
        return f"{agent} used {tool_name}"
    if card_type == "consult_call":
        target_agent = str(card.get("target_agent") or "agent")
        return f"{agent} consulted {target_agent}"
    if card_type == "agent_error":
        return f"{agent} stream failed"
    if card_type == "stage_started":
        return f"{agent} started {card.get('display_name') or card.get('stage') or 'stage'}"
    if card_type == "stage_completed":
        return f"{agent} finished {card.get('display_name') or card.get('stage') or 'stage'}"
    if card_type == "gate_blocked":
        return f"Gate blocked at {card.get('stage') or 'pipeline'}"
    if card_type == "gate_rejected":
        return f"Gate rejected by {agent}"
    if card_type == "gate_approved":
        return f"Gate approved by {agent}"
    if card_type == "skill_inject":
        return f"{agent} loaded skills"
    if card_type == "agent_message":
        to_agent = card.get("to_agent")
        if to_agent:
            return f"{agent} -> {to_agent}"
        return f"Agent message from {agent}"
    if card_type == "boss_instruction":
        return f"Boss instruction for {agent}"
    return f"{agent} / {card_type}"



def _build_runtime_preview(card: dict[str, Any]) -> str:
    candidates = [
        card.get("summary_text"),
        card.get("error"),
        card.get("response_preview"),
        card.get("response"),
        card.get("result"),
        card.get("content_preview"),
        card.get("content"),
        card.get("summary"),
        card.get("arguments"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            compact = " ".join(candidate.strip().split())
            return compact[:240]
    return ""


def _normalize_log_level(level: str | None) -> str:
    normalized = (level or "all").strip().lower()
    if normalized == "warn":
        return "warning"
    return normalized


def _runtime_card_token_usage(card: dict[str, Any]) -> tuple[int, int]:
    return int(card.get("tokens_in") or 0), int(card.get("tokens_out") or 0)


def _runtime_card_cost(tokens_in: int, tokens_out: int) -> float:
    return (tokens_in / 1000 * INPUT_PRICE_PER_1K) + (tokens_out / 1000 * OUTPUT_PRICE_PER_1K)


def _usage_bucket_boundaries(range_value: str) -> list[tuple[datetime, datetime, str]]:
    now = datetime.now()
    if range_value == "1h":
        bucket_count = 12
        minute = (now.minute // 5) * 5
        current = now.replace(minute=minute, second=0, microsecond=0)
        starts = [current - timedelta(minutes=5 * offset) for offset in reversed(range(bucket_count))]
        return [(start, start + timedelta(minutes=5), f"{start.minute:02d}") for start in starts]

    if range_value == "6h":
        bucket_count = 6
        current = now.replace(minute=0, second=0, microsecond=0)
        starts = [current - timedelta(hours=offset) for offset in reversed(range(bucket_count))]
        return [(start, start + timedelta(hours=1), f"{start.hour:02d}") for start in starts]

    if range_value == "24h":
        bucket_count = 24
        current = now.replace(minute=0, second=0, microsecond=0)
        starts = [current - timedelta(hours=offset) for offset in reversed(range(bucket_count))]
        return [(start, start + timedelta(hours=1), f"{start.hour:02d}") for start in starts]

    bucket_count = 7 if range_value == "7d" else 30
    current = now.replace(hour=0, minute=0, second=0, microsecond=0)
    starts = [current - timedelta(days=offset) for offset in reversed(range(bucket_count))]
    return [(start, start + timedelta(days=1), str(start.day)) for start in starts]


def _period_start(period: str) -> datetime:
    now = datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "day":
        return today
    if period == "week":
        return today - timedelta(days=today.weekday())
    return today.replace(day=1)


def _range_scan_start(range_value: str) -> datetime:
    return _usage_bucket_boundaries(range_value)[0][0]


def _empty_usage_totals() -> dict[str, Any]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "estimated_cost_usd": 0.0,
        "llm_calls": 0,
    }


def _add_usage(totals: dict[str, Any], tokens_in: int, tokens_out: int) -> None:
    totals["input_tokens"] += tokens_in
    totals["output_tokens"] += tokens_out
    totals["total_tokens"] += tokens_in + tokens_out
    totals["estimated_cost_usd"] += _runtime_card_cost(tokens_in, tokens_out)
    totals["llm_calls"] += 1


def _finalize_usage_totals(totals: dict[str, Any]) -> dict[str, Any]:
    return {
        **totals,
        "estimated_cost_usd": round(float(totals["estimated_cost_usd"]), 4),
    }


def _query_recent_runtime_activity(
    db: Session,
    *,
    runtime_limit: int,
    summary_window: int,
) -> tuple[list[dict[str, Any]], list[Message]]:
    projections = (
        db.query(RuntimeCardProjection, Chatroom, Project)
        .join(Chatroom, RuntimeCardProjection.chatroom_id == Chatroom.id)
        .outerjoin(Project, Chatroom.project_id == Project.id)
        .order_by(desc(RuntimeCardProjection.created_at), desc(RuntimeCardProjection.id))
        .limit(summary_window)
        .all()
    )

    recent_projections = projections[:runtime_limit]
    recent_runtime: list[dict[str, Any]] = []
    for proj, chatroom, project in recent_projections:
        card: dict[str, Any] | None = None
        if proj.card_json:
            try:
                parsed = json.loads(proj.card_json)
            except (json.JSONDecodeError, TypeError):
                parsed = None
            if isinstance(parsed, dict):
                card = parsed
        if card:
            recent_runtime.append(
                serialize_monitor_runtime_item(
                    runtime_message_id=proj.message_id,
                    chatroom_id=proj.chatroom_id,
                    chat_title=chatroom.title,
                    project_id=project.id if project else None,
                    project_name=project.name if project else None,
                    card=card,
                    created_at=proj.created_at,
                    metadata={"card": card},
                )
            )
            continue

        card_type = proj.card_type
        from_entity, to_entity = None, None
        if card_type == "llm_call":
            from_entity = proj.agent_name or "agent"
            to_entity = "LLM"
        elif card_type == "tool_call":
            from_entity = proj.agent_name or "agent"
            to_entity = proj.tool_name or "tool"

        recent_runtime.append({
            "id": proj.message_id,
            "type": proj.card_type,
            "title": proj.title or "",
            "operation_label": proj.tool_name or proj.card_type.replace("_", " "),
            "preview": proj.preview or "",
            "created_at": proj.created_at.isoformat() if proj.created_at else None,
            "chatroom_id": proj.chatroom_id,
            "chat_title": chatroom.title,
            "project_id": project.id if project else None,
            "project_name": project.name if project else None,
            "agent": proj.agent_name,
            "from_entity": from_entity,
            "to_entity": to_entity,
            "model": proj.model_name,
            "tool_name": proj.tool_name,
            "tool_call_id": None,
            "success": proj.success,
            "tokens_in": proj.tokens_in or 0,
            "tokens_out": proj.tokens_out or 0,
            "duration_ms": proj.duration_ms or 0,
            "turn": proj.turn,
            "client_turn_id": None,
            "prompt_preview": proj.prompt_preview or "",
            "response_preview": proj.response_preview or "",
            "arguments_preview": "",
            "stage": None,
            "brain_events": [],
        })

    return recent_runtime, []


def _query_recent_message_activity(
    db: Session,
    *,
    message_limit: int,
) -> list[dict[str, Any]]:
    recent_message_rows = (
        db.query(Message, Chatroom, Project)
        .join(Chatroom, Message.chatroom_id == Chatroom.id)
        .outerjoin(Project, Chatroom.project_id == Project.id)
        .filter(Message.message_type != "runtime_card")
        .order_by(desc(Message.created_at), desc(Message.id))
        .limit(message_limit)
        .all()
    )
    agent_name_by_id = {agent.id: agent.name for agent in db.query(Agent).all()}
    return [
        {
            "id": message.id,
            "chatroom_id": chatroom.id,
            "chat_title": chatroom.title,
            "project_id": project.id if project else None,
            "project_name": project.name if project else None,
            "agent_name": agent_name_by_id.get(message.agent_id),
            "content": message.content or "",
            "content_preview": " ".join((message.content or "").split())[:220],
            "message_type": message.message_type,
            "created_at": message.created_at.isoformat() if message.created_at else None,
            "client_turn_id": _metadata_client_turn_id(_parse_metadata(message.metadata_json)),
        }
        for message, chatroom, project in recent_message_rows
    ]


def _query_recent_context_budget_activity(
    db: Session,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    recent_context_budget_rows = (
        db.query(TaskRunEvent, TaskRun, Chatroom, Project)
        .join(TaskRun, TaskRunEvent.task_run_id == TaskRun.id)
        .join(Chatroom, TaskRun.chatroom_id == Chatroom.id)
        .outerjoin(Project, TaskRun.project_id == Project.id)
        .filter(TaskRunEvent.event_type == CONTEXT_BUDGET_EVENT_TYPE)
        .order_by(desc(TaskRunEvent.created_at), desc(TaskRunEvent.id))
        .limit(limit)
        .all()
    )
    return [
        serialize_monitor_context_budget_item(
            event,
            task_run=task_run,
            chat_title=chatroom.title,
            project_name=project.name if project else None,
        )
        for event, task_run, chatroom, project in recent_context_budget_rows
    ]


def _build_overview_usage_window(
    db: Session,
    *,
    range_value: str = "24h",
) -> dict[str, Any]:
    scan_start = _range_scan_start(range_value)

    # SQL aggregation instead of loading all rows into Python
    agent_rows = (
        db.query(
            RuntimeCardProjection.agent_name,
            RuntimeCardProjection.card_type,
            func.sum(RuntimeCardProjection.tokens_in).label("total_tokens_in"),
            func.sum(RuntimeCardProjection.tokens_out).label("total_tokens_out"),
            func.count().label("call_count"),
        )
        .filter(
            RuntimeCardProjection.created_at >= scan_start,
            RuntimeCardProjection.card_type.in_(["llm_call", "tool_call"]),
        )
        .group_by(RuntimeCardProjection.agent_name, RuntimeCardProjection.card_type)
        .all()
    )

    agent_summary: dict[str, dict[str, float]] = defaultdict(
        lambda: {"llm_calls": 0, "tool_calls": 0, "errors": 0, "token_input": 0, "token_output": 0}
    )
    llm_calls = 0
    tool_calls = 0
    input_tokens = 0
    output_tokens = 0

    for agent_name, card_type, total_in, total_out, count in agent_rows:
        name = str(agent_name or "system")
        if card_type == "llm_call":
            llm_calls += count
            input_tokens += int(total_in or 0)
            output_tokens += int(total_out or 0)
            agent_summary[name]["llm_calls"] += count
            agent_summary[name]["token_input"] += int(total_in or 0)
            agent_summary[name]["token_output"] += int(total_out or 0)
        elif card_type == "tool_call":
            tool_calls += count
            agent_summary[name]["tool_calls"] += count

    # Tool summary via SQL
    tool_rows = (
        db.query(
            RuntimeCardProjection.tool_name,
            func.count().label("call_count"),
            func.sum(RuntimeCardProjection.duration_ms).label("total_duration"),
            func.sum(
                func.cast(RuntimeCardProjection.success == False, Integer)
            ).label("failure_count"),
        )
        .filter(
            RuntimeCardProjection.created_at >= scan_start,
            RuntimeCardProjection.card_type == "tool_call",
            RuntimeCardProjection.tool_name.isnot(None),
        )
        .group_by(RuntimeCardProjection.tool_name)
        .order_by(desc("call_count"))
        .limit(8)
        .all()
    )

    top_tools = [
        {
            "tool_name": str(row.tool_name or "tool"),
            "call_count": int(row.call_count or 0),
            "failure_count": int(row.failure_count or 0),
            "avg_duration_ms": round(float(row.total_duration or 0) / row.call_count, 1) if row.call_count else 0,
        }
        for row in tool_rows
    ]

    provider_mode_summary: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "calls": 0,
            "state_reused": 0,
            "with_response_id": 0,
            "stateful_delta_calls": 0,
            "sent_input_items": 0,
            "omitted_input_items": 0,
            "sent_input_tokens": 0,
            "omitted_input_tokens": 0,
            "instruction_tokens": 0,
            "reported_input_tokens": 0,
            "reported_output_tokens": 0,
            "reported_total_tokens": 0,
            "first_chunk_ms_total": 0,
            "first_chunk_ms_count": 0,
            "first_content_ms_total": 0,
            "first_content_ms_count": 0,
            "completed_ms_total": 0,
            "completed_ms_count": 0,
        }
    )
    provider_rows = (
        db.query(RuntimeCardProjection.card_json)
        .filter(
            RuntimeCardProjection.created_at >= scan_start,
            RuntimeCardProjection.card_type == "llm_call",
            RuntimeCardProjection.card_json.isnot(None),
        )
        .all()
    )
    for (card_json,) in provider_rows:
        try:
            card = json.loads(card_json or "{}")
        except (json.JSONDecodeError, TypeError):
            card = {}
        if not isinstance(card, dict):
            continue
        provider_session = (
            card.get("provider_session")
            if isinstance(card.get("provider_session"), dict)
            else {}
        )
        provider_mode = str(
            card.get("provider_mode")
            or provider_session.get("provider_mode")
            or "chat_completions"
        ).strip() or "chat_completions"
        entry = provider_mode_summary[provider_mode]
        entry["calls"] += 1
        if provider_session.get("state_reused"):
            entry["state_reused"] += 1
        if provider_session.get("last_response_id") or provider_session.get("previous_response_id"):
            entry["with_response_id"] += 1
        provider_request = (
            card.get("provider_request")
            if isinstance(card.get("provider_request"), dict)
            else {}
        )
        if provider_request.get("stateful_delta"):
            entry["stateful_delta_calls"] += 1
        entry["sent_input_items"] += int(provider_request.get("sent_input_item_count") or 0)
        entry["omitted_input_items"] += int(provider_request.get("omitted_input_item_count") or 0)
        entry["sent_input_tokens"] += int(provider_request.get("estimated_sent_input_tokens") or 0)
        entry["omitted_input_tokens"] += int(provider_request.get("estimated_omitted_input_tokens") or 0)
        entry["instruction_tokens"] += int(provider_request.get("estimated_instruction_tokens") or 0)
        reported_input_tokens = int(card.get("tokens_in") or 0)
        reported_output_tokens = int(card.get("tokens_out") or 0)
        entry["reported_input_tokens"] += reported_input_tokens
        entry["reported_output_tokens"] += reported_output_tokens
        entry["reported_total_tokens"] += reported_input_tokens + reported_output_tokens
        timings = card.get("timings") if isinstance(card.get("timings"), dict) else {}
        for source_key, total_key, count_key in (
            ("first_chunk_ms", "first_chunk_ms_total", "first_chunk_ms_count"),
            ("first_content_ms", "first_content_ms_total", "first_content_ms_count"),
            ("completed_ms", "completed_ms_total", "completed_ms_count"),
        ):
            value = int(timings.get(source_key) or 0)
            if value > 0:
                entry[total_key] += value
                entry[count_key] += 1
    if not provider_mode_summary and llm_calls:
        provider_mode_summary["chat_completions"]["calls"] = int(llm_calls)

    # Error count via SQL
    error_count = (
        db.query(func.count())
        .filter(
            RuntimeCardProjection.created_at >= scan_start,
            RuntimeCardProjection.success == False,
        )
        .scalar()
        or 0
    )

    by_agent = []
    for agent_name, metrics in sorted(
        agent_summary.items(),
        key=lambda item: (-(item[1]["llm_calls"] + item[1]["tool_calls"]), item[0]),
    ):
        token_total = int(metrics["token_input"] + metrics["token_output"])
        by_agent.append(
            {
                "agent_name": agent_name,
                "llm_calls": int(metrics["llm_calls"]),
                "tool_calls": int(metrics["tool_calls"]),
                "errors": int(metrics["errors"]),
                "token_input": int(metrics["token_input"]),
                "token_output": int(metrics["token_output"]),
                "token_total": token_total,
                "estimated_cost_usd": round(
                    _runtime_card_cost(int(metrics["token_input"]), int(metrics["token_output"])),
                    4,
                ),
            }
        )

    # Skill and file summaries still need JSON for now (they access nested card data)
    # But these are much less frequent than LLM/tool calls
    skill_summary: dict[str, int] = defaultdict(int)
    file_summary_data = {
        "reads": 0, "writes": 0, "lists": 0, "searches": 0, "deletes": 0, "errors": 0,
    }

    # Only load skill_inject and file tool cards for detailed parsing (much smaller subset)
    detail_rows = (
        db.query(RuntimeCardProjection)
        .filter(
            RuntimeCardProjection.created_at >= scan_start,
            RuntimeCardProjection.card_json.isnot(None),
        )
        .filter(
            (RuntimeCardProjection.card_type == "skill_inject")
            | (RuntimeCardProjection.tool_name.in_(sorted(FILE_MONITOR_TOOLS)))
        )
        .all()
    )

    for proj in detail_rows:
        if not proj.card_json:
            continue
        try:
            card = json.loads(proj.card_json)
        except (json.JSONDecodeError, TypeError):
            continue

        if proj.card_type == "skill_inject":
            skills = card.get("skills") if isinstance(card.get("skills"), list) else []
            for skill in skills:
                if not isinstance(skill, dict):
                    continue
                skill_name = str(skill.get("name") or "").strip()
                if skill_name:
                    skill_summary[skill_name] += 1
        elif proj.tool_name in FILE_MONITOR_TOOLS:
            success = bool(card.get("success", True))
            action = FILE_MONITOR_ACTIONS.get(proj.tool_name, "access")
            if action == "read":
                file_summary_data["reads"] += 1
            elif action == "write":
                file_summary_data["writes"] += 1
            elif action == "list":
                file_summary_data["lists"] += 1
            elif action == "search":
                file_summary_data["searches"] += 1
            elif action == "delete":
                file_summary_data["deletes"] += 1
            if not success:
                file_summary_data["errors"] += 1

    top_skills = [
        {"skill_name": name, "inject_count": count}
        for name, count in sorted(skill_summary.items(), key=lambda item: (-item[1], item[0]))[:8]
    ]

    return {
        "range": range_value,
        "runtime_cards_considered": llm_calls + tool_calls,
        "llm_calls": llm_calls,
        "tool_calls": tool_calls,
        "tool_errors": int(error_count),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "estimated_cost_usd": round(_runtime_card_cost(input_tokens, output_tokens), 4),
        "pricing": {
            "input_per_1k": INPUT_PRICE_PER_1K,
            "output_per_1k": OUTPUT_PRICE_PER_1K,
        },
        "by_agent": by_agent,
        "top_tools": top_tools,
        "top_skills": top_skills,
        "provider_modes": [
            _provider_mode_usage_payload(mode, counts)
            for mode, counts in sorted(
                provider_mode_summary.items(),
                key=lambda item: (-item[1]["calls"], item[0]),
            )
        ],
        "files": {
            "reads": file_summary_data["reads"],
            "writes": file_summary_data["writes"],
            "lists": file_summary_data["lists"],
            "searches": file_summary_data["searches"],
            "deletes": file_summary_data["deletes"],
            "errors": file_summary_data["errors"],
            "unique_paths": 0,
            "top_paths": [],
        },
    }


def _provider_mode_usage_payload(mode: str, counts: dict[str, int]) -> dict[str, Any]:
    def average(total_key: str, count_key: str) -> float | None:
        count = int(counts.get(count_key) or 0)
        if count <= 0:
            return None
        return round(float(counts.get(total_key) or 0) / count, 1)

    omitted_keys = {
        "first_chunk_ms_total",
        "first_chunk_ms_count",
        "first_content_ms_total",
        "first_content_ms_count",
        "completed_ms_total",
        "completed_ms_count",
    }
    return {
        "mode": mode,
        **{key: value for key, value in counts.items() if key not in omitted_keys},
        "avg_first_chunk_ms": average("first_chunk_ms_total", "first_chunk_ms_count"),
        "avg_first_content_ms": average("first_content_ms_total", "first_content_ms_count"),
        "avg_completed_ms": average("completed_ms_total", "completed_ms_count"),
    }


def _build_overview_task_summary(
    db: Session,
    *,
    telemetry_db: Session,
    range_value: str = "24h",
) -> dict[str, Any]:
    scan_start = _range_scan_start(range_value)
    task_runs = (
        db.query(TaskRun)
        .filter(TaskRun.created_at >= scan_start)
        .order_by(desc(TaskRun.created_at), desc(TaskRun.id))
        .all()
    )
    if not task_runs:
        return {
            "range": range_value,
            "counts": {
                "total": 0,
                "running": 0,
                "completed": 0,
                "failed": 0,
                "awaiting_approval": 0,
            },
            "by_agent": [],
            "tokens": {
                "input": 0,
                "output": 0,
                "total": 0,
                "avg_per_task": 0,
            },
            "context": {
                "configured_window": None,
                "avg_usage_ratio": None,
                "max_usage_ratio": None,
                "sampled_runs": 0,
            },
            "artifacts": {
                "recorded": 0,
                "task_runs_with_artifacts": 0,
                "top_outputs": [],
            },
        }

    counts = {
        "total": len(task_runs),
        "running": 0,
        "completed": 0,
        "failed": 0,
        "awaiting_approval": 0,
    }
    by_agent: dict[str, int] = defaultdict(int)
    task_run_ids = [task_run.id for task_run in task_runs]

    for task_run in task_runs:
        status = str(task_run.status or "").lower()
        if status == "running":
            counts["running"] += 1
        elif status == "completed":
            counts["completed"] += 1
        elif status == "failed":
            counts["failed"] += 1
        if any((item.status or "") == "pending" for item in list(task_run.approval_queue_items or [])):
            counts["awaiting_approval"] += 1
        agent_name = str(task_run.target_agent_name or "unassigned")
        by_agent[agent_name] += 1

    llm_rows = (
        telemetry_db.query(LLMCall)
        .filter(LLMCall.run_id.in_(task_run_ids))
        .all()
    )
    total_input = sum(int(row.token_input or 0) for row in llm_rows)
    total_output = sum(int(row.token_output or 0) for row in llm_rows)

    context_budget_rows = (
        db.query(TaskRunEvent)
        .filter(
            TaskRunEvent.task_run_id.in_(task_run_ids),
            TaskRunEvent.event_type == CONTEXT_BUDGET_EVENT_TYPE,
        )
        .all()
    )
    context_ratios: list[float] = []
    configured_windows: list[int] = []
    for event in context_budget_rows:
        payload = _parse_metadata(event.payload_json)
        diagnostics = payload.get("selector_diagnostics") if isinstance(payload.get("selector_diagnostics"), dict) else {}
        prompt = diagnostics.get("prompt") if isinstance(diagnostics.get("prompt"), dict) else {}
        prompt_total = prompt.get("total") if isinstance(prompt.get("total"), dict) else {}
        selector = diagnostics.get("selector") if isinstance(diagnostics.get("selector"), dict) else {}
        user_section = diagnostics.get("user") if isinstance(diagnostics.get("user"), dict) else {}
        prompt_tokens = _coerce_int(prompt_total.get("tokens")) or 0
        max_tokens = _coerce_int(selector.get("max_tokens")) or _coerce_int(user_section.get("effective_input_tokens"))
        if max_tokens and max_tokens > 0 and prompt_tokens >= 0:
            context_ratios.append(prompt_tokens / max_tokens)
            configured_windows.append(max_tokens)

    artifact_rows = (
        telemetry_db.query(Event)
        .filter(
            Event.run_id.in_(task_run_ids),
            Event.event_type == "stage_artifacts_recorded",
        )
        .all()
    )
    recorded_artifacts = 0
    task_runs_with_artifacts: set[int] = set()
    artifact_output_counts: dict[str, int] = defaultdict(int)
    for row in artifact_rows:
        payload = _parse_metadata(row.payload)
        artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), list) else []
        recorded_artifacts += len(artifacts)
        if row.run_id is not None:
            task_runs_with_artifacts.add(int(row.run_id))
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            artifact_path = str(
                artifact.get("file_path")
                or artifact.get("path")
                or artifact.get("title")
                or ""
            ).strip()
            if artifact_path:
                artifact_output_counts[artifact_path] += 1

    if task_run_ids:
        stage_artifact_rows = (
            db.query(PipelineRun.task_run_id, StageArtifact.file_path)
            .join(PipelineStage, StageArtifact.stage_id == PipelineStage.id)
            .join(PipelineRun, PipelineStage.run_id == PipelineRun.id)
            .filter(PipelineRun.task_run_id.in_(task_run_ids))
            .all()
        )
    else:
        stage_artifact_rows = []

    if recorded_artifacts == 0 and stage_artifact_rows:
        recorded_artifacts = len(stage_artifact_rows)
    if not task_runs_with_artifacts and stage_artifact_rows:
        task_runs_with_artifacts = {
            int(task_run_id)
            for task_run_id, _file_path in stage_artifact_rows
            if task_run_id is not None
        }
    if not artifact_output_counts and stage_artifact_rows:
        for _task_run_id, file_path in stage_artifact_rows:
            normalized_path = str(file_path or "").strip()
            if normalized_path:
                artifact_output_counts[normalized_path] += 1

    top_outputs = [
        {"path": path, "count": count}
        for path, count in sorted(artifact_output_counts.items(), key=lambda item: (-item[1], item[0]))[:8]
    ]

    return {
        "range": range_value,
        "counts": counts,
        "by_agent": [
            {"agent_name": name, "task_count": count}
            for name, count in sorted(by_agent.items(), key=lambda item: (-item[1], item[0]))[:8]
        ],
        "tokens": {
            "input": total_input,
            "output": total_output,
            "total": total_input + total_output,
            "avg_per_task": round((total_input + total_output) / len(task_runs), 1) if task_runs else 0,
        },
        "context": {
            "configured_window": max(configured_windows) if configured_windows else None,
            "avg_usage_ratio": round(sum(context_ratios) / len(context_ratios), 4) if context_ratios else None,
            "max_usage_ratio": round(max(context_ratios), 4) if context_ratios else None,
            "sampled_runs": len(context_ratios),
        },
        "artifacts": {
            "recorded": recorded_artifacts,
            "task_runs_with_artifacts": len(task_runs_with_artifacts),
            "top_outputs": top_outputs,
        },
    }


def _build_overview_llm_summary(
    telemetry_db: Session,
    *,
    range_value: str = "24h",
) -> dict[str, Any]:
    scan_start = _range_scan_start(range_value)
    llm_rows = (
        telemetry_db.query(LLMCall)
        .filter(LLMCall.created_at >= scan_start)
        .order_by(desc(LLMCall.created_at), desc(LLMCall.id))
        .all()
    )
    tool_rows = (
        telemetry_db.query(ToolCall)
        .filter(ToolCall.created_at >= scan_start)
        .order_by(desc(ToolCall.created_at), desc(ToolCall.id))
        .all()
    )
    total_input = sum(int(row.token_input or 0) for row in llm_rows)
    total_output = sum(int(row.token_output or 0) for row in llm_rows)
    errors = sum(1 for row in llm_rows if row.error)
    duration_total = sum(int(row.duration_ms or 0) for row in llm_rows)
    tool_total = len(tool_rows)
    tool_errors = sum(1 for row in tool_rows if row.success is False)

    model_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"calls": 0, "tokens": 0})
    for row in llm_rows:
        model_key = str(row.model or "unknown").strip() or "unknown"
        model_counts[model_key]["calls"] += 1
        model_counts[model_key]["tokens"] += int(row.token_input or 0) + int(row.token_output or 0)

    top_models = [
        {"name": name, "calls": data["calls"], "tokens": data["tokens"]}
        for name, data in sorted(model_counts.items(), key=lambda item: (-item[1]["calls"], -item[1]["tokens"], item[0]))[:8]
    ]
    success_calls = len(llm_rows) - errors

    return {
        "range": range_value,
        "status": "healthy" if errors == 0 else ("degraded" if success_calls > 0 else "error"),
        "requests": len(llm_rows),
        "success": success_calls,
        "errors": errors,
        "success_rate": round(success_calls / len(llm_rows), 4) if llm_rows else None,
        "avg_latency_ms": round(duration_total / len(llm_rows), 1) if llm_rows else None,
        "tokens": {
            "input": total_input,
            "output": total_output,
            "total": total_input + total_output,
        },
        "tool_followups": {
            "calls": tool_total,
            "errors": tool_errors,
        },
        "top_models": top_models,
        "last_request_at": llm_rows[0].created_at.isoformat() if llm_rows and llm_rows[0].created_at else None,
    }


def _build_overview_approval_summary(db: Session) -> dict[str, Any]:
    audit_rows = db.query(ApprovalAuditLog).all()
    queue_total = db.query(ApprovalQueueItem).count()
    queue_pending = db.query(ApprovalQueueItem).filter(ApprovalQueueItem.status == "pending").count()
    queue_approved = db.query(ApprovalQueueItem).filter(ApprovalQueueItem.status == "approved").count()
    queue_rejected = db.query(ApprovalQueueItem).filter(ApprovalQueueItem.status == "rejected").count()
    remembered = sum(1 for row in audit_rows if (row.event_kind or "") == "authorization_rule_saved")
    automatic = sum(1 for row in audit_rows if (row.event_kind or "") == "authorization_rule_matched")
    approved = sum(1 for row in audit_rows if (row.decision or "") == "approve")
    rejected = sum(1 for row in audit_rows if (row.decision or "") == "reject")

    return {
        "queue": {
            "total": queue_total,
            "pending": queue_pending,
            "approved": queue_approved,
            "rejected": queue_rejected,
        },
        "audit": {
            "approved": approved,
            "rejected": rejected,
            "remembered": remembered,
            "automatic": automatic,
        },
    }


def _build_overview_context_budget_summary(db: Session) -> dict[str, Any]:
    rows = (
        db.query(TaskRunEvent)
        .filter(TaskRunEvent.event_type == CONTEXT_BUDGET_EVENT_TYPE)
        .order_by(TaskRunEvent.created_at.asc(), TaskRunEvent.id.asc())
        .all()
    )
    if not rows:
        return {
            "total": 0,
            "task_runs": 0,
            "avg_interval_minutes": None,
            "avg_per_task_run": None,
            "avg_prompt_tokens": None,
            "avg_usage_ratio": None,
            "tool_output_saved_tokens": 0,
            "avg_tool_output_savings_pct": None,
            "tool_output_by_tool": [],
            "tool_schema_tokens": 0,
            "tool_schema_saved_tokens": 0,
            "tool_schema_count": 0,
            "tool_schema_by_tool": [],
            "tool_schema_excluded_by_tool": [],
            "trend": [],
            "tool_schema_recommendations": [],
            "reasons": [],
            "last_event_at": None,
        }

    intervals: list[float] = []
    prompt_tokens: list[int] = []
    usage_ratios: list[float] = []
    tool_output_saved_tokens = 0
    tool_output_savings_pcts: list[float] = []
    tool_output_by_tool: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "message_count": 0,
            "summarized_message_count": 0,
            "estimated_saved_tokens": 0,
        }
    )
    tool_schema_tokens = 0
    tool_schema_saved_tokens = 0
    tool_schema_count = 0
    tool_schema_by_tool: dict[str, dict[str, int]] = defaultdict(lambda: {"tokens": 0, "bytes": 0, "count": 0})
    tool_schema_excluded_by_tool: dict[str, dict[str, int]] = defaultdict(lambda: {"tokens": 0, "bytes": 0, "count": 0})
    trend_buckets: dict[str, dict[str, Any]] = {}
    schema_recommendations: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    reason_counts: dict[str, int] = defaultdict(int)
    task_run_counts: dict[int, int] = defaultdict(int)
    previous_created_at: datetime | None = None

    for row in rows:
        task_run_counts[int(row.task_run_id)] += 1
        if row.created_at and previous_created_at:
            intervals.append((row.created_at - previous_created_at).total_seconds() / 60)
        if row.created_at:
            previous_created_at = row.created_at
        payload = _parse_metadata(row.payload_json)
        diagnostics = payload.get("selector_diagnostics") if isinstance(payload.get("selector_diagnostics"), dict) else {}
        prompt = diagnostics.get("prompt") if isinstance(diagnostics.get("prompt"), dict) else {}
        prompt_total = prompt.get("total") if isinstance(prompt.get("total"), dict) else {}
        selector = diagnostics.get("selector") if isinstance(diagnostics.get("selector"), dict) else {}
        user_section = diagnostics.get("user") if isinstance(diagnostics.get("user"), dict) else {}
        prompt_token_value = _coerce_int(prompt_total.get("tokens"))
        max_tokens = _coerce_int(selector.get("max_tokens")) or _coerce_int(user_section.get("effective_input_tokens"))
        if prompt_token_value is not None:
            prompt_tokens.append(prompt_token_value)
        if prompt_token_value is not None and max_tokens and max_tokens > 0:
            usage_ratios.append(prompt_token_value / max_tokens)
        trend_bucket = _context_budget_trend_bucket(row.created_at)
        tool_output_budget = prompt.get("tool_output_budget") if isinstance(prompt.get("tool_output_budget"), dict) else {}
        event_tool_output_saved_tokens = _coerce_int(tool_output_budget.get("estimated_saved_tokens")) or 0
        tool_output_saved_tokens += event_tool_output_saved_tokens
        savings_pct = tool_output_budget.get("estimated_savings_pct")
        if isinstance(savings_pct, (int, float)):
            tool_output_savings_pcts.append(float(savings_pct))
        by_tool = tool_output_budget.get("by_tool") if isinstance(tool_output_budget.get("by_tool"), dict) else {}
        for tool_name, payload in by_tool.items():
            if not isinstance(payload, dict):
                continue
            normalized_tool_name = str(tool_name or "tool").strip() or "tool"
            tool_entry = tool_output_by_tool[normalized_tool_name]
            tool_entry["message_count"] += _coerce_int(payload.get("message_count")) or 0
            tool_entry["summarized_message_count"] += _coerce_int(payload.get("summarized_message_count")) or 0
            tool_entry["estimated_saved_tokens"] += _coerce_int(payload.get("estimated_saved_tokens")) or 0
        tool_schema_budget = prompt.get("tool_schema_budget") if isinstance(prompt.get("tool_schema_budget"), dict) else {}
        event_tool_schema_tokens = _coerce_int(tool_schema_budget.get("tokens")) or 0
        event_tool_schema_saved_tokens = _coerce_int(tool_schema_budget.get("estimated_saved_tokens")) or 0
        tool_schema_tokens += event_tool_schema_tokens
        tool_schema_saved_tokens += event_tool_schema_saved_tokens
        tool_schema_count += _coerce_int(tool_schema_budget.get("tool_count")) or 0
        if trend_bucket:
            bucket = trend_buckets.setdefault(
                trend_bucket["bucket"],
                {
                    "bucket": trend_bucket["bucket"],
                    "label": trend_bucket["label"],
                    "event_count": 0,
                    "tool_output_saved_tokens": 0,
                    "tool_schema_tokens": 0,
                    "tool_schema_saved_tokens": 0,
                    "_prompt_token_sum": 0,
                    "_prompt_token_count": 0,
                    "_usage_ratio_sum": 0.0,
                    "_usage_ratio_count": 0,
                },
            )
            bucket["event_count"] += 1
            bucket["tool_output_saved_tokens"] += event_tool_output_saved_tokens
            bucket["tool_schema_tokens"] += event_tool_schema_tokens
            bucket["tool_schema_saved_tokens"] += event_tool_schema_saved_tokens
            if prompt_token_value is not None:
                bucket["_prompt_token_sum"] += prompt_token_value
                bucket["_prompt_token_count"] += 1
            if prompt_token_value is not None and max_tokens and max_tokens > 0:
                bucket["_usage_ratio_sum"] += prompt_token_value / max_tokens
                bucket["_usage_ratio_count"] += 1
        schema_filter = (
            tool_schema_budget.get("filter")
            if isinstance(tool_schema_budget.get("filter"), dict)
            else {}
        )
        schema_profile_name = str(schema_filter.get("profile_name") or "default").strip() or "default"
        schema_mode = str(schema_filter.get("mode") or "default").strip() or "default"
        schema_agent = str(row.agent_name or "agent").strip() or "agent"
        schema_by_tool = tool_schema_budget.get("by_tool") if isinstance(tool_schema_budget.get("by_tool"), list) else []
        for item in schema_by_tool:
            if not isinstance(item, dict):
                continue
            normalized_tool_name = str(item.get("tool_name") or "tool").strip() or "tool"
            schema_entry = tool_schema_by_tool[normalized_tool_name]
            schema_entry["tokens"] += _coerce_int(item.get("tokens")) or 0
            schema_entry["bytes"] += _coerce_int(item.get("bytes")) or 0
            schema_entry["count"] += 1
        schema_excluded_by_tool = (
            tool_schema_budget.get("excluded_by_tool")
            if isinstance(tool_schema_budget.get("excluded_by_tool"), list)
            else []
        )
        for item in schema_excluded_by_tool:
            if not isinstance(item, dict):
                continue
            normalized_tool_name = str(item.get("tool_name") or "tool").strip() or "tool"
            schema_entry = tool_schema_excluded_by_tool[normalized_tool_name]
            excluded_tokens = _coerce_int(item.get("tokens")) or 0
            schema_entry["tokens"] += excluded_tokens
            schema_entry["bytes"] += _coerce_int(item.get("bytes")) or 0
            schema_entry["count"] += 1
            recommendation_key = (
                "frequently_filtered",
                schema_agent,
                schema_profile_name,
                schema_mode,
                normalized_tool_name,
            )
            recommendation = schema_recommendations.setdefault(
                recommendation_key,
                {
                    "kind": "frequently_filtered",
                    "agent_name": schema_agent,
                    "profile_name": schema_profile_name,
                    "mode": schema_mode,
                    "tool_name": normalized_tool_name,
                    "event_count": 0,
                    "tokens": 0,
                },
            )
            recommendation["event_count"] += 1
            recommendation["tokens"] += excluded_tokens
        activated_groups = (
            schema_filter.get("activated_groups")
            if isinstance(schema_filter.get("activated_groups"), list)
            else []
        )
        for group_name in activated_groups:
            normalized_group_name = str(group_name or "group").strip() or "group"
            recommendation_key = (
                "frequent_activation",
                schema_agent,
                schema_profile_name,
                schema_mode,
                normalized_group_name,
            )
            recommendation = schema_recommendations.setdefault(
                recommendation_key,
                {
                    "kind": "frequent_activation",
                    "agent_name": schema_agent,
                    "profile_name": schema_profile_name,
                    "mode": schema_mode,
                    "group_name": normalized_group_name,
                    "event_count": 0,
                    "tokens": 0,
                },
            )
            recommendation["event_count"] += 1
        reasons = diagnostics.get("reasons") if isinstance(diagnostics.get("reasons"), list) else []
        for reason in reasons:
            if not isinstance(reason, dict):
                continue
            name = str(reason.get("kind") or "unknown").strip() or "unknown"
            reason_counts[name] += 1

    trend = []
    for _, bucket in sorted(trend_buckets.items()):
        prompt_token_count = int(bucket.pop("_prompt_token_count", 0))
        prompt_token_sum = int(bucket.pop("_prompt_token_sum", 0))
        usage_ratio_count = int(bucket.pop("_usage_ratio_count", 0))
        usage_ratio_sum = float(bucket.pop("_usage_ratio_sum", 0.0))
        bucket["avg_prompt_tokens"] = (
            round(prompt_token_sum / prompt_token_count, 1)
            if prompt_token_count
            else None
        )
        bucket["avg_usage_ratio"] = (
            round(usage_ratio_sum / usage_ratio_count, 4)
            if usage_ratio_count
            else None
        )
        trend.append(bucket)

    return {
        "total": len(rows),
        "task_runs": len(task_run_counts),
        "avg_interval_minutes": round(sum(intervals) / len(intervals), 1) if intervals else None,
        "avg_per_task_run": round(len(rows) / len(task_run_counts), 2) if task_run_counts else None,
        "avg_prompt_tokens": round(sum(prompt_tokens) / len(prompt_tokens), 1) if prompt_tokens else None,
        "avg_usage_ratio": round(sum(usage_ratios) / len(usage_ratios), 4) if usage_ratios else None,
        "tool_output_saved_tokens": tool_output_saved_tokens,
        "avg_tool_output_savings_pct": (
            round(sum(tool_output_savings_pcts) / len(tool_output_savings_pcts), 1)
            if tool_output_savings_pcts
            else None
        ),
        "tool_output_by_tool": [
            {"tool_name": tool_name, **counts}
            for tool_name, counts in sorted(
                tool_output_by_tool.items(),
                key=lambda item: (-item[1]["estimated_saved_tokens"], item[0]),
            )[:8]
        ],
        "tool_schema_tokens": tool_schema_tokens,
        "tool_schema_saved_tokens": tool_schema_saved_tokens,
        "tool_schema_count": tool_schema_count,
        "tool_schema_by_tool": [
            {"tool_name": tool_name, **counts}
            for tool_name, counts in sorted(
                tool_schema_by_tool.items(),
                key=lambda item: (-item[1]["tokens"], item[0]),
            )[:8]
        ],
        "tool_schema_excluded_by_tool": [
            {"tool_name": tool_name, **counts}
            for tool_name, counts in sorted(
                tool_schema_excluded_by_tool.items(),
                key=lambda item: (-item[1]["tokens"], item[0]),
            )[:8]
        ],
        "trend": trend[-24:],
        "tool_schema_recommendations": sorted(
            schema_recommendations.values(),
            key=lambda item: (-int(item.get("tokens") or 0), -int(item.get("event_count") or 0), str(item.get("tool_name") or item.get("group_name") or "")),
        )[:8],
        "reasons": [
            {"reason": reason, "count": count}
            for reason, count in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))[:8]
        ],
        "last_event_at": rows[-1].created_at.isoformat() if rows[-1].created_at else None,
    }


def _task_run_step_sort_key(step: dict[str, Any]) -> tuple[str, int]:
    created_at = step.get("created_at")
    step_id = step.get("sequence") or 0
    return (str(created_at or ""), int(step_id))


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except (TypeError, ValueError):
            return None
    return None


def _context_budget_trend_bucket(created_at: datetime | None) -> dict[str, str] | None:
    if not created_at:
        return None
    bucket_at = created_at.replace(minute=0, second=0, microsecond=0)
    return {
        "bucket": bucket_at.isoformat(),
        "label": bucket_at.strftime("%m-%d %H:%M"),
    }


def _runtime_card_matches_task_run(
    *,
    task_run: TaskRun,
    metadata: dict[str, Any],
    card: dict[str, Any],
) -> bool:
    task_run_id = (
        _coerce_int(metadata.get("task_run_id"))
        or _coerce_int(card.get("task_run_id"))
    )
    if task_run_id is not None:
        return task_run_id == task_run.id

    task_turn_id = (task_run.client_turn_id or "").strip()
    if not task_turn_id:
        return False
    return _metadata_client_turn_id(metadata) == task_turn_id


def _serialize_runtime_step(
    *,
    message: Message,
    card: dict[str, Any],
) -> dict[str, Any]:
    card_type = str(card.get("type") or "runtime")
    step_kind = "event"
    if card_type == "llm_call":
        step_kind = "llm"
    elif card_type == "tool_call":
        step_kind = "tool"

    tool_calls = card.get("tool_calls")
    planned_tools: list[str] = []
    if isinstance(tool_calls, list):
        for item in tool_calls:
            if not isinstance(item, dict):
                continue
            function = item.get("function") if isinstance(item.get("function"), dict) else {}
            tool_name = function.get("name") or item.get("name")
            if isinstance(tool_name, str) and tool_name.strip():
                planned_tools.append(tool_name.strip())

    return {
        "id": f"runtime-{message.id}",
        "source": "runtime_card",
        "step_kind": step_kind,
        "title": _build_runtime_title(card),
        "preview": _build_runtime_preview(card),
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "agent_name": card.get("agent") or card.get("from_agent"),
        "turn": _coerce_int(card.get("turn")),
        "model": card.get("model"),
        "tool_name": card.get("tool"),
        "success": card.get("success"),
        "status": card.get("status"),
        "blocked": card.get("blocked"),
        "blocked_kind": card.get("blocked_kind"),
        "duration_ms": _coerce_int(card.get("duration_ms")),
        "tokens_in": _coerce_int(card.get("tokens_in")) or 0,
        "tokens_out": _coerce_int(card.get("tokens_out")) or 0,
        "event_type": card_type,
        "message_id": message.id,
        "pipeline_run_id": _coerce_int(card.get("pipeline_run_id")),
        "pipeline_stage_id": _coerce_int(card.get("pipeline_stage_id")),
        "arguments": card.get("arguments") if isinstance(card.get("arguments"), str) else None,
        "result": card.get("result") if isinstance(card.get("result"), str) else None,
        "prompt_preview": _extract_prompt_preview(card),
        "response_preview": _compact_preview(
            card.get("summary_text")
            or card.get("response_preview")
            or card.get("response")
            or card.get("result")
        ),
        "planned_tools": planned_tools,
        "payload": card,
    }


def _serialize_synthetic_tool_steps(event: TaskRunEvent, payload: dict[str, Any]) -> list[dict[str, Any]]:
    turn_local_state = payload.get("turn_local_state")
    if not isinstance(turn_local_state, dict):
        return []

    tool_results = turn_local_state.get("tool_results")
    if not isinstance(tool_results, list):
        return []

    turn = _coerce_int(payload.get("turn"))
    created_at = event.created_at.isoformat() if event.created_at else None
    agent_name = event.agent_name
    steps: list[dict[str, Any]] = []
    for index, result in enumerate(tool_results, start=1):
        if not isinstance(result, dict):
            continue
        tool_name = str(result.get("tool_name") or "tool")
        status = str(result.get("status") or ("succeeded" if result.get("success") else "failed"))
        preview = _compact_preview(result.get("result") or result.get("blocked_reason"))
        steps.append(
            {
                "id": f"event-tool-{event.id}-{index}",
                "source": "task_event",
                "step_kind": "tool",
                "title": f"{agent_name or 'agent'} used {tool_name}",
                "preview": preview,
                "created_at": created_at,
                "agent_name": agent_name,
                "turn": turn,
                "model": None,
                "tool_name": tool_name,
                "success": bool(result.get("success", False)),
                "status": status,
                "blocked": bool(result.get("blocked", False)),
                "blocked_kind": result.get("blocked_kind"),
                "duration_ms": None,
                "tokens_in": 0,
                "tokens_out": 0,
                "event_type": event.event_type,
                "message_id": event.message_id,
                "pipeline_run_id": _coerce_int(payload.get("pipeline_run_id")),
                "pipeline_stage_id": _coerce_int(payload.get("pipeline_stage_id") or payload.get("stage_id")),
                "arguments": result.get("arguments") if isinstance(result.get("arguments"), str) else None,
                "result": result.get("result") if isinstance(result.get("result"), str) else None,
                "prompt_preview": None,
                "response_preview": preview,
                "planned_tools": [],
                "payload": result,
            }
        )
    return steps


def _serialize_synthetic_llm_step(event: TaskRunEvent, payload: dict[str, Any]) -> dict[str, Any]:
    preview = _compact_preview(payload.get("response_preview") or event.summary)
    return {
        "id": f"event-llm-{event.id}",
        "source": "task_event",
        "step_kind": "llm",
        "title": f"{event.agent_name or 'agent'} completed an LLM turn",
        "preview": preview,
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "agent_name": event.agent_name,
        "turn": _coerce_int(payload.get("turn")),
        "model": payload.get("model"),
        "tool_name": None,
        "success": True,
        "status": "completed",
        "blocked": False,
        "blocked_kind": None,
        "duration_ms": _coerce_int(payload.get("duration_ms")),
        "tokens_in": _coerce_int(payload.get("tokens_in")) or 0,
        "tokens_out": _coerce_int(payload.get("tokens_out")) or 0,
        "event_type": event.event_type,
        "message_id": event.message_id,
        "pipeline_run_id": _coerce_int(payload.get("pipeline_run_id")),
        "pipeline_stage_id": _coerce_int(payload.get("pipeline_stage_id") or payload.get("stage_id")),
        "arguments": None,
        "result": payload.get("response_preview") if isinstance(payload.get("response_preview"), str) else None,
        "prompt_preview": None,
        "response_preview": preview,
        "planned_tools": [],
        "payload": payload,
    }


def _serialize_task_event_step(event: TaskRunEvent, payload: dict[str, Any]) -> dict[str, Any]:
    preview = _compact_preview(
        event.summary
        or payload.get("response_preview")
        or payload.get("blocked_reason")
        or payload.get("details")
        or payload
    )
    return {
        "id": f"event-{event.id}",
        "source": "task_event",
        "step_kind": "event",
        "title": title_case_event_label(event.event_type),
        "preview": preview,
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "agent_name": event.agent_name,
        "turn": _coerce_int(payload.get("turn")),
        "model": payload.get("model"),
        "tool_name": payload.get("tool_name"),
        "success": payload.get("success"),
        "status": payload.get("status"),
        "blocked": payload.get("blocked"),
        "blocked_kind": payload.get("blocked_kind"),
        "duration_ms": _coerce_int(payload.get("duration_ms")),
        "tokens_in": _coerce_int(payload.get("tokens_in")) or 0,
        "tokens_out": _coerce_int(payload.get("tokens_out")) or 0,
        "event_type": event.event_type,
        "message_id": event.message_id,
        "pipeline_run_id": _coerce_int(payload.get("pipeline_run_id")),
        "pipeline_stage_id": _coerce_int(payload.get("pipeline_stage_id") or payload.get("stage_id")),
        "arguments": payload.get("arguments") if isinstance(payload.get("arguments"), str) else None,
        "result": payload.get("result") if isinstance(payload.get("result"), str) else None,
        "prompt_preview": None,
        "response_preview": _compact_preview(payload.get("response_preview")),
        "planned_tools": [],
        "payload": payload,
    }


def _step_counts(steps: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(steps),
        "llm": sum(1 for step in steps if step.get("step_kind") == "llm"),
        "tool": sum(1 for step in steps if step.get("step_kind") == "tool"),
        "event": sum(1 for step in steps if step.get("step_kind") == "event"),
        "tool_errors": sum(
            1
            for step in steps
            if step.get("step_kind") == "tool" and step.get("success") is False
        ),
        "tool_blocked": sum(
            1
            for step in steps
            if step.get("step_kind") == "tool" and bool(step.get("blocked"))
        ),
        "tokens_in": sum(int(step.get("tokens_in") or 0) for step in steps),
        "tokens_out": sum(int(step.get("tokens_out") or 0) for step in steps),
    }


def _serialize_pipeline_llm_step(call: LLMCall) -> dict[str, Any]:
    prompt_preview = ""
    planned_tools: list[str] = []
    if call.messages:
        try:
            messages = json.loads(call.messages)
        except json.JSONDecodeError:
            messages = call.messages
        if isinstance(messages, list):
            for message in reversed(messages):
                if not isinstance(message, dict):
                    continue
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    prompt_preview = _compact_preview(content)
                    break
    if call.response_tool_calls:
        try:
            tool_calls = json.loads(call.response_tool_calls)
        except json.JSONDecodeError:
            tool_calls = None
        if isinstance(tool_calls, list):
            for item in tool_calls:
                if not isinstance(item, dict):
                    continue
                function = item.get("function") if isinstance(item.get("function"), dict) else {}
                tool_name = function.get("name")
                if isinstance(tool_name, str) and tool_name.strip():
                    planned_tools.append(tool_name.strip())

    return {
        "id": f"pipeline-llm-{call.id}",
        "source": "pipeline_audit_llm",
        "step_kind": "llm",
        "title": f"{call.agent_name} -> LLM",
        "preview": _compact_preview(call.response_content or call.error),
        "created_at": call.created_at.isoformat() if call.created_at else None,
        "agent_name": call.agent_name,
        "turn": _coerce_int(call.turn_index),
        "model": call.model,
        "tool_name": None,
        "success": not bool(call.error),
        "status": "failed" if call.error else "completed",
        "blocked": False,
        "blocked_kind": None,
        "duration_ms": _coerce_int(call.duration_ms),
        "tokens_in": _coerce_int(call.token_input) or 0,
        "tokens_out": _coerce_int(call.token_output) or 0,
        "event_type": "llm_call",
        "message_id": None,
        "pipeline_run_id": _coerce_int(call.run_id),
        "pipeline_stage_id": _coerce_int(call.stage_id),
        "arguments": None,
        "result": call.response_content,
        "prompt_preview": prompt_preview,
        "response_preview": _compact_preview(call.response_content or call.error),
        "planned_tools": planned_tools,
        "payload": {
            "error": call.error,
            "response_tool_calls": call.response_tool_calls,
        },
    }


def _serialize_pipeline_tool_step(call: ToolCall) -> dict[str, Any]:
    return {
        "id": f"pipeline-tool-{call.id}",
        "source": "pipeline_audit_tool",
        "step_kind": "tool",
        "title": f"{call.agent_name} used {call.tool_name}",
        "preview": _compact_preview(call.result_summary),
        "created_at": call.created_at.isoformat() if call.created_at else None,
        "agent_name": call.agent_name,
        "turn": None,
        "model": None,
        "tool_name": call.tool_name,
        "success": bool(call.success),
        "status": "succeeded" if call.success else "failed",
        "blocked": False,
        "blocked_kind": None,
        "duration_ms": _coerce_int(call.duration_ms),
        "tokens_in": 0,
        "tokens_out": 0,
        "event_type": "tool_call",
        "message_id": None,
        "pipeline_run_id": _coerce_int(call.run_id),
        "pipeline_stage_id": _coerce_int(call.stage_id),
        "arguments": call.arguments,
        "result": call.result_summary,
        "prompt_preview": None,
        "response_preview": _compact_preview(call.result_summary),
        "planned_tools": [],
        "payload": {
            "result_length": call.result_length,
        },
    }


def title_case_event_label(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return "Event"
    return " ".join(part.capitalize() for part in text.split("_") if part)


@router.get("/logs")
async def get_monitor_logs(
    limit: int = Query(200, ge=20, le=1000),
    level: str = Query("all"),
    query: str | None = Query(None, max_length=200),
):
    entries = monitor_log_buffer.list_entries(
        limit=limit,
        level=_normalize_log_level(level),
        query=query,
    )
    return {
        "captured_at": datetime.now().isoformat(),
        "latest_id": monitor_log_buffer.latest_id(),
        "entries": entries,
    }


@router.get("/network")
async def get_monitor_network_events(
    limit: int = Query(300, ge=20, le=2000),
    category: str = Query("all"),
    query: str | None = Query(None, max_length=200),
    include_internal: bool = Query(False),
):
    entries = monitor_network_buffer.list_entries(
        limit=limit,
        category=_normalize_network_category(category),
        query=query,
        include_internal=include_internal,
    )
    return {
        "captured_at": datetime.now().isoformat(),
        "latest_id": monitor_network_buffer.latest_id(),
        "entries": entries,
    }


@router.post("/network/ingest")
async def ingest_monitor_network_event(payload: dict[str, Any]):
    event = monitor_network_buffer.append(payload, require_persisted_id=True)
    return {"ok": True, "event_id": event["id"]}


@router.get("/network/stream")
async def stream_monitor_network_events(
    request: Request,
    cursor: int = Query(0, ge=0),
    category: str = Query("all"),
    query: str | None = Query(None, max_length=200),
    once: bool = Query(False),
    include_internal: bool = Query(False),
):
    normalized_category = _normalize_network_category(category)
    scan_limit = LOG_STREAM_LIMIT

    async def event_generator():
        last_seen_id = cursor
        idle_ticks = 0

        # Flush SSE headers immediately so the frontend can enter "connected"
        # state even when there are no new events yet.
        yield ": connected\n\n"

        while True:
            if await request.is_disconnected():
                break

            entries = monitor_network_buffer.list_entries(
                limit=scan_limit,
                after_id=last_seen_id,
                category=normalized_category,
                query=query,
                include_internal=include_internal,
            )
            if entries:
                last_seen_id = max(last_seen_id, max(int(entry["id"]) for entry in entries))
                for entry in reversed(entries[:LOG_STREAM_LIMIT]):
                    last_seen_id = max(last_seen_id, int(entry["id"]))
                    yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
                idle_ticks = 0
                if once:
                    break
                continue

            idle_ticks += 1
            if once:
                break
            if idle_ticks >= int(15 / LOG_STREAM_POLL_INTERVAL):
                yield ": ping\n\n"
                idle_ticks = 0

            await asyncio.sleep(LOG_STREAM_POLL_INTERVAL)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/logs/stream")
async def stream_monitor_logs(
    request: Request,
    cursor: int = Query(0, ge=0),
    level: str = Query("all"),
    query: str | None = Query(None, max_length=200),
    once: bool = Query(False),
):
    normalized_level = _normalize_log_level(level)

    async def event_generator():
        last_seen_id = cursor
        idle_ticks = 0

        # Flush SSE headers immediately so the frontend can enter "connected"
        # state even when there are no new log entries yet.
        yield ": connected\n\n"

        while True:
            if await request.is_disconnected():
                break

            entries = monitor_log_buffer.list_entries(
                limit=LOG_STREAM_LIMIT,
                after_id=last_seen_id,
                level=normalized_level,
                query=query,
            )
            if entries:
                for entry in entries:
                    last_seen_id = max(last_seen_id, int(entry["id"]))
                    yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
                idle_ticks = 0
                if once:
                    break
                continue

            idle_ticks += 1
            if once:
                break
            if idle_ticks >= int(15 / LOG_STREAM_POLL_INTERVAL):
                yield ": ping\n\n"
                idle_ticks = 0

            await asyncio.sleep(LOG_STREAM_POLL_INTERVAL)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/runtime-cards/{message_id}")
async def get_monitor_runtime_card_detail(message_id: int, db: Session = Depends(get_db)):
    # Try projection table first (single json.loads instead of multi-level)
    projection = (
        db.query(RuntimeCardProjection)
        .filter(RuntimeCardProjection.message_id == message_id)
        .first()
    )
    if projection and projection.card_json:
        card = json.loads(projection.card_json)
        chatroom = db.query(Chatroom).filter(Chatroom.id == projection.chatroom_id).first()
        project = None
        if chatroom and chatroom.project_id:
            project = db.query(Project).filter(Project.id == chatroom.project_id).first()
        return serialize_monitor_runtime_detail(
            runtime_message_id=message_id,
            chatroom_id=projection.chatroom_id,
            chat_title=chatroom.title if chatroom else "",
            project_id=project.id if project else None,
            project_name=project.name if project else None,
            card=card,
            created_at=projection.created_at,
            metadata={},
        )

    # Fallback to legacy path
    row = (
        db.query(Message, Chatroom, Project)
        .join(Chatroom, Message.chatroom_id == Chatroom.id)
        .outerjoin(Project, Chatroom.project_id == Project.id)
        .filter(Message.id == message_id, Message.message_type == "runtime_card")
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Runtime card not found")

    message, chatroom, project = row
    metadata = _parse_metadata(message.metadata_json)
    card = metadata.get("card") if isinstance(metadata.get("card"), dict) else None
    if not card:
        raise HTTPException(status_code=404, detail="Runtime card payload is unavailable")

    return serialize_monitor_runtime_detail(
        runtime_message_id=message.id,
        chatroom_id=chatroom.id,
        chat_title=chatroom.title,
        project_id=project.id if project else None,
        project_name=project.name if project else None,
        card=card,
        created_at=message.created_at,
        metadata=metadata,
    )


@router.get("/usage")
async def get_monitor_usage(
    range: str = Query("24h", pattern="^(1h|6h|24h|7d|30d)$"),
    db: Session = Depends(get_db),
):
    range_value = range if range in USAGE_RANGES else "24h"
    bucket_specs = _usage_bucket_boundaries(range_value)
    bucket_start = bucket_specs[0][0]
    month_start = _period_start("month")
    scan_start = min(bucket_start, month_start)

    buckets = [
        {
            "label": label,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "estimated_cost_usd": 0.0,
            "llm_calls": 0,
        }
        for start, end, label in bucket_specs
    ]

    totals = {
        "day": _empty_usage_totals(),
        "week": _empty_usage_totals(),
        "month": _empty_usage_totals(),
    }

    projections = (
        db.query(RuntimeCardProjection)
        .filter(
            RuntimeCardProjection.card_type == "llm_call",
            RuntimeCardProjection.created_at >= scan_start,
        )
        .order_by(RuntimeCardProjection.created_at.asc(), RuntimeCardProjection.id.asc())
        .all()
    )

    scanned_runtime_cards = 0
    day_start = _period_start("day")
    week_start = _period_start("week")

    for proj in projections:
        created_at = proj.created_at
        if created_at is None:
            continue

        tokens_in = proj.tokens_in or 0
        tokens_out = proj.tokens_out or 0
        scanned_runtime_cards += 1

        if created_at >= day_start:
            _add_usage(totals["day"], tokens_in, tokens_out)
        if created_at >= week_start:
            _add_usage(totals["week"], tokens_in, tokens_out)
        if created_at >= month_start:
            _add_usage(totals["month"], tokens_in, tokens_out)

        for index, (start, end, _label) in enumerate(bucket_specs):
            if start <= created_at < end:
                buckets[index]["input_tokens"] += tokens_in
                buckets[index]["output_tokens"] += tokens_out
                buckets[index]["total_tokens"] += tokens_in + tokens_out
                buckets[index]["estimated_cost_usd"] += _runtime_card_cost(tokens_in, tokens_out)
                buckets[index]["llm_calls"] += 1
                break

    return {
        "captured_at": datetime.now().isoformat(),
        "range": range_value,
        "pricing": {
            "input_per_1k": INPUT_PRICE_PER_1K,
            "output_per_1k": OUTPUT_PRICE_PER_1K,
        },
        "totals": {
            "day": _finalize_usage_totals(totals["day"]),
            "week": _finalize_usage_totals(totals["week"]),
            "month": _finalize_usage_totals(totals["month"]),
        },
        "buckets": [
            {
                **bucket,
                "estimated_cost_usd": round(float(bucket["estimated_cost_usd"]), 4),
            }
            for bucket in buckets
        ],
        "scanned_runtime_cards": scanned_runtime_cards,
    }


@router.get("/task-runs")
async def get_monitor_task_runs(
    range: str = Query("24h", pattern="^(1h|6h|24h|7d|30d)$"),
    limit: int = Query(120, ge=10, le=300),
    db: Session = Depends(get_db),
):
    range_value = range if range in USAGE_RANGES else "24h"
    scan_start = _range_scan_start(range_value)

    rows = (
        db.query(TaskRun, Chatroom, Project)
        .join(Chatroom, TaskRun.chatroom_id == Chatroom.id)
        .outerjoin(Project, TaskRun.project_id == Project.id)
        .filter(TaskRun.created_at >= scan_start)
        .order_by(desc(TaskRun.created_at), desc(TaskRun.id))
        .limit(limit)
        .all()
    )

    entries: list[dict[str, Any]] = []
    for task_run, chatroom, project in rows:
        entries.append(
            serialize_monitor_task_run_summary(
                task_run,
                chat_title=chatroom.title,
                project_name=project.name if project else None,
            )
        )

    return {
        "captured_at": datetime.now().isoformat(),
        "range": range_value,
        "entries": entries,
    }


@router.get("/processes")
async def get_monitor_processes(
    limit: int = Query(120, ge=10, le=300),
    tail_chars: int = Query(1200, ge=0, le=8000),
):
    entries = list_tracked_run_shell_processes(limit=limit, tail_chars=tail_chars)
    return {
        "captured_at": datetime.now().isoformat(),
        "entries": entries,
        "counts": {
            "total": len(entries),
            "running": sum(1 for entry in entries if entry.get("is_active")),
            "finished": sum(1 for entry in entries if entry.get("is_terminal")),
            "failed": sum(1 for entry in entries if str(entry.get("status") or "").lower() == "failed"),
        },
    }


@router.get("/task-runs/{task_run_id}/steps")
async def get_monitor_task_run_steps(
    task_run_id: int,
    limit: int = Query(120, ge=10, le=500),
    db: Session = Depends(get_db),
    telemetry_db: Session = Depends(get_telemetry_db),
):
    task_run = (
        db.query(TaskRun)
        .filter(TaskRun.id == task_run_id)
        .first()
    )
    if not task_run:
        raise HTTPException(status_code=404, detail="Task run not found")

    scan_limit = max(TASK_RUN_STEP_SCAN_MIN, min(TASK_RUN_STEP_SCAN_MAX, limit * TASK_RUN_STEP_SCAN_MULTIPLIER))

    # Use projection table with task_run_id filter for direct match
    proj_filters = [
        RuntimeCardProjection.chatroom_id == task_run.chatroom_id,
    ]
    if task_run.id is not None:
        proj_filters.append(RuntimeCardProjection.task_run_id == task_run.id)

    projections = (
        db.query(RuntimeCardProjection)
        .filter(*proj_filters)
        .order_by(RuntimeCardProjection.id.desc())
        .limit(scan_limit)
        .all()
    )

    runtime_steps: list[dict[str, Any]] = []
    runtime_message_ids: list[int] = []
    for proj in projections:
        if len(runtime_steps) >= limit:
            break
        if not proj.card_json:
            continue
        try:
            card = json.loads(proj.card_json)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(card, dict):
            continue
        # Build a minimal message-like object for _serialize_runtime_step compatibility
        message_proxy = SimpleNamespace(
            id=proj.message_id,
            created_at=proj.created_at,
            metadata_json=proj.card_json,
        )
        runtime_steps.append(_serialize_runtime_step(message=message_proxy, card=card))
        runtime_message_ids.append(proj.message_id)

    # Fallback: if no task_run_id matches, try client_turn_id matching
    if not runtime_steps and task_run.client_turn_id:
        fallback_rows = (
            db.query(Message)
            .filter(Message.chatroom_id == task_run.chatroom_id, Message.message_type == "runtime_card")
            .order_by(Message.id.desc())
            .limit(scan_limit)
            .all()
        )
        for message in fallback_rows:
            if len(runtime_steps) >= limit:
                break
            metadata = _parse_metadata(message.metadata_json)
            card = metadata.get("card") if isinstance(metadata.get("card"), dict) else None
            if not card:
                continue
            if not _runtime_card_matches_task_run(task_run=task_run, metadata=metadata, card=card):
                continue
            runtime_steps.append(_serialize_runtime_step(message=message, card=card))
            runtime_message_ids.append(message.id)

    pipeline_run_ids = [
        run.id
        for run in list(getattr(task_run, "pipeline_runs", []) or [])
        if getattr(run, "id", None) is not None
    ]
    pipeline_llm_steps: list[dict[str, Any]] = []
    pipeline_tool_steps: list[dict[str, Any]] = []
    if pipeline_run_ids:
        llm_calls = (
            telemetry_db.query(LLMCall)
            .filter(LLMCall.run_id.in_(pipeline_run_ids))
            .order_by(LLMCall.id.desc())
            .limit(limit)
            .all()
        )
        pipeline_llm_steps = [_serialize_pipeline_llm_step(call) for call in llm_calls]

        tool_calls = (
            telemetry_db.query(ToolCall)
            .filter(ToolCall.run_id.in_(pipeline_run_ids))
            .order_by(ToolCall.id.desc())
            .limit(limit)
            .all()
        )
        pipeline_tool_steps = [_serialize_pipeline_tool_step(call) for call in tool_calls]

    runtime_coverage = {
        "llm_turns": {(step.get("agent_name"), step.get("turn")) for step in runtime_steps if step.get("step_kind") == "llm"},
        "tool_turns": {(step.get("agent_name"), step.get("turn")) for step in runtime_steps if step.get("step_kind") == "tool"},
    }

    event_steps: list[dict[str, Any]] = []
    task_events = (
        db.query(TaskRunEvent)
        .filter(TaskRunEvent.task_run_id == task_run.id)
        .order_by(TaskRunEvent.event_index.desc(), TaskRunEvent.id.desc())
        .limit(limit)
        .all()
    )
    task_event_count = (
        db.query(func.count(TaskRunEvent.id))
        .filter(TaskRunEvent.task_run_id == task_run.id)
        .scalar()
        or 0
    )
    for event in task_events:
        payload = _parse_metadata(event.payload_json)
        event_type = str(event.event_type or "")
        turn = _coerce_int(payload.get("turn"))
        if event_type == "tool_round_recorded":
            if (event.agent_name, turn) not in runtime_coverage["tool_turns"]:
                event_steps.extend(_serialize_synthetic_tool_steps(event, payload))
            continue
        if event_type == "agent_turn_completed":
            if (event.agent_name, turn) not in runtime_coverage["llm_turns"]:
                event_steps.append(_serialize_synthetic_llm_step(event, payload))
            continue
        event_steps.append(_serialize_task_event_step(event, payload))

    merged_steps = sorted(
        [*runtime_steps, *pipeline_llm_steps, *pipeline_tool_steps, *event_steps],
        key=_task_run_step_sort_key,
    )
    for index, step in enumerate(merged_steps, start=1):
        step["sequence"] = index
    visible_steps = merged_steps[-limit:]

    counts = _step_counts(visible_steps)
    counts["total"] = max(task_event_count + len(runtime_steps) + len(pipeline_llm_steps) + len(pipeline_tool_steps), len(visible_steps))

    return {
        "task_run_id": task_run.id,
        "chatroom_id": task_run.chatroom_id,
        "project_id": task_run.project_id,
        "client_turn_id": task_run.client_turn_id,
        "captured_at": datetime.now().isoformat(),
        "counts": counts,
        "runtime_message_ids": runtime_message_ids,
        "steps": visible_steps,
    }


@router.get("/approval-queue")
async def get_monitor_approval_queue(
    status: str = Query("all", pattern="^(all|pending|approved|rejected)$"),
    limit: int = Query(120, ge=10, le=300),
    db: Session = Depends(get_db),
):
    status_value = None if status == "all" else status
    items = list_approval_queue_items(db, status=status_value, limit=limit)
    counts = {
        "all": db.query(ApprovalQueueItem).count(),
        "pending": db.query(ApprovalQueueItem).filter(ApprovalQueueItem.status == "pending").count(),
        "approved": db.query(ApprovalQueueItem).filter(ApprovalQueueItem.status == "approved").count(),
        "rejected": db.query(ApprovalQueueItem).filter(ApprovalQueueItem.status == "rejected").count(),
    }
    entries = [
        serialize_monitor_approval_queue_item(
            item,
            chat_title=item.chatroom.title if item.chatroom else None,
            project_name=item.project.name if item.project else None,
            task_run=item.task_run,
        )
        for item in items
    ]
    return {
        "captured_at": datetime.now().isoformat(),
        "status": status,
        "counts": counts,
        "entries": entries,
    }


@router.get("/approval-audit")
async def get_monitor_approval_audit(
    decision: str = Query("all", pattern="^(all|approve|reject|allow|deny|allow_no_timeout)$"),
    event_kind: str = Query("all"),
    source: str = Query("all"),
    tool_name: str = Query("all"),
    project_id: int | None = None,
    chatroom_id: int | None = None,
    limit: int = Query(200, ge=10, le=500),
    db: Session = Depends(get_db),
):
    entries = [
        serialize_approval_audit_log(row)
        for row in list_approval_audit_logs(
            db,
            decision=decision,
            event_kind=(event_kind or "all").strip() or "all",
            source=(source or "all").strip() or "all",
            tool_name=(tool_name or "all").strip() or "all",
            project_id=project_id,
            chatroom_id=chatroom_id,
            limit=limit,
        )
    ]
    counts = {
        "all": db.query(ApprovalAuditLog).count(),
        "approve": db.query(ApprovalAuditLog).filter(ApprovalAuditLog.decision == "approve").count(),
        "reject": db.query(ApprovalAuditLog).filter(ApprovalAuditLog.decision == "reject").count(),
        "allow": db.query(ApprovalAuditLog).filter(ApprovalAuditLog.decision == "allow").count(),
        "deny": db.query(ApprovalAuditLog).filter(ApprovalAuditLog.decision == "deny").count(),
        "remembered": db.query(ApprovalAuditLog).filter(ApprovalAuditLog.event_kind == "authorization_rule_saved").count(),
        "automatic": db.query(ApprovalAuditLog).filter(ApprovalAuditLog.event_kind == "authorization_rule_matched").count(),
    }
    return {
        "captured_at": datetime.now().isoformat(),
        "decision": decision,
        "event_kind": event_kind,
        "source": source,
        "counts": counts,
        "entries": entries,
    }


@router.get("/context-budget-events")
async def get_monitor_context_budget_events(
    limit: int = Query(120, ge=10, le=500),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(TaskRunEvent, TaskRun, Chatroom, Project)
        .join(TaskRun, TaskRunEvent.task_run_id == TaskRun.id)
        .join(Chatroom, TaskRun.chatroom_id == Chatroom.id)
        .outerjoin(Project, TaskRun.project_id == Project.id)
        .filter(TaskRunEvent.event_type == CONTEXT_BUDGET_EVENT_TYPE)
        .order_by(desc(TaskRunEvent.created_at), desc(TaskRunEvent.id))
        .limit(limit)
        .all()
    )
    entries = [
        serialize_monitor_context_budget_item(
            event,
            task_run=task_run,
            chat_title=chatroom.title,
            project_name=project.name if project else None,
        )
        for event, task_run, chatroom, project in rows
    ]
    total = db.query(TaskRunEvent).filter(TaskRunEvent.event_type == CONTEXT_BUDGET_EVENT_TYPE).count()
    return {
        "captured_at": datetime.now().isoformat(),
        "limit": limit,
        "counts": {
            "total": total,
            "returned": len(entries),
            "dropped": sum(int(item.get("dropped_count") or 0) for item in entries),
            "truncated": sum(int(item.get("truncated_count") or 0) for item in entries),
        },
        "entries": entries,
    }


@router.get("/context-optimization-evaluation")
async def get_monitor_context_optimization_evaluation(
    limit: int = Query(120, ge=10, le=500),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(TaskRunEvent)
        .filter(TaskRunEvent.event_type == CONTEXT_BUDGET_EVENT_TYPE)
        .order_by(desc(TaskRunEvent.created_at), desc(TaskRunEvent.id))
        .limit(limit)
        .all()
    )
    total = db.query(TaskRunEvent).filter(TaskRunEvent.event_type == CONTEXT_BUDGET_EVENT_TYPE).count()
    evaluation = evaluate_context_budget_event_observations(
        [{"payload_json": row.payload_json} for row in rows],
    )
    return {
        "captured_at": datetime.now().isoformat(),
        "limit": limit,
        "counts": {
            "total": total,
            "returned": len(rows),
        },
        "evaluation": evaluation,
    }


@router.get("/compaction-checkpoints")
async def get_monitor_compaction_checkpoints(
    limit: int = Query(120, ge=10, le=500),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(TaskRunEvent, TaskRun, Chatroom, Project)
        .join(TaskRun, TaskRunEvent.task_run_id == TaskRun.id)
        .join(Chatroom, TaskRun.chatroom_id == Chatroom.id)
        .outerjoin(Project, TaskRun.project_id == Project.id)
        .filter(TaskRunEvent.event_type == SEMANTIC_COMPACTION_EVENT_TYPE)
        .order_by(desc(TaskRunEvent.created_at), desc(TaskRunEvent.id))
        .limit(limit)
        .all()
    )
    entries = [
        serialize_monitor_provider_compaction_item(
            event,
            task_run=task_run,
            chat_title=chatroom.title,
            project_name=project.name if project else None,
        )
        for event, task_run, chatroom, project in rows
    ]
    total = db.query(TaskRunEvent).filter(TaskRunEvent.event_type == SEMANTIC_COMPACTION_EVENT_TYPE).count()
    return {
        "captured_at": datetime.now().isoformat(),
        "limit": limit,
        "counts": {
            "total": total,
            "returned": len(entries),
            "local_structured_summary": sum(
                1 for item in entries if item.get("compaction_kind") == "local_structured_summary"
            ),
        },
        "entries": entries,
    }


@router.get("/files")
async def get_monitor_files(
    limit: int = Query(200, ge=20, le=1000),
    tool: str = Query("all"),
    query: str = Query(""),
    db: Session = Depends(get_db),
):
    normalized_tool = tool.strip()
    if normalized_tool != "all" and normalized_tool not in FILE_MONITOR_TOOLS:
        raise HTTPException(status_code=400, detail="Unsupported file monitor tool")

    projection_health = _projection_health_payload(db)
    query_text = query.strip().lower()
    projection_query = _file_monitor_projection_query(
        db,
        normalized_tool=normalized_tool,
        query_text=query_text,
    )
    path_expr = _file_monitor_path_sql_expr()
    agent_expr = _file_monitor_agent_sql_expr()
    projections = (
        projection_query
        .order_by(desc(RuntimeCardProjection.created_at), desc(RuntimeCardProjection.id))
        .limit(limit)
        .all()
    )

    entries: list[dict[str, Any]] = []
    for proj, chatroom, project in projections:
        if not proj.card_json:
            continue
        try:
            card = json.loads(proj.card_json)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(card, dict):
            continue
        entry = _file_monitor_item_from_projection(
            proj=proj,
            chatroom=chatroom,
            project=project,
            card=card,
        )
        if not entry:
            continue
        entries.append(entry)

    totals_row = projection_query.with_entities(
        func.count(RuntimeCardProjection.id),
        func.sum(case((RuntimeCardProjection.tool_name == "read_file", 1), else_=0)),
        func.sum(case((RuntimeCardProjection.tool_name.in_(["write_file", "delete_file"]), 1), else_=0)),
        func.sum(case((RuntimeCardProjection.tool_name == "list_files", 1), else_=0)),
        func.sum(case((RuntimeCardProjection.tool_name == "search_files", 1), else_=0)),
        func.sum(case((RuntimeCardProjection.tool_name == "delete_file", 1), else_=0)),
        func.sum(case((RuntimeCardProjection.success.is_(False), 1), else_=0)),
        func.count(distinct(case((path_expr != "", path_expr), else_=None))),
    ).one()
    total_count = int(totals_row[0] or 0)
    reads_count = int(totals_row[1] or 0)
    writes_count = int(totals_row[2] or 0)
    lists_count = int(totals_row[3] or 0)
    searches_count = int(totals_row[4] or 0)
    deletes_count = int(totals_row[5] or 0)
    error_count = int(totals_row[6] or 0)
    unique_paths_count = int(totals_row[7] or 0)

    tool_counts = projection_query.with_entities(
        RuntimeCardProjection.tool_name,
        func.count(RuntimeCardProjection.id),
    ).group_by(RuntimeCardProjection.tool_name).all()
    agent_counts = projection_query.with_entities(
        agent_expr.label("agent"),
        func.count(RuntimeCardProjection.id),
    ).group_by(agent_expr).all()

    return {
        "captured_at": datetime.now().isoformat(),
        "limit": limit,
        "tool": normalized_tool,
        "query": query,
        "counts": {
            "total": total_count,
            "reads": reads_count,
            "writes": writes_count,
            "lists": lists_count,
            "searches": searches_count,
            "deletes": deletes_count,
            "errors": error_count,
            "unique_paths": unique_paths_count,
        },
        "by_tool": [
            {"tool_name": str(name or "tool"), "count": int(count or 0)}
            for name, count in sorted(tool_counts, key=lambda item: str(item[0] or "tool"))
        ],
        "by_agent": [
            {"agent": str(name or "agent"), "count": int(count or 0)}
            for name, count in sorted(agent_counts, key=lambda item: str(item[0] or "agent"))
        ],
        "diagnostics": {
            "projection_health": projection_health,
            "scope": {
                "mode": "explicit_file_tools",
                "tool_names": sorted(FILE_MONITOR_TOOLS),
                "shell_activity_included": False,
            },
        },
        "entries": entries,
    }


@router.get("/overview")
async def get_monitor_overview(
    range: str = Query("24h", pattern="^(1h|6h|24h|7d|30d)$"),
    db: Session = Depends(get_db),
    telemetry_db: Session = Depends(get_telemetry_db),
):
    active_agent_count = db.query(Agent).filter(Agent.is_active.is_(True)).count()
    agent_count = db.query(Agent).count()
    project_count = db.query(Project).count()
    chatroom_count = db.query(Chatroom).count()
    visible_chat_count = db.query(Chatroom).filter(Chatroom.is_visible_in_chat_list.is_(True)).count()
    message_count = db.query(Message).filter(Message.message_type != "runtime_card").count()
    runtime_card_count = db.query(Message).filter(Message.message_type == "runtime_card").count()
    projection_health = _projection_health_payload(db)
    latest_message = db.query(Message).order_by(desc(Message.created_at), desc(Message.id)).first()
    context_budget_event_count = db.query(TaskRunEvent).filter(TaskRunEvent.event_type == CONTEXT_BUDGET_EVENT_TYPE).count()
    semantic_compaction_count = db.query(TaskRunEvent).filter(TaskRunEvent.event_type == SEMANTIC_COMPACTION_EVENT_TYPE).count()
    range_value = range if range in USAGE_RANGES else "24h"
    usage_window = _build_overview_usage_window(db, range_value=range_value)
    task_summary = _build_overview_task_summary(db, telemetry_db=telemetry_db, range_value=range_value)
    llm_summary = _build_overview_llm_summary(telemetry_db, range_value=range_value)
    approval_summary = _build_overview_approval_summary(db)
    context_budget_summary = _build_overview_context_budget_summary(db)
    return {
        "captured_at": datetime.now().isoformat(),
        "system": {
            "status": "degraded" if projection_health["missing"] > 0 else "healthy",
            "version": "1.0.0",
            "stats": {
                "agents": agent_count,
                "active_agents": active_agent_count,
                "projects": project_count,
                "chatrooms": chatroom_count,
                "visible_chats": visible_chat_count,
                "messages": message_count,
                "runtime_cards": runtime_card_count,
                "runtime_card_projections": projection_health["projections"],
                "runtime_card_projection_missing": projection_health["missing"],
                "approval_queue_total": approval_summary["queue"]["total"],
                "approval_queue_pending": approval_summary["queue"]["pending"],
                "context_budget_events": context_budget_event_count,
                "semantic_compactions": semantic_compaction_count,
            },
            "features": {
                "llm_enabled": True,
                "websocket_enabled": True,
                "tools_enabled": True,
                "memory_enabled": True,
            },
            "collaboration": {
                **get_runtime_collaboration_status(),
                "status": "active",
            },
            "projections": projection_health,
            "last_message_at": latest_message.created_at.isoformat() if latest_message and latest_message.created_at else None,
        },
        "usage_window": usage_window,
        "tasks": task_summary,
        "llm": llm_summary,
        "approvals": approval_summary,
        "context_budget": context_budget_summary,
    }


@router.get("/overview/activity")
async def get_monitor_overview_activity(
    runtime_limit: int = Query(OVERVIEW_ACTIVITY_RUNTIME_LIMIT, ge=12, le=160),
    summary_window: int = Query(96, ge=24, le=320),
    message_limit: int = Query(OVERVIEW_ACTIVITY_MESSAGE_LIMIT, ge=8, le=120),
    context_budget_limit: int = Query(OVERVIEW_ACTIVITY_CONTEXT_BUDGET_LIMIT, ge=4, le=64),
    db: Session = Depends(get_db),
):
    recent_runtime, _summary_messages = _query_recent_runtime_activity(
        db,
        runtime_limit=runtime_limit,
        summary_window=summary_window,
    )
    recent_messages = _query_recent_message_activity(db, message_limit=message_limit)
    recent_context_budget_events = _query_recent_context_budget_activity(db, limit=context_budget_limit)
    return {
        "captured_at": datetime.now().isoformat(),
        "recent_runtime": recent_runtime,
        "recent_messages": recent_messages,
        "recent_context_budget_events": recent_context_budget_events,
    }


@router.post("/backfill-projections")
async def run_backfill_projections(db: Session = Depends(get_db)):
    """Backfill runtime_card_projections from existing messages."""
    from services.monitor_projection import backfill_runtime_card_projections
    inserted = backfill_runtime_card_projections(db)
    return {
        "status": "ok",
        "inserted": inserted,
        "health": _projection_health_payload(db),
    }


@router.post("/watchdog/sweep")
def trigger_watchdog_sweep(
    stale_running_threshold: int = Query(default=30, ge=1, le=1440),
    stale_paused_threshold: int = Query(default=60, ge=1, le=1440),
    db: Session = Depends(get_db),
):
    """Manually trigger a watchdog sweep of stale TaskRuns.

    Also runs automatically on startup; this endpoint is for manual
    invocation or external cron integration.
    """
    from services.task_run_watchdog import run_watchdog_sweep

    result = run_watchdog_sweep(
        db,
        stale_running_threshold_minutes=stale_running_threshold,
        stale_paused_threshold_minutes=stale_paused_threshold,
    )
    return {
        "status": "ok",
        **result,
    }
