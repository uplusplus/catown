# -*- coding: utf-8 -*-
"""Run-level orchestration ledger helpers for chat/runtime execution."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from models.database import Chatroom, Project, TaskRun, TaskRunEvent
from services.approval_queue import serialize_approval_queue_item


def get_task_run(db: Session, task_run_id: int | None) -> Optional[TaskRun]:
    if not task_run_id:
        return None
    return db.query(TaskRun).filter(TaskRun.id == task_run_id).first()


def create_task_run(
    db: Session,
    *,
    chatroom_id: int,
    project_id: int | None,
    origin_message_id: int | None,
    client_turn_id: str | None,
    run_kind: str,
    user_request: str,
    initiator: str = "user",
    target_agent_name: str | None = None,
    title: str | None = None,
) -> TaskRun:
    if origin_message_id is not None:
        existing = (
            db.query(TaskRun)
            .filter(TaskRun.origin_message_id == origin_message_id)
            .first()
        )
        if existing is not None:
            return existing

    task_run = TaskRun(
        chatroom_id=chatroom_id,
        project_id=project_id,
        origin_message_id=origin_message_id,
        client_turn_id=(client_turn_id or "").strip() or None,
        run_kind=run_kind or "chat_turn",
        status="running",
        title=title or _default_title(user_request),
        user_request=(user_request or "").strip(),
        initiator=initiator or "user",
        target_agent_name=(target_agent_name or "").strip() or None,
    )
    db.add(task_run)
    db.commit()
    db.refresh(task_run)
    _schedule_monitor_task_run_broadcast(db, task_run.id, change_reason="task_run_created")
    return task_run


def update_task_run(
    db: Session,
    task_run: TaskRun | None,
    *,
    run_kind: str | None = None,
    status: str | None = None,
    title: str | None = None,
    target_agent_name: str | None = None,
    summary: str | None = None,
    completed: bool = False,
) -> Optional[TaskRun]:
    if task_run is None:
        return None

    changed = False
    if run_kind and task_run.run_kind != run_kind:
        task_run.run_kind = run_kind
        changed = True
    if status and task_run.status != status:
        task_run.status = status
        changed = True
    if title and task_run.title != title:
        task_run.title = title
        changed = True
    if target_agent_name is not None and task_run.target_agent_name != ((target_agent_name or "").strip() or None):
        task_run.target_agent_name = (target_agent_name or "").strip() or None
        changed = True
    if summary is not None and task_run.summary != summary:
        task_run.summary = summary
        changed = True
    if completed:
        task_run.completed_at = datetime.now()
        if getattr(task_run, "recovery_owner", None) is not None:
            task_run.recovery_owner = None
            changed = True
        if getattr(task_run, "recovery_claimed_at", None) is not None:
            task_run.recovery_claimed_at = None
            changed = True
        if getattr(task_run, "recovery_lease_expires_at", None) is not None:
            task_run.recovery_lease_expires_at = None
            changed = True
        changed = True

    if changed:
        db.add(task_run)
        db.commit()
        db.refresh(task_run)
        _schedule_monitor_task_run_broadcast(db, task_run.id, change_reason="task_run_updated")
    return task_run


def complete_task_run(
    db: Session,
    task_run: TaskRun | None,
    *,
    status: str = "completed",
    summary: str = "",
) -> Optional[TaskRun]:
    return update_task_run(
        db,
        task_run,
        status=status,
        summary=(summary or "").strip() or None,
        completed=True,
    )


def append_task_event(
    db: Session,
    task_run: TaskRun | None,
    event_type: str,
    *,
    agent_name: str | None = None,
    message_id: int | None = None,
    summary: str = "",
    payload: Any = None,
) -> Optional[TaskRunEvent]:
    if task_run is None:
        return None

    next_index = (
        db.query(func.max(TaskRunEvent.event_index))
        .filter(TaskRunEvent.task_run_id == task_run.id)
        .scalar()
        or 0
    ) + 1

    event = TaskRunEvent(
        task_run_id=task_run.id,
        event_index=next_index,
        event_type=event_type,
        agent_name=(agent_name or "").strip() or None,
        message_id=message_id,
        summary=(summary or "").strip() or None,
        payload_json=_dump_payload(payload),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    _schedule_monitor_task_run_broadcast(
        db,
        task_run.id,
        change_reason="task_run_event_appended",
        task_event_type=event.event_type,
    )
    return event


def serialize_task_run_summary(task_run: TaskRun) -> dict[str, Any]:
    approval_items = list(getattr(task_run, "approval_queue_items", []) or [])
    return {
        "id": task_run.id,
        "chatroom_id": task_run.chatroom_id,
        "project_id": task_run.project_id,
        "origin_message_id": task_run.origin_message_id,
        "client_turn_id": task_run.client_turn_id,
        "run_kind": task_run.run_kind,
        "status": task_run.status,
        "title": task_run.title,
        "user_request": task_run.user_request,
        "initiator": task_run.initiator,
        "target_agent_name": task_run.target_agent_name,
        "recovery_owner": task_run.recovery_owner,
        "recovery_claimed_at": task_run.recovery_claimed_at.isoformat() if task_run.recovery_claimed_at else None,
        "recovery_lease_expires_at": (
            task_run.recovery_lease_expires_at.isoformat() if task_run.recovery_lease_expires_at else None
        ),
        "summary": task_run.summary,
        "event_count": len(task_run.events or []),
        "approval_queue_count": len(approval_items),
        "pending_approval_count": sum(1 for item in approval_items if (item.status or "") == "pending"),
        "created_at": task_run.created_at.isoformat() if task_run.created_at else None,
        "updated_at": task_run.updated_at.isoformat() if task_run.updated_at else None,
        "completed_at": task_run.completed_at.isoformat() if task_run.completed_at else None,
    }


def serialize_task_run_detail(task_run: TaskRun) -> dict[str, Any]:
    payload = serialize_task_run_summary(task_run)
    payload["events"] = [
        {
            "id": event.id,
            "event_index": event.event_index,
            "event_type": event.event_type,
            "agent_name": event.agent_name,
            "message_id": event.message_id,
            "summary": event.summary,
            "payload": _load_payload(event.payload_json),
            "created_at": event.created_at.isoformat() if event.created_at else None,
        }
        for event in task_run.events
    ]
    payload["approval_queue_items"] = [
        serialize_approval_queue_item(item)
        for item in list(getattr(task_run, "approval_queue_items", []) or [])
    ]
    return payload


def serialize_monitor_task_run_summary(
    task_run: TaskRun,
    *,
    chat_title: str,
    project_name: str | None = None,
) -> dict[str, Any]:
    payload = serialize_task_run_summary(task_run)
    payload["chat_title"] = chat_title
    payload["project_name"] = project_name
    payload["latest_event_type"] = task_run.events[-1].event_type if task_run.events else None
    return payload


def _default_title(user_request: str) -> str:
    text = " ".join((user_request or "").strip().split())
    if not text:
        return "Task run"
    return text[:77] + "..." if len(text) > 80 else text


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
        return json.loads(payload_json)
    except json.JSONDecodeError:
        return {"raw": payload_json}


def _schedule_monitor_task_run_broadcast(
    db: Session,
    task_run_id: int | None,
    *,
    change_reason: str,
    task_event_type: str | None = None,
) -> None:
    payload = _build_monitor_task_run_payload(
        db,
        task_run_id,
        change_reason=change_reason,
        task_event_type=task_event_type,
    )
    if payload is None:
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    loop.create_task(_broadcast_monitor_task_run_payload(payload))


def _build_monitor_task_run_payload(
    db: Session,
    task_run_id: int | None,
    *,
    change_reason: str,
    task_event_type: str | None = None,
) -> dict[str, Any] | None:
    if not task_run_id:
        return None

    row = (
        db.query(TaskRun, Chatroom, Project)
        .join(Chatroom, TaskRun.chatroom_id == Chatroom.id)
        .outerjoin(Project, TaskRun.project_id == Project.id)
        .filter(TaskRun.id == task_run_id)
        .first()
    )
    if row is None:
        return None

    task_run, chatroom, project = row
    return {
        "change_type": "upsert",
        "change_reason": change_reason,
        "task_event_type": task_event_type,
        "captured_at": datetime.now().isoformat(),
        "entry": serialize_monitor_task_run_summary(
            task_run,
            chat_title=chatroom.title,
            project_name=project.name if project else None,
        ),
        "detail": serialize_task_run_detail(task_run),
    }


async def _broadcast_monitor_task_run_payload(payload: dict[str, Any]) -> None:
    from routes.websocket import websocket_manager

    await websocket_manager.broadcast_to_topic(
        {
            "type": "monitor_task_run",
            "payload": payload,
        },
        "monitor",
    )
