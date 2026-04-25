# -*- coding: utf-8 -*-
"""Persistent approval/escalation queue for blocked runtime actions."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from models.database import ApprovalQueueItem, TaskRun


def get_approval_queue_item(db: Session, item_id: int | None) -> Optional[ApprovalQueueItem]:
    if not item_id:
        return None
    return db.query(ApprovalQueueItem).filter(ApprovalQueueItem.id == item_id).first()


def find_pending_queue_item(
    db: Session,
    *,
    request_key: str | None = None,
    pipeline_run_id: int | None = None,
    pipeline_stage_id: int | None = None,
    target_kind: str | None = None,
    target_name: str | None = None,
) -> Optional[ApprovalQueueItem]:
    query = db.query(ApprovalQueueItem).filter(ApprovalQueueItem.status == "pending")
    if request_key:
        query = query.filter(ApprovalQueueItem.request_key == request_key)
    if pipeline_run_id is not None:
        query = query.filter(ApprovalQueueItem.pipeline_run_id == pipeline_run_id)
    if pipeline_stage_id is not None:
        query = query.filter(ApprovalQueueItem.pipeline_stage_id == pipeline_stage_id)
    if target_kind:
        query = query.filter(ApprovalQueueItem.target_kind == target_kind)
    if target_name:
        query = query.filter(ApprovalQueueItem.target_name == target_name)
    return query.order_by(ApprovalQueueItem.created_at.desc(), ApprovalQueueItem.id.desc()).first()


def create_approval_queue_item(
    db: Session,
    *,
    task_run: TaskRun | None,
    chatroom_id: int | None,
    project_id: int | None,
    queue_kind: str,
    source: str,
    title: str,
    summary: str = "",
    agent_name: str | None = None,
    target_kind: str,
    target_name: str | None = None,
    request_key: str | None = None,
    request_payload: Any = None,
    pipeline_run_id: int | None = None,
    pipeline_stage_id: int | None = None,
) -> ApprovalQueueItem:
    normalized_request_key = (request_key or "").strip() or None
    if normalized_request_key:
        existing = find_pending_queue_item(db, request_key=normalized_request_key)
        if existing is not None:
            return existing

    resolved_chatroom_id = chatroom_id if chatroom_id is not None else getattr(task_run, "chatroom_id", None)
    if resolved_chatroom_id is None:
        raise ValueError("chatroom_id is required to create an approval queue item")

    item = ApprovalQueueItem(
        task_run_id=getattr(task_run, "id", None),
        chatroom_id=resolved_chatroom_id,
        project_id=project_id if project_id is not None else getattr(task_run, "project_id", None),
        pipeline_run_id=pipeline_run_id,
        pipeline_stage_id=pipeline_stage_id,
        queue_kind=(queue_kind or "approval").strip() or "approval",
        status="pending",
        source=(source or "runtime").strip() or "runtime",
        title=(title or "Approval request").strip() or "Approval request",
        summary=(summary or "").strip() or None,
        agent_name=(agent_name or "").strip() or None,
        target_kind=(target_kind or "tool").strip() or "tool",
        target_name=(target_name or "").strip() or None,
        request_key=normalized_request_key,
        request_payload_json=_dump_payload(request_payload),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def resolve_approval_queue_item(
    db: Session,
    item: ApprovalQueueItem | None,
    *,
    status: str,
    resolved_by: str | None = None,
    resolution_note: str | None = None,
    resolution_payload: Any = None,
) -> ApprovalQueueItem | None:
    if item is None:
        return None
    normalized_status = (status or "").strip().lower()
    if normalized_status not in {"approved", "rejected"}:
        raise ValueError(f"Unsupported approval queue resolution status: {status}")
    if item.status == normalized_status:
        return item

    item.status = normalized_status
    item.resolved_by = (resolved_by or "").strip() or None
    item.resolution_note = (resolution_note or "").strip() or None
    item.resolution_payload_json = _dump_payload(resolution_payload)
    item.resolved_at = datetime.now()
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def list_approval_queue_items(
    db: Session,
    *,
    status: str | None = None,
    queue_kind: str | None = None,
    chatroom_id: int | None = None,
    project_id: int | None = None,
    task_run_id: int | None = None,
    limit: int = 50,
) -> list[ApprovalQueueItem]:
    query = db.query(ApprovalQueueItem).order_by(ApprovalQueueItem.created_at.desc(), ApprovalQueueItem.id.desc())
    if status:
        query = query.filter(ApprovalQueueItem.status == status)
    if queue_kind:
        query = query.filter(ApprovalQueueItem.queue_kind == queue_kind)
    if chatroom_id is not None:
        query = query.filter(ApprovalQueueItem.chatroom_id == chatroom_id)
    if project_id is not None:
        query = query.filter(ApprovalQueueItem.project_id == project_id)
    if task_run_id is not None:
        query = query.filter(ApprovalQueueItem.task_run_id == task_run_id)
    return query.limit(max(1, limit)).all()


def serialize_approval_queue_item(item: ApprovalQueueItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "task_run_id": item.task_run_id,
        "chatroom_id": item.chatroom_id,
        "project_id": item.project_id,
        "pipeline_run_id": item.pipeline_run_id,
        "pipeline_stage_id": item.pipeline_stage_id,
        "queue_kind": item.queue_kind,
        "status": item.status,
        "source": item.source,
        "title": item.title,
        "summary": item.summary,
        "agent_name": item.agent_name,
        "target_kind": item.target_kind,
        "target_name": item.target_name,
        "request_key": item.request_key,
        "request_payload": _load_payload(item.request_payload_json),
        "resolution_note": item.resolution_note,
        "resolution_payload": _load_payload(item.resolution_payload_json),
        "resolved_by": item.resolved_by,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None,
    }


def _dump_payload(payload: Any) -> str:
    if payload is None:
        return "{}"
    try:
        return json.dumps(payload, ensure_ascii=False)
    except TypeError:
        return json.dumps({"value": str(payload)}, ensure_ascii=False)


def _load_payload(payload_json: str | None) -> Any:
    if not payload_json:
        return {}
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError:
        return {"raw": payload_json}
    return payload if isinstance(payload, dict) else {"value": payload}
