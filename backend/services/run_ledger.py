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
    checkpoint_snapshot = build_task_run_checkpoint_snapshot(task_run)
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
        "checkpoint_snapshot": checkpoint_snapshot,
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
        _serialize_task_run_event(event)
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


def _serialize_task_run_event(event: TaskRunEvent) -> dict[str, Any]:
    raw_payload = _load_payload(event.payload_json)
    continuation_state = _extract_event_continuation_state(raw_payload)
    return {
        "id": event.id,
        "event_index": event.event_index,
        "event_type": event.event_type,
        "agent_name": event.agent_name,
        "message_id": event.message_id,
        "summary": event.summary,
        "payload": raw_payload,
        "continuation_state": continuation_state,
        "continuation_state_summary": summarize_continuation_state(continuation_state),
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def _default_title(user_request: str) -> str:
    text = " ".join((user_request or "").strip().split())
    if not text:
        return "Task run"
    return text[:77] + "..." if len(text) > 80 else text


def build_task_run_checkpoint_snapshot(task_run: TaskRun | None) -> dict[str, Any]:
    if task_run is None:
        return {}

    events = list(getattr(task_run, "events", []) or [])
    approval_items = list(getattr(task_run, "approval_queue_items", []) or [])
    payload_by_event_id = {
        event.id: _load_payload(event.payload_json)
        for event in events
    }

    latest_event = events[-1] if events else None
    latest_agent_turn = next((event for event in reversed(events) if event.event_type == "agent_turn_completed"), None)
    latest_turn_events = _latest_checkpoint_turn_events(events)
    latest_tool_round = next(
        (event for event in reversed(latest_turn_events) if event.event_type == "tool_round_recorded"),
        None,
    )
    latest_tool_blocked = next(
        (event for event in reversed(latest_turn_events) if event.event_type == "tool_call_blocked"),
        None,
    )
    latest_followup = next(
        (event for event in reversed(latest_turn_events) if event.event_type == "approval_queue_item_followup_triggered"),
        None,
    )
    latest_compaction = next((event for event in reversed(events) if event.event_type == "context_compaction"), None)
    latest_runtime_event = next(
        (
            event
            for event in reversed(events)
            if isinstance(payload_by_event_id.get(event.id), dict)
            and payload_by_event_id.get(event.id, {}).get("runtime") is not None
        ),
        None,
    )

    latest_agent_payload = payload_by_event_id.get(latest_agent_turn.id, {}) if latest_agent_turn is not None else {}
    latest_tool_round_payload = payload_by_event_id.get(latest_tool_round.id, {}) if latest_tool_round is not None else {}
    latest_tool_blocked_payload = payload_by_event_id.get(latest_tool_blocked.id, {}) if latest_tool_blocked is not None else {}
    latest_followup_payload = payload_by_event_id.get(latest_followup.id, {}) if latest_followup is not None else {}
    latest_compaction_payload = payload_by_event_id.get(latest_compaction.id, {}) if latest_compaction is not None else {}
    latest_compaction_diagnostics = (
        latest_compaction_payload.get("selector_diagnostics")
        if isinstance(latest_compaction_payload.get("selector_diagnostics"), dict)
        else {}
    )
    latest_runtime_payload = payload_by_event_id.get(latest_runtime_event.id, {}) if latest_runtime_event is not None else {}
    pending_tool_queue_item = next(
        (
            item for item in reversed(approval_items)
            if (item.status or "").strip().lower() == "pending" and (item.target_kind or "").strip().lower() == "tool"
        ),
        None,
    )
    continuation_cursor = _build_task_run_continuation_cursor(
        task_run=task_run,
        latest_tool_round=latest_tool_round,
        latest_tool_round_payload=latest_tool_round_payload,
        latest_tool_blocked=latest_tool_blocked,
        latest_tool_blocked_payload=latest_tool_blocked_payload,
        latest_followup=latest_followup,
        latest_followup_payload=latest_followup_payload,
        latest_runtime_event=latest_runtime_event,
        latest_runtime_payload=latest_runtime_payload,
        pending_tool_queue_item=pending_tool_queue_item,
    )
    turn_local_state = _build_task_run_turn_local_state(
        events=latest_turn_events,
        payload_by_event_id=payload_by_event_id,
        latest_tool_round_payload=latest_tool_round_payload,
        latest_tool_blocked_payload=latest_tool_blocked_payload,
    )

    snapshot = {
        "event_count": len(events),
        "latest_event_type": latest_event.event_type if latest_event is not None else None,
        "latest_event_at": latest_event.created_at.isoformat() if latest_event and latest_event.created_at else None,
        "latest_agent_turn": {
            "agent_name": latest_agent_turn.agent_name if latest_agent_turn is not None else None,
            "message_id": latest_agent_turn.message_id if latest_agent_turn is not None else None,
            "response_preview": latest_agent_payload.get("response_preview") if isinstance(latest_agent_payload, dict) else None,
            "created_at": latest_agent_turn.created_at.isoformat() if latest_agent_turn and latest_agent_turn.created_at else None,
        },
        "latest_compaction": {
            "event_id": latest_compaction.id if latest_compaction is not None else None,
            "dropped_count": (
                latest_compaction_diagnostics.get("summary", {}).get("dropped_count")
                if isinstance(latest_compaction_diagnostics.get("summary"), dict)
                else None
            ),
            "truncated_count": (
                latest_compaction_diagnostics.get("summary", {}).get("truncated_count")
                if isinstance(latest_compaction_diagnostics.get("summary"), dict)
                else None
            ),
            "max_tokens": (
                latest_compaction_diagnostics.get("selector", {}).get("max_tokens")
                if isinstance(latest_compaction_diagnostics.get("selector"), dict)
                else None
            ),
            "created_at": latest_compaction.created_at.isoformat() if latest_compaction and latest_compaction.created_at else None,
        },
        "latest_scheduler_runtime": latest_runtime_payload.get("runtime") if isinstance(latest_runtime_payload, dict) else None,
        "continuation_cursor": continuation_cursor,
        "turn_local_state": turn_local_state,
        "pending_approval_count": sum(1 for item in approval_items if (item.status or "") == "pending"),
        "approval_queue_count": len(approval_items),
        "status": task_run.status,
        "summary": task_run.summary,
    }
    snapshot["continuation_state"] = describe_checkpoint_continuation_state(snapshot)
    return snapshot


def describe_checkpoint_continuation_state(checkpoint_snapshot: Any) -> dict[str, Any]:
    snapshot = checkpoint_snapshot if isinstance(checkpoint_snapshot, dict) else {}
    continuation_cursor = (
        snapshot.get("continuation_cursor")
        if isinstance(snapshot.get("continuation_cursor"), dict)
        else {}
    )
    turn_local_state = (
        snapshot.get("turn_local_state")
        if isinstance(snapshot.get("turn_local_state"), dict)
        else {}
    )
    protocol_tail_messages = (
        turn_local_state.get("protocol_tail_messages")
        if isinstance(turn_local_state.get("protocol_tail_messages"), list)
        else []
    )
    prior_round_summaries = (
        turn_local_state.get("prior_round_summaries")
        if isinstance(turn_local_state.get("prior_round_summaries"), list)
        else []
    )
    consumed_layers: list[str] = []
    if continuation_cursor.get("resume_strategy") == "rebuild_from_runtime_snapshot":
        consumed_layers.append("runtime_snapshot")
    if protocol_tail_messages:
        consumed_layers.append("protocol_tail")
    if prior_round_summaries:
        consumed_layers.append("prior_round_summaries")
    return {
        "consumed": bool(consumed_layers),
        "next_action": continuation_cursor.get("next_action"),
        "resume_strategy": continuation_cursor.get("resume_strategy"),
        "consumed_layers": consumed_layers,
        "protocol_tail_message_count": len(protocol_tail_messages),
        "prior_round_summary_count": len(prior_round_summaries),
    }


def summarize_continuation_state(continuation_state: Any) -> str | None:
    state = continuation_state if isinstance(continuation_state, dict) else {}
    if not state.get("consumed"):
        return None

    parts: list[str] = []
    next_action = str(state.get("next_action") or "").strip()
    resume_strategy = str(state.get("resume_strategy") or "").strip()
    if next_action:
        parts.append(next_action.replace("_", " "))
    if resume_strategy:
        parts.append(f"via {resume_strategy}")

    try:
        protocol_tail_message_count = int(state.get("protocol_tail_message_count") or 0)
    except (TypeError, ValueError):
        protocol_tail_message_count = 0
    if protocol_tail_message_count > 0:
        parts.append(f"{protocol_tail_message_count} tail messages")

    try:
        prior_round_summary_count = int(state.get("prior_round_summary_count") or 0)
    except (TypeError, ValueError):
        prior_round_summary_count = 0
    if prior_round_summary_count > 0:
        parts.append(f"{prior_round_summary_count} prior summaries")

    consumed_layers = state.get("consumed_layers")
    if isinstance(consumed_layers, list):
        labels = [str(layer).strip() for layer in consumed_layers if str(layer).strip()]
        if labels:
            parts.append(", ".join(labels))

    if not parts:
        return "continuation consumed"
    return " · ".join(parts)


def _latest_checkpoint_turn_events(events: list[TaskRunEvent]) -> list[TaskRunEvent]:
    if not events:
        return []

    latest_completed_index = next(
        (index for index in range(len(events) - 1, -1, -1) if events[index].event_type == "agent_turn_completed"),
        None,
    )
    latest_started_index = next(
        (index for index in range(len(events) - 1, -1, -1) if events[index].event_type == "agent_turn_started"),
        None,
    )

    if latest_started_index is None and latest_completed_index is None:
        return events

    if latest_started_index is not None and (
        latest_completed_index is None or latest_started_index > latest_completed_index
    ):
        return events[latest_started_index:]

    if latest_completed_index is None:
        return events

    completed_event = events[latest_completed_index]
    matching_start_index = next(
        (
            index
            for index in range(latest_completed_index, -1, -1)
            if events[index].event_type == "agent_turn_started"
            and (
                not completed_event.agent_name
                or not events[index].agent_name
                or events[index].agent_name == completed_event.agent_name
            )
        ),
        None,
    )
    if matching_start_index is None:
        matching_start_index = next(
            (
                index
                for index in range(latest_completed_index, -1, -1)
                if events[index].event_type == "agent_turn_started"
            ),
            None,
        )
    if matching_start_index is None:
        return events[: latest_completed_index + 1]
    return events[matching_start_index : latest_completed_index + 1]


def _extract_event_continuation_state(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    direct_state = payload.get("recovery_continuation_state")
    if isinstance(direct_state, dict):
        return direct_state

    direct_state = payload.get("continuation_state")
    if isinstance(direct_state, dict):
        return direct_state

    checkpoint_snapshot = payload.get("checkpoint_snapshot")
    if isinstance(checkpoint_snapshot, dict):
        checkpoint_state = checkpoint_snapshot.get("continuation_state")
        if isinstance(checkpoint_state, dict):
            return checkpoint_state
        return describe_checkpoint_continuation_state(checkpoint_snapshot)

    return None


def _build_task_run_continuation_cursor(
    *,
    task_run: TaskRun,
    latest_tool_round: TaskRunEvent | None,
    latest_tool_round_payload: Any,
    latest_tool_blocked: TaskRunEvent | None,
    latest_tool_blocked_payload: Any,
    latest_followup: TaskRunEvent | None,
    latest_followup_payload: Any,
    latest_runtime_event: TaskRunEvent | None,
    latest_runtime_payload: Any,
    pending_tool_queue_item: Any,
) -> dict[str, Any]:
    tool_round_payload = latest_tool_round_payload if isinstance(latest_tool_round_payload, dict) else {}
    blocked_payload = latest_tool_blocked_payload if isinstance(latest_tool_blocked_payload, dict) else {}
    followup_payload = latest_followup_payload if isinstance(latest_followup_payload, dict) else {}
    runtime_payload = latest_runtime_payload if isinstance(latest_runtime_payload, dict) else {}

    if pending_tool_queue_item is not None:
        request_payload = _load_payload(getattr(pending_tool_queue_item, "request_payload_json", None))
        if not isinstance(request_payload, dict):
            request_payload = {}
        return {
            "next_action": "await_approval",
            "resume_strategy": (
                "resume_pipeline_stage_after_replay"
                if getattr(pending_tool_queue_item, "pipeline_run_id", None) is not None
                else "replay_tool_then_continue_turn"
            ),
            "source_event_type": latest_tool_blocked.event_type if latest_tool_blocked is not None else None,
            "source_event_at": latest_tool_blocked.created_at.isoformat() if latest_tool_blocked and latest_tool_blocked.created_at else None,
            "turn": request_payload.get("turn") or blocked_payload.get("turn") or tool_round_payload.get("turn"),
            "tool_name": getattr(pending_tool_queue_item, "target_name", None) or blocked_payload.get("tool_name"),
            "blocked_kind": request_payload.get("blocked_kind") or blocked_payload.get("blocked_kind"),
            "queue_item_id": getattr(pending_tool_queue_item, "id", None),
            "pipeline_run_id": getattr(pending_tool_queue_item, "pipeline_run_id", None),
            "pipeline_stage_id": getattr(pending_tool_queue_item, "pipeline_stage_id", None),
        }

    if latest_followup is not None:
        return {
            "next_action": "followup_injected",
            "resume_strategy": (
                "pipeline_resumed"
                if followup_payload.get("pipeline_run_id") is not None
                else "agent_turn_resumed"
            ),
            "source_event_type": latest_followup.event_type,
            "source_event_at": latest_followup.created_at.isoformat() if latest_followup.created_at else None,
            "turn": tool_round_payload.get("turn"),
            "tool_name": followup_payload.get("tool_name"),
            "queue_item_id": followup_payload.get("queue_item_id"),
            "pipeline_run_id": followup_payload.get("pipeline_run_id"),
            "pipeline_stage_id": followup_payload.get("pipeline_stage_id"),
        }

    runtime_snapshot = runtime_payload.get("runtime")
    if isinstance(runtime_snapshot, dict) and (task_run.status or "").strip().lower() in {"running", "paused"}:
        return {
            "next_action": "resume_scheduler",
            "resume_strategy": "rebuild_from_runtime_snapshot",
            "source_event_type": latest_runtime_event.event_type if latest_runtime_event is not None else None,
            "source_event_at": latest_runtime_event.created_at.isoformat() if latest_runtime_event and latest_runtime_event.created_at else None,
            "completed_step_count": runtime_snapshot.get("completed_step_count"),
            "ready_step_count": runtime_snapshot.get("ready_step_count"),
            "running_step_count": runtime_snapshot.get("running_step_count"),
            "waiting_step_count": runtime_snapshot.get("waiting_step_count"),
        }

    if latest_tool_round is not None and (task_run.status or "").strip().lower() == "running":
        return {
            "next_action": "continue_agent_turn",
            "resume_strategy": "rebuild_turn_state_from_tool_round",
            "source_event_type": latest_tool_round.event_type,
            "source_event_at": latest_tool_round.created_at.isoformat() if latest_tool_round.created_at else None,
            "turn": tool_round_payload.get("turn"),
            "tool_names": tool_round_payload.get("tool_names"),
            "blocked_tool_count": tool_round_payload.get("blocked_tool_count"),
        }

    return {
        "next_action": "none",
        "resume_strategy": None,
        "source_event_type": latest_tool_round.event_type if latest_tool_round is not None else None,
        "source_event_at": latest_tool_round.created_at.isoformat() if latest_tool_round and latest_tool_round.created_at else None,
    }


def _build_task_run_turn_local_state(
    *,
    events: list[TaskRunEvent],
    payload_by_event_id: dict[int, Any],
    latest_tool_round_payload: Any,
    latest_tool_blocked_payload: Any,
) -> dict[str, Any]:
    tool_round_payload = latest_tool_round_payload if isinstance(latest_tool_round_payload, dict) else {}
    blocked_payload = latest_tool_blocked_payload if isinstance(latest_tool_blocked_payload, dict) else {}
    turn_local_state = tool_round_payload.get("turn_local_state")
    if not isinstance(turn_local_state, dict):
        turn_local_state = {}

    protocol_messages = turn_local_state.get("protocol_messages")
    if not isinstance(protocol_messages, list):
        protocol_messages = []

    tool_results = turn_local_state.get("tool_results")
    if not isinstance(tool_results, list):
        tool_results = []

    tool_round_payloads = [
        payload_by_event_id.get(event.id)
        for event in events
        if event.event_type == "tool_round_recorded" and isinstance(payload_by_event_id.get(event.id), dict)
    ]
    recent_round_payloads = tool_round_payloads[-2:]
    prior_round_payloads = tool_round_payloads[:-2]
    protocol_tail_messages: list[Any] = []
    for round_payload in recent_round_payloads:
        round_turn_local_state = round_payload.get("turn_local_state") if isinstance(round_payload, dict) else None
        round_protocol_messages = (
            round_turn_local_state.get("protocol_messages")
            if isinstance(round_turn_local_state, dict)
            else None
        )
        if isinstance(round_protocol_messages, list):
            protocol_tail_messages.extend(round_protocol_messages)
    prior_round_summaries = [
        {
            "turn": round_payload.get("turn"),
            "tool_names": round_payload.get("tool_names"),
            "blocked_tool_count": round_payload.get("blocked_tool_count"),
            "assistant_content": (
                round_payload.get("turn_local_state", {}).get("assistant_content")
                if isinstance(round_payload.get("turn_local_state"), dict)
                else None
            ),
        }
        for round_payload in prior_round_payloads
    ]

    return {
        "turn": tool_round_payload.get("turn"),
        "tool_names": tool_round_payload.get("tool_names"),
        "blocked_tool_count": tool_round_payload.get("blocked_tool_count"),
        "assistant_content": turn_local_state.get("assistant_content"),
        "protocol_messages": protocol_messages,
        "protocol_tail_messages": protocol_tail_messages,
        "prior_round_summaries": prior_round_summaries,
        "tool_results": tool_results,
        "blocked_tool": {
            "tool_name": blocked_payload.get("tool_name"),
            "status": blocked_payload.get("status"),
            "blocked_kind": blocked_payload.get("blocked_kind"),
            "blocked_reason": blocked_payload.get("blocked_reason"),
            "queue_item_id": blocked_payload.get("queue_item_id"),
        } if blocked_payload else None,
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
