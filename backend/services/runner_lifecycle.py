# -*- coding: utf-8 -*-
"""Shared task-run event helpers for runner-style agent turns."""

from __future__ import annotations

import hashlib
from typing import Any, Iterable

from sqlalchemy.orm import Session

from models.database import TaskRun
from services.approval_queue import create_approval_queue_item
from services.run_ledger import append_task_event, update_task_run


def start_agent_turn(
    db: Session,
    task_run: TaskRun | None,
    *,
    agent_name: str,
    summary: str,
    payload: Any = None,
    target_agent_name: str | None = None,
):
    update_task_run(
        db,
        task_run,
        target_agent_name=(target_agent_name or "").strip() or agent_name,
    )
    return append_task_event(
        db,
        task_run,
        "agent_turn_started",
        agent_name=agent_name,
        summary=summary,
        payload=payload,
    )


def record_tool_round(
    db: Session,
    task_run: TaskRun | None,
    *,
    agent_name: str,
    turn: int,
    tool_names: Iterable[str],
    tool_results: Iterable[Any] | None = None,
    summary: str,
    payload: Any = None,
):
    normalized_tool_names = [str(name or "").strip() for name in tool_names if str(name or "").strip()]
    normalized_tool_results = list(tool_results or [])
    payload_dict = payload if isinstance(payload, dict) else None
    pipeline_run_id = payload_dict.get("pipeline_run_id") if payload_dict else None
    pipeline_stage_id = (
        payload_dict.get("pipeline_stage_id")
        if payload_dict and payload_dict.get("pipeline_stage_id") is not None
        else (payload_dict.get("stage_id") if payload_dict else None)
    )
    status_counts: dict[str, int] = {}
    blocked_tools: list[dict[str, Any]] = []
    for result in normalized_tool_results:
        status = str(getattr(result, "status", "") or ("succeeded" if getattr(result, "success", True) else "failed"))
        status_counts[status] = status_counts.get(status, 0) + 1
        if bool(getattr(result, "blocked", False)):
            blocked_tools.append(
                {
                    "tool_name": str(getattr(result, "tool_name", "") or "tool"),
                    "arguments": str(getattr(result, "arguments", "") or "{}"),
                    "status": status,
                    "blocked_kind": getattr(result, "blocked_kind", None),
                    "blocked_reason": compact_runtime_text(getattr(result, "blocked_reason", "") or getattr(result, "result", ""), limit=220),
                }
            )
    merged_payload = {
        "turn": int(turn),
        "tool_names": normalized_tool_names,
        "tool_count": len(normalized_tool_names),
        "tool_status_counts": status_counts,
        "blocked_tool_count": len(blocked_tools),
    }
    if blocked_tools:
        merged_payload["blocked_tools"] = blocked_tools
    if isinstance(payload, dict):
        merged_payload.update(payload)
    elif payload is not None:
        merged_payload["details"] = payload
    event = append_task_event(
        db,
        task_run,
        "tool_round_recorded",
        agent_name=agent_name,
        summary=summary,
        payload=merged_payload,
    )
    for blocked_tool in blocked_tools:
        queue_item = None
        if task_run is not None:
            queue_kind = "escalation" if blocked_tool["blocked_kind"] == "sandbox" else "approval"
            resume_supported = _blocked_tool_resume_supported(
                blocked_kind=blocked_tool["blocked_kind"],
                blocked_reason=blocked_tool["blocked_reason"],
            )
            request_key = hashlib.sha1(
                "|".join(
                    [
                        str(getattr(task_run, "id", "") or ""),
                        str(agent_name or ""),
                        str(blocked_tool["status"] or ""),
                        str(blocked_tool["tool_name"] or ""),
                        str(blocked_tool["arguments"] or ""),
                        str(blocked_tool["blocked_reason"] or ""),
                    ]
                ).encode("utf-8")
            ).hexdigest()
            queue_item = create_approval_queue_item(
                db,
                task_run=task_run,
                chatroom_id=getattr(task_run, "chatroom_id", None),
                project_id=getattr(task_run, "project_id", None),
                queue_kind=queue_kind,
                source="tool_call_blocked",
                title=(
                    f"Escalation needed for {blocked_tool['tool_name']}"
                    if queue_kind == "escalation"
                    else f"Approval needed for {blocked_tool['tool_name']}"
                ),
                summary=blocked_tool["blocked_reason"],
                agent_name=agent_name,
                target_kind="tool",
                target_name=blocked_tool["tool_name"],
                request_key=request_key,
                request_payload={
                    "turn": int(turn),
                    "tool_name": blocked_tool["tool_name"],
                    "arguments": blocked_tool["arguments"],
                    "status": blocked_tool["status"],
                    "blocked_kind": blocked_tool["blocked_kind"],
                    "blocked_reason": blocked_tool["blocked_reason"],
                    "resume_supported": resume_supported,
                    "pipeline_run_id": pipeline_run_id,
                    "pipeline_stage_id": pipeline_stage_id,
                    "pipeline_id": payload_dict.get("pipeline_id") if payload_dict else None,
                    "stage_name": payload_dict.get("stage_name") if payload_dict else None,
                    "display_name": payload_dict.get("display_name") if payload_dict else None,
                },
                pipeline_run_id=pipeline_run_id,
                pipeline_stage_id=pipeline_stage_id,
            )
            append_task_event(
                db,
                task_run,
                "approval_queue_item_created",
                agent_name=agent_name,
                summary=f"Queued {queue_kind} item for {blocked_tool['tool_name']}.",
                payload={
                    "queue_item_id": queue_item.id,
                    "queue_kind": queue_item.queue_kind,
                    "target_kind": queue_item.target_kind,
                    "target_name": queue_item.target_name,
                    "status": queue_item.status,
                    "source": queue_item.source,
                },
            )
        append_task_event(
            db,
            task_run,
            "tool_call_blocked",
            agent_name=agent_name,
            summary=f"{blocked_tool['tool_name']} was blocked ({blocked_tool['status']}).",
            payload={
                "turn": int(turn),
                "tool_name": blocked_tool["tool_name"],
                "status": blocked_tool["status"],
                "blocked_kind": blocked_tool["blocked_kind"],
                "blocked_reason": blocked_tool["blocked_reason"],
                "queue_item_id": getattr(queue_item, "id", None),
            },
        )
    return event


def complete_agent_turn(
    db: Session,
    task_run: TaskRun | None,
    *,
    agent_name: str,
    response_content: str = "",
    message_id: int | None = None,
    summary: str,
    payload: Any = None,
):
    merged_payload = {
        "response_preview": compact_runtime_text(response_content, limit=280),
    }
    if message_id is not None:
        merged_payload["message_id"] = message_id
    if isinstance(payload, dict):
        merged_payload.update(payload)
    elif payload is not None:
        merged_payload["details"] = payload
    return append_task_event(
        db,
        task_run,
        "agent_turn_completed",
        agent_name=agent_name,
        message_id=message_id,
        summary=summary,
        payload=merged_payload,
    )


def compact_runtime_text(value: Any, *, limit: int = 600) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def _blocked_tool_resume_supported(*, blocked_kind: Any, blocked_reason: Any) -> bool:
    if str(blocked_kind or "").strip().lower() != "approval":
        return False
    reason = str(blocked_reason or "").strip().lower()
    if not reason:
        return True
    return "not authorized to use tool" not in reason and "unauthorized tool" not in reason
