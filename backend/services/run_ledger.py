# -*- coding: utf-8 -*-
"""Run-level orchestration ledger helpers for chat/runtime execution."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional, Union

from sqlalchemy import func
from sqlalchemy.orm import Session

from models.enums import EventType

if TYPE_CHECKING:
    from models.database import Chatroom, Project, TaskRun, TaskRunEvent


def _db_models():
    from models import database as db_models

    return db_models


from services.approval_queue import serialize_approval_queue_item
from services.task_status_transition import InvalidTaskRunStatusTransition
from services.approval_replay import (
    build_pending_approval_continuation_cursor,
    load_approval_queue_request_payload,
)
from services.context_compaction_summary import build_context_compaction_projection
from services.orchestration_inbox import (
    summarize_orchestration_handoff_inbox,
    summarize_orchestration_handoff_projection,
)
from services.pipeline_inbox import summarize_pipeline_run_inbox
from services.policy_decision_contracts import (
    build_policy_decision_event_payload,
    format_policy_decision_summary,
    summarize_policy_decision,
    summarize_policy_decision_set,
)
from services.subagent_lifecycle import (
    build_subagent_lifecycle_from_events,
    build_subagent_runtime_handles,
    summarize_subagent_lifecycle,
    summarize_subagent_runtime_handles,
)


def get_task_run(db: Session, task_run_id: int | None) -> Optional[TaskRun]:
    if not task_run_id:
        return None
    db_models = _db_models()
    return db.query(db_models.TaskRun).filter(db_models.TaskRun.id == task_run_id).first()


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
    db_models = _db_models()
    if origin_message_id is not None:
        existing = (
            db.query(db_models.TaskRun)
            .filter(db_models.TaskRun.origin_message_id == origin_message_id)
            .first()
        )
        if existing is not None:
            return existing

    task_run = db_models.TaskRun(
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
    title: str | None = None,
    target_agent_name: str | None = None,
    summary: str | None = None,
) -> Optional[TaskRun]:
    """Update TaskRun metadata (non-status fields only).

    ADR-030: Status is derived from the event stream by
    ``append_task_event()``.  This function only updates metadata
    fields like run_kind, title, target_agent_name, summary.
    """
    if task_run is None:
        return None

    changed = False
    if run_kind and task_run.run_kind != run_kind:
        task_run.run_kind = run_kind
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
    """Mark a TaskRun as completed with metadata.

    ADR-030: Status is now derived from the event stream via
    ``append_task_event()``.  This function only sets the completion
    metadata (completed_at, recovery lease clearing, summary).
    The ``status`` parameter is kept for backward compatibility but
    is no longer the primary mechanism for state transitions.
    """
    if task_run is None:
        return None

    changed = False
    # Update summary if provided.
    resolved_summary = (summary or "").strip() or None
    if resolved_summary and task_run.summary != resolved_summary:
        task_run.summary = resolved_summary
        changed = True

    # Set completion metadata.
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


def append_task_event(
    db: Session,
    task_run: TaskRun | None,
    event_type: Union[str, EventType],
    *,
    agent_name: str | None = None,
    message_id: int | None = None,
    summary: str = "",
    payload: Any = None,
) -> Optional[TaskRunEvent]:
    if task_run is None:
        return None

    # Normalise to plain string for DB storage
    event_type_str = str(event_type.value) if isinstance(event_type, EventType) else str(event_type)

    db_models = _db_models()
    next_index = (
        db.query(func.max(db_models.TaskRunEvent.event_index))
        .filter(db_models.TaskRunEvent.task_run_id == task_run.id)
        .scalar()
        or 0
    ) + 1

    event = db_models.TaskRunEvent(
        task_run_id=task_run.id,
        event_index=next_index,
        event_type=event_type_str,
        agent_name=(agent_name or "").strip() or None,
        message_id=message_id,
        summary=(summary or "").strip() or None,
        payload_json=_dump_payload(payload),
    )
    task_run.updated_at = datetime.now()

    # ADR-030: Auto-derive cached status from event stream.
    try:
        from services.task_run_state import derive_task_run_status
        all_events = (
            db.query(db_models.TaskRunEvent)
            .filter(db_models.TaskRunEvent.task_run_id == task_run.id)
            .order_by(db_models.TaskRunEvent.event_index.asc())
            .all()
        )
        all_events.append(event)
        derived_status = derive_task_run_status(all_events)
        if derived_status and derived_status != "unknown":
            task_run.status = derived_status
    except Exception:
        pass  # Don't fail event recording if derivation fails

    db.add(task_run)
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


def append_policy_decision_event(
    db: Session,
    task_run: TaskRun | None,
    decision: Any,
    *,
    agent_name: str | None = None,
    message_id: int | None = None,
    summary: str | None = None,
) -> Optional[TaskRunEvent]:
    """Append one canonical policy-decision ledger event."""

    payload = build_policy_decision_event_payload(decision)
    decision_summary = payload.get("policy_decision_summary")
    return append_task_event(
        db,
        task_run,
        EventType.POLICY_DECISION_RECORDED,
        agent_name=agent_name,
        message_id=message_id,
        summary=summary or format_policy_decision_summary(decision_summary),
        payload=payload,
    )


def append_policy_decision_event_from_result_payload(
    db: Session,
    task_run: TaskRun | None,
    result_payload: Any,
    *,
    agent_name: str | None = None,
    message_id: int | None = None,
    summary: str | None = None,
) -> Optional[TaskRunEvent]:
    """Append a policy-decision event from a service result payload when present."""

    payload = result_payload if isinstance(result_payload, dict) else {}
    decision = payload.get("policy_decision")
    if isinstance(decision, dict) and decision.get("kind") == "policy_decision":
        return append_policy_decision_event(
            db,
            task_run,
            decision,
            agent_name=agent_name,
            message_id=message_id,
            summary=summary,
        )

    event_payload = payload.get("policy_decision_event_payload")
    if not isinstance(event_payload, dict) and payload.get("event_kind") == "policy_decision_recorded":
        event_payload = payload
    if not isinstance(event_payload, dict):
        return None

    event_decision = event_payload.get("policy_decision")
    if isinstance(event_decision, dict) and event_decision.get("kind") == "policy_decision":
        return append_policy_decision_event(
            db,
            task_run,
            event_decision,
            agent_name=agent_name,
            message_id=message_id,
            summary=summary,
        )

    decision_summary = event_payload.get("policy_decision_summary")
    if not isinstance(decision_summary, dict):
        return None

    return append_task_event(
        db,
        task_run,
        EventType.POLICY_DECISION_RECORDED,
        agent_name=agent_name,
        message_id=message_id,
        summary=summary or format_policy_decision_summary(decision_summary),
        payload=event_payload,
    )


def serialize_task_run_summary(task_run: TaskRun) -> dict[str, Any]:
    approval_items = list(getattr(task_run, "approval_queue_items", []) or [])
    checkpoint_snapshot = build_task_run_checkpoint_snapshot(task_run)
    latest_continuation_event = _find_latest_continuation_event(list(task_run.events or []))
    latest_scheduler_runtime = checkpoint_snapshot.get("latest_scheduler_runtime")
    continuation_cursor = checkpoint_snapshot.get("continuation_cursor")
    return {
        "id": task_run.id,
        "public_id": getattr(task_run, "public_id", None),
        "chatroom_id": task_run.chatroom_id,
        "chatroom_public_id": getattr(task_run, "chatroom_public_id", None),
        "project_id": task_run.project_id,
        "origin_message_id": task_run.origin_message_id,
        "client_turn_id": task_run.client_turn_id,
        "run_kind": task_run.run_kind,
        "status": task_run.status,
        "title": task_run.title,
        "user_request": task_run.user_request,
        "initiator": task_run.initiator,
        "target_agent_name": task_run.target_agent_name,
        "blocked_by_queue_item_id": task_run.blocked_by_queue_item_id,
        "recovery_owner": task_run.recovery_owner,
        "recovery_claimed_at": task_run.recovery_claimed_at.isoformat() if task_run.recovery_claimed_at else None,
        "recovery_lease_expires_at": (
            task_run.recovery_lease_expires_at.isoformat() if task_run.recovery_lease_expires_at else None
        ),
        "summary": task_run.summary,
        "continuation_cursor": continuation_cursor,
        "continuation_cursor_summary": checkpoint_snapshot.get("continuation_cursor_summary"),
        "continuation_state": checkpoint_snapshot.get("continuation_state"),
        "continuation_state_summary": checkpoint_snapshot.get("continuation_state_summary"),
        "latest_event_type": task_run.events[-1].event_type if task_run.events else None,
        "latest_continuation_event_type": latest_continuation_event.get("event_type") if latest_continuation_event else None,
        "latest_continuation_event_summary": latest_continuation_event.get("continuation_state_summary") if latest_continuation_event else None,
        "latest_continuation_event_at": latest_continuation_event.get("created_at") if latest_continuation_event else None,
        "latest_scheduler_runtime": latest_scheduler_runtime,
        "scheduler_runtime_summary": summarize_scheduler_runtime(latest_scheduler_runtime),
        "latest_subagent_step": checkpoint_snapshot.get("latest_subagent_step"),
        "pipeline_inbox_summary": checkpoint_snapshot.get("pipeline_inbox_summary"),
        "orchestration_handoff_inbox_summary": checkpoint_snapshot.get("orchestration_handoff_inbox_summary"),
        "subagent_lifecycle_summary": checkpoint_snapshot.get("subagent_lifecycle_summary"),
        "subagent_handles_summary": checkpoint_snapshot.get("subagent_handles_summary"),
        "policy_decision_summary": checkpoint_snapshot.get("policy_decision_summary"),
        "checkpoint_snapshot": checkpoint_snapshot,
        "event_count": len(task_run.events or []),
        "approval_queue_count": len(approval_items),
        "pending_approval_count": sum(1 for item in approval_items if (item.status or "") == "pending"),
        "created_at": task_run.created_at.isoformat() if task_run.created_at else None,
        "updated_at": task_run.updated_at.isoformat() if task_run.updated_at else None,
        "completed_at": task_run.completed_at.isoformat() if task_run.completed_at else None,
    }


def serialize_task_run_detail(task_run: TaskRun, *, event_limit: int | None = None) -> dict[str, Any]:
    payload = serialize_task_run_summary(task_run)
    events = list(task_run.events or [])
    if event_limit is not None and event_limit > 0:
        events = events[-event_limit:]
    payload["events"] = [
        _serialize_task_run_event(event)
        for event in events
    ]
    payload["policy_decisions"] = _serialize_task_run_policy_decision_entries(
        list(task_run.events or [])
    )
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
    diagnostics = (
        raw_payload.get("selector_diagnostics")
        if isinstance(raw_payload.get("selector_diagnostics"), dict)
        else {}
    )
    compaction_projection = (
        build_context_compaction_projection(diagnostics, fallback_summary=event.summary)
        if event.event_type == "context_compaction"
        else {}
    )
    runtime_snapshot = raw_payload.get("runtime") if isinstance(raw_payload.get("runtime"), dict) else None
    handoff_projection = (
        {
            "from_agent": raw_payload.get("from_agent"),
            "to_agent": raw_payload.get("to_agent"),
            "from_step_id": raw_payload.get("from_step_id"),
            "to_step_id": raw_payload.get("to_step_id"),
            "attached_to_step_id": raw_payload.get("attached_to_step_id"),
            "dispatch_kind": raw_payload.get("dispatch_kind"),
            "content_preview": raw_payload.get("content_preview"),
        }
        if event.event_type == "handoff_created"
        else {}
    )
    scheduler_plan_projection = (
        {
            "schedule_mode": raw_payload.get("mode"),
            "schedule_step_count": raw_payload.get("step_count"),
            "schedule_blocking_step_count": raw_payload.get("blocking_step_count"),
            "schedule_sidecar_step_count": raw_payload.get("sidecar_step_count"),
            "schedule_sidecar_agent_types": raw_payload.get("sidecar_agent_types"),
            "schedule_steps": raw_payload.get("steps"),
        }
        if event.event_type == "scheduler_plan_created"
        else {}
    )
    return {
        "id": event.id,
        "event_index": event.event_index,
        "event_type": event.event_type,
        "agent_name": event.agent_name,
        "message_id": event.message_id,
        "summary": event.summary,
        **compaction_projection,
        **handoff_projection,
        **scheduler_plan_projection,
        "runtime_snapshot": runtime_snapshot,
        "payload": raw_payload,
        "continuation_state": continuation_state,
        "continuation_state_summary": summarize_continuation_state(continuation_state),
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def _find_latest_continuation_event(events: list[TaskRunEvent]) -> dict[str, Any] | None:
    for event in reversed(events):
        serialized = _serialize_task_run_event(event)
        if serialized.get("continuation_state_summary"):
            return serialized
    return None


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
    latest_compaction_projection = build_context_compaction_projection(
        latest_compaction_diagnostics,
        fallback_summary=latest_compaction.summary if latest_compaction is not None else None,
    )
    latest_runtime_payload = payload_by_event_id.get(latest_runtime_event.id, {}) if latest_runtime_event is not None else {}
    latest_subagent_event = next(
        (
            event for event in reversed(events)
            if event.event_type in {"scheduler_step_dispatched", "scheduler_step_completed", "scheduler_step_failed"}
        ),
        None,
    )
    latest_subagent_payload = payload_by_event_id.get(latest_subagent_event.id, {}) if latest_subagent_event is not None else {}
    pipeline_inbox = [
        summarize_pipeline_run_inbox(pipeline_run)
        for pipeline_run in list(getattr(task_run, "pipeline_runs", []) or [])
    ]
    orchestration_handoff_inbox = summarize_orchestration_handoff_inbox(task_run)
    subagent_lifecycle = build_subagent_lifecycle_from_events(events)
    subagent_handles = build_subagent_runtime_handles(subagent_lifecycle)
    policy_decisions = _extract_policy_decision_payloads(
        events=events,
        payload_by_event_id=payload_by_event_id,
    )
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
            **latest_compaction_projection,
            "created_at": latest_compaction.created_at.isoformat() if latest_compaction and latest_compaction.created_at else None,
        },
        "latest_scheduler_runtime": latest_runtime_payload.get("runtime") if isinstance(latest_runtime_payload, dict) else None,
        "latest_subagent_step": (
            {
                "event_type": latest_subagent_event.event_type,
                "step_id": latest_subagent_payload.get("step_id"),
                "agent_name": latest_subagent_payload.get("agent_name"),
                "dispatch_kind": latest_subagent_payload.get("dispatch_kind"),
                "status": (
                    latest_subagent_payload.get("step_state", {}).get("status")
                    if isinstance(latest_subagent_payload.get("step_state"), dict)
                    else None
                ),
                "source": latest_subagent_payload.get("source"),
                "response_preview": latest_subagent_payload.get("response_preview"),
            }
            if latest_subagent_event is not None and isinstance(latest_subagent_payload, dict)
            else None
        ),
        "pipeline_inbox": pipeline_inbox,
        "pipeline_inbox_summary": summarize_pipeline_inbox_projection(pipeline_inbox),
        "orchestration_handoff_inbox": orchestration_handoff_inbox,
        "orchestration_handoff_inbox_summary": summarize_orchestration_handoff_projection(orchestration_handoff_inbox),
        "subagent_lifecycle": subagent_lifecycle,
        "subagent_lifecycle_summary": summarize_subagent_lifecycle(subagent_lifecycle),
        "subagent_handles": subagent_handles,
        "subagent_handles_summary": summarize_subagent_runtime_handles(subagent_handles),
        "policy_decision_summary": summarize_policy_decision_set(policy_decisions),
        "continuation_cursor": continuation_cursor,
        "turn_local_state": turn_local_state,
        "pending_approval_count": sum(1 for item in approval_items if (item.status or "") == "pending"),
        "approval_queue_count": len(approval_items),
        "status": task_run.status,
        "summary": task_run.summary,
    }
    snapshot["continuation_cursor_summary"] = summarize_continuation_cursor(snapshot["continuation_cursor"])
    snapshot["continuation_state"] = describe_checkpoint_continuation_state(snapshot)
    snapshot["continuation_state_summary"] = summarize_continuation_state(snapshot["continuation_state"])
    return snapshot


def _extract_policy_decision_payloads(
    *,
    events: list[TaskRunEvent],
    payload_by_event_id: dict[int, Any],
) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    for event in events:
        payload = payload_by_event_id.get(event.id)
        if not isinstance(payload, dict):
            continue
        if (
            event.event_type != "policy_decision_recorded"
            and payload.get("event_kind") != "policy_decision_recorded"
        ):
            continue
        contract = payload.get("policy_decision")
        if isinstance(contract, dict) and contract.get("kind") == "policy_decision":
            decisions.append(contract)
            continue
        summary = payload.get("policy_decision_summary")
        if isinstance(summary, dict):
            decisions.append(summary)
    return decisions


def _serialize_task_run_policy_decision_entries(events: list[TaskRunEvent]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for event in events:
        payload = _load_payload(event.payload_json)
        if not isinstance(payload, dict):
            continue
        if (
            event.event_type != "policy_decision_recorded"
            and payload.get("event_kind") != "policy_decision_recorded"
        ):
            continue
        contract = payload.get("policy_decision")
        summary = payload.get("policy_decision_summary")
        if isinstance(contract, dict) and contract.get("kind") == "policy_decision":
            decision_summary = summarize_policy_decision(contract)
            decision_payload = contract
        elif isinstance(summary, dict):
            decision_summary = summarize_policy_decision(summary)
            decision_payload = None
        else:
            continue
        entries.append(
            {
                "event_id": event.id,
                "event_index": event.event_index,
                "event_type": event.event_type,
                "event_summary": event.summary,
                "policy_decision_summary": decision_summary,
                "policy_decision": decision_payload,
                "created_at": event.created_at.isoformat() if event.created_at else None,
            }
        )
    return entries


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
    latest_subagent_step = (
        snapshot.get("latest_subagent_step")
        if isinstance(snapshot.get("latest_subagent_step"), dict)
        else {}
    )
    if continuation_cursor.get("resume_strategy") == "rebuild_from_runtime_snapshot":
        consumed_layers.append("runtime_snapshot")
    if protocol_tail_messages:
        consumed_layers.append("protocol_tail")
    if prior_round_summaries:
        consumed_layers.append("prior_round_summaries")
    if str(latest_subagent_step.get("dispatch_kind") or "").strip() == "consult":
        consumed_layers.append("consult_subagent")
    return {
        "consumed": bool(consumed_layers),
        "next_action": continuation_cursor.get("next_action"),
        "resume_strategy": continuation_cursor.get("resume_strategy"),
        "consumed_layers": consumed_layers,
        "protocol_tail_message_count": len(protocol_tail_messages),
        "prior_round_summary_count": len(prior_round_summaries),
        "latest_subagent_dispatch_kind": latest_subagent_step.get("dispatch_kind"),
        "latest_subagent_status": latest_subagent_step.get("status"),
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


def summarize_scheduler_runtime(runtime: Any) -> str | None:
    state = runtime if isinstance(runtime, dict) else {}
    if not state:
        return None

    parts: list[str] = []
    for key, label in [
        ("completed_step_count", "completed"),
        ("ready_step_count", "ready"),
        ("running_step_count", "running"),
        ("waiting_step_count", "waiting"),
        ("step_count", "total"),
    ]:
        try:
            value = int(state.get(key))
        except (TypeError, ValueError):
            continue
        parts.append(f"{value} {label}")

    if not parts:
        return None
    return " · ".join(parts)


def summarize_pipeline_inbox_projection(projection: Any) -> str | None:
    runs = projection if isinstance(projection, list) else []
    if not runs:
        return None

    totals = {
        "pending": 0,
        "inflight": 0,
        "dead_letter": 0,
        "consumed": 0,
        "delivery": 0,
    }
    for run in runs:
        if not isinstance(run, dict):
            continue
        totals["pending"] += _coerce_int(run.get("pending_delivery_count")) or 0
        totals["inflight"] += _coerce_int(run.get("inflight_delivery_count")) or 0
        totals["dead_letter"] += _coerce_int(run.get("dead_letter_delivery_count")) or 0
        totals["consumed"] += _coerce_int(run.get("consumed_delivery_count")) or 0
        totals["delivery"] += _coerce_int(run.get("delivery_count")) or 0

    if totals["delivery"] <= 0:
        return None

    parts = [
        f"{totals['delivery']} deliveries",
        f"{totals['pending']} pending",
        f"{totals['inflight']} inflight",
    ]
    if totals["dead_letter"] > 0:
        parts.append(f"{totals['dead_letter']} dead-letter")
    if totals["consumed"] > 0:
        parts.append(f"{totals['consumed']} consumed")
    return " · ".join(parts)


def summarize_continuation_cursor(cursor: Any) -> str | None:
    state = cursor if isinstance(cursor, dict) else {}
    next_action = str(state.get("next_action") or "").strip()
    if not next_action or next_action == "none":
        return None

    parts = [next_action.replace("_", " ")]

    resume_strategy = str(state.get("resume_strategy") or "").strip()
    if resume_strategy:
        parts.append(f"via {resume_strategy}")

    tool_name = str(state.get("tool_name") or "").strip()
    if tool_name:
        parts.append(f"tool {tool_name}")

    turn = _coerce_int(state.get("turn"))
    if turn is not None:
        parts.append(f"turn {turn}")

    for key, label in [
        ("ready_step_count", "ready"),
        ("running_step_count", "running"),
        ("waiting_step_count", "waiting"),
    ]:
        value = _coerce_int(state.get(key))
        if value is not None:
            parts.append(f"{value} {label}")

    return " · ".join(parts) if parts else None


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
        request_payload = load_approval_queue_request_payload(
            getattr(pending_tool_queue_item, "request_payload_json", None)
        )
        return build_pending_approval_continuation_cursor(
            pending_tool_queue_item,
            request_payload=request_payload,
            blocked_payload=blocked_payload,
            tool_round_payload=tool_round_payload,
            source_event_type=latest_tool_blocked.event_type if latest_tool_blocked is not None else None,
            source_event_at=(
                latest_tool_blocked.created_at.isoformat()
                if latest_tool_blocked and latest_tool_blocked.created_at
                else None
            ),
        )

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

    db_models = _db_models()
    row = (
        db.query(db_models.TaskRun, db_models.Chatroom, db_models.Project)
        .join(db_models.Chatroom, db_models.TaskRun.chatroom_id == db_models.Chatroom.id)
        .outerjoin(db_models.Project, db_models.TaskRun.project_id == db_models.Project.id)
        .filter(db_models.TaskRun.id == task_run_id)
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
    entry = payload.get("entry") if isinstance(payload.get("entry"), dict) else None
    detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else None
    chatroom_id = entry.get("chatroom_id") if isinstance(entry, dict) else None
    if isinstance(chatroom_id, int):
        await websocket_manager.broadcast_to_room(
            {
                "type": "task_run_update",
                "payload": {
                    "change_type": payload.get("change_type"),
                    "change_reason": payload.get("change_reason"),
                    "task_event_type": payload.get("task_event_type"),
                    "captured_at": payload.get("captured_at"),
                    "entry": entry,
                    "detail": detail,
                },
                "chatroom_id": chatroom_id,
            },
            chatroom_id,
        )
        await websocket_manager.broadcast_to_room(
            {
                "type": "chat_processes_changed",
                "chatroom_id": chatroom_id,
                "reason": "task_run_update",
                "task_run_id": entry.get("id"),
                "change_reason": payload.get("change_reason"),
                "task_event_type": payload.get("task_event_type"),
                "captured_at": payload.get("captured_at"),
            },
            chatroom_id,
        )
