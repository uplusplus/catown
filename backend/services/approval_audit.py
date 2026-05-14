# -*- coding: utf-8 -*-
"""Persistent audit log helpers for approval decisions and remembered rules."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session


def _model():
    from models.database import ApprovalAuditLog

    return ApprovalAuditLog


def _dump_json(payload: Any) -> str:
    if payload is None:
        return "{}"
    try:
        return json.dumps(payload, ensure_ascii=False)
    except TypeError:
        return json.dumps({"value": str(payload)}, ensure_ascii=False)


def _load_json(payload_json: str | None) -> dict[str, Any]:
    if not payload_json:
        return {}
    try:
        loaded = json.loads(payload_json)
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _compact(value: Any, *, limit: int = 240) -> str:
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


def _field(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _parse_arguments_payload(request_payload: dict[str, Any]) -> dict[str, Any]:
    raw_arguments = request_payload.get("arguments")
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if isinstance(raw_arguments, str) and raw_arguments.strip():
        try:
            loaded = json.loads(raw_arguments)
        except Exception:
            return {}
        return loaded if isinstance(loaded, dict) else {}

    nested = request_payload.get("request_payload")
    if isinstance(nested, dict):
        return _parse_arguments_payload(nested)
    raw_nested_args = request_payload.get("tool_arguments")
    if isinstance(raw_nested_args, dict):
        return raw_nested_args
    return {}


def _approval_fingerprint_payload(row: Any, request_payload: dict[str, Any], resolution_payload: dict[str, Any]) -> dict[str, Any]:
    remembered_rule = resolution_payload.get("remembered_rule")
    if not isinstance(remembered_rule, dict):
        remembered_rule = {}

    matcher_value = row.matcher_value or remembered_rule.get("matcher_value")
    matcher_type = row.matcher_type or remembered_rule.get("matcher_type")
    if matcher_value:
        fingerprint_input: dict[str, Any] = {
            "tool_name": row.tool_name or remembered_rule.get("tool_name"),
            "matcher_type": matcher_type,
            "scope": row.scope or remembered_rule.get("scope"),
            "command_preview": row.command_preview or remembered_rule.get("command_preview"),
        }
        arguments = _parse_arguments_payload(request_payload)
        if arguments:
            fingerprint_input["raw_arguments"] = arguments
            if "command" in arguments:
                fingerprint_input["command"] = arguments.get("command")
            if "cwd" in arguments:
                fingerprint_input["cwd"] = arguments.get("cwd")
        return {
            "approval_fingerprint": matcher_value,
            "approval_fingerprint_kind": matcher_type or "authorization_rule",
            "approval_fingerprint_input": fingerprint_input,
        }

    request_key = None
    if isinstance(request_payload.get("request_key"), str):
        request_key = request_payload.get("request_key")
    if not request_key and isinstance(resolution_payload.get("request_payload"), dict):
        nested_payload = resolution_payload.get("request_payload")
        if isinstance(nested_payload.get("request_key"), str):
            request_key = nested_payload.get("request_key")
    if request_key:
        return {
            "approval_fingerprint": request_key,
            "approval_fingerprint_kind": "queue_request_key",
            "approval_fingerprint_input": {
                "task_run_id": row.task_run_id,
                "agent_name": row.agent_name,
                "status": request_payload.get("status"),
                "tool_name": request_payload.get("tool_name") or row.tool_name,
                "arguments": request_payload.get("arguments"),
                "blocked_reason": request_payload.get("blocked_reason") or row.reason,
            },
        }

    return {
        "approval_fingerprint": None,
        "approval_fingerprint_kind": None,
        "approval_fingerprint_input": {},
    }


def record_approval_audit(
    db: Session,
    *,
    event_kind: str,
    decision: str,
    source: str = "runtime",
    resolved_by: str | None = None,
    queue_item: Any | None = None,
    preference: Any | None = None,
    task_run_id: int | None = None,
    chatroom_id: int | None = None,
    project_id: int | None = None,
    pipeline_run_id: int | None = None,
    pipeline_stage_id: int | None = None,
    agent_name: str | None = None,
    target_kind: str | None = None,
    target_name: str | None = None,
    tool_name: str | None = None,
    scope: str | None = None,
    matcher_type: str | None = None,
    matcher_value: str | None = None,
    command_preview: str | None = None,
    reason: str | None = None,
    request_payload: Any = None,
    resolution_payload: Any = None,
) -> Any:
    ApprovalAuditLog = _model()
    row = ApprovalAuditLog(
        event_kind=(event_kind or "approval_event").strip() or "approval_event",
        decision=(decision or "unknown").strip().lower() or "unknown",
        source=(source or "runtime").strip() or "runtime",
        resolved_by=(resolved_by or "").strip() or None,
        queue_item_id=getattr(queue_item, "id", None),
        task_run_id=task_run_id if task_run_id is not None else getattr(queue_item, "task_run_id", None),
        chatroom_id=chatroom_id if chatroom_id is not None else getattr(queue_item, "chatroom_id", None),
        project_id=project_id if project_id is not None else getattr(queue_item, "project_id", None),
        pipeline_run_id=pipeline_run_id if pipeline_run_id is not None else getattr(queue_item, "pipeline_run_id", None),
        pipeline_stage_id=pipeline_stage_id if pipeline_stage_id is not None else getattr(queue_item, "pipeline_stage_id", None),
        preference_id=_field(preference, "id"),
        agent_name=(agent_name or getattr(queue_item, "agent_name", None) or _field(preference, "agent_name") or "").strip() or None,
        target_kind=(target_kind or getattr(queue_item, "target_kind", None) or "").strip() or None,
        target_name=(target_name or getattr(queue_item, "target_name", None) or "").strip() or None,
        tool_name=(tool_name or _field(preference, "tool_name") or getattr(queue_item, "target_name", None) or "").strip() or None,
        scope=(scope or _field(preference, "scope") or "").strip() or None,
        matcher_type=(matcher_type or _field(preference, "matcher_type") or "").strip() or None,
        matcher_value=(matcher_value or _field(preference, "matcher_value") or "").strip() or None,
        command_preview=(command_preview or _field(preference, "command_preview") or "").strip() or None,
        reason=(reason or getattr(queue_item, "summary", None) or "").strip() or None,
        request_payload_json=_dump_json(request_payload),
        resolution_payload_json=_dump_json(resolution_payload),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_approval_audit_logs(
    db: Session,
    *,
    decision: str | None = None,
    event_kind: str | None = None,
    source: str | None = None,
    tool_name: str | None = None,
    project_id: int | None = None,
    chatroom_id: int | None = None,
    limit: int = 200,
) -> list[Any]:
    ApprovalAuditLog = _model()
    query = db.query(ApprovalAuditLog)
    if decision and decision != "all":
        query = query.filter(ApprovalAuditLog.decision == decision)
    if event_kind and event_kind != "all":
        query = query.filter(ApprovalAuditLog.event_kind == event_kind)
    if source and source != "all":
        query = query.filter(ApprovalAuditLog.source == source)
    if tool_name and tool_name != "all":
        query = query.filter(ApprovalAuditLog.tool_name == tool_name)
    if project_id is not None:
        query = query.filter(ApprovalAuditLog.project_id == project_id)
    if chatroom_id is not None:
        query = query.filter(ApprovalAuditLog.chatroom_id == chatroom_id)
    return query.order_by(ApprovalAuditLog.created_at.desc(), ApprovalAuditLog.id.desc()).limit(max(1, limit)).all()


def serialize_approval_audit_log(row: Any) -> dict[str, Any]:
    request_payload = _load_json(getattr(row, "request_payload_json", None))
    resolution_payload = _load_json(getattr(row, "resolution_payload_json", None))
    fingerprint_payload = _approval_fingerprint_payload(row, request_payload, resolution_payload)
    preview = (
        row.reason
        or row.command_preview
        or _compact(request_payload.get("blocked_reason"))
        or _compact(request_payload.get("arguments"))
        or _compact(request_payload)
    )
    return {
        "id": row.id,
        "event_kind": row.event_kind,
        "decision": row.decision,
        "source": row.source,
        "resolved_by": row.resolved_by,
        "queue_item_id": row.queue_item_id,
        "task_run_id": row.task_run_id,
        "chatroom_id": row.chatroom_id,
        "project_id": row.project_id,
        "pipeline_run_id": row.pipeline_run_id,
        "pipeline_stage_id": row.pipeline_stage_id,
        "preference_id": row.preference_id,
        "agent_name": row.agent_name,
        "target_kind": row.target_kind,
        "target_name": row.target_name,
        "tool_name": row.tool_name,
        "scope": row.scope,
        "matcher_type": row.matcher_type,
        "matcher_value": row.matcher_value,
        "command_preview": row.command_preview,
        **fingerprint_payload,
        "reason": row.reason,
        "preview": preview,
        "request_payload": request_payload,
        "resolution_payload": resolution_payload,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
