# -*- coding: utf-8 -*-
"""Shared task-run event helpers for runner-style agent turns."""

from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy.orm import Session

from models.database import TaskRun
from services.approval_queue import create_approval_queue_item
from services.approval_replay import (
    blocked_tool_queue_kind,
    blocked_tool_queue_title,
    blocked_tool_resume_supported,
    build_blocked_tool_request_key,
    build_blocked_tool_request_payload,
)
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
    serialized_tool_results: list[dict[str, Any]] = []
    for result in normalized_tool_results:
        status = str(getattr(result, "status", "") or ("succeeded" if getattr(result, "success", True) else "failed"))
        status_counts[status] = status_counts.get(status, 0) + 1
        serialized_tool_results.append(
            {
                "tool_call_id": str(getattr(result, "tool_call_id", "") or ""),
                "tool_name": str(getattr(result, "tool_name", "") or "tool"),
                "arguments": str(getattr(result, "arguments", "") or "{}"),
                "result": compact_runtime_text(getattr(result, "result", "") or "", limit=1200),
                "success": bool(getattr(result, "success", False)),
                "status": status,
                "blocked": bool(getattr(result, "blocked", False)),
                "blocked_kind": getattr(result, "blocked_kind", None),
                "blocked_reason": compact_runtime_text(
                    getattr(result, "blocked_reason", "") or getattr(result, "result", ""),
                    limit=220,
                ),
            }
        )
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
    if serialized_tool_results:
        merged_payload["turn_local_state"] = {
            "assistant_content": compact_runtime_text(summary, limit=280),
            "tool_results": serialized_tool_results,
            "protocol_messages": [
                {
                    "role": "assistant",
                    "content": compact_runtime_text(summary, limit=280),
                    "tool_calls": [
                        {
                            "id": result_payload["tool_call_id"],
                            "type": "function",
                            "function": {
                                "name": result_payload["tool_name"],
                                "arguments": result_payload["arguments"],
                            },
                        }
                        for result_payload in serialized_tool_results
                    ],
                },
                *[
                    {
                        "role": "tool",
                        "tool_call_id": result_payload["tool_call_id"],
                        "name": result_payload["tool_name"],
                        "content": result_payload["result"],
                    }
                    for result_payload in serialized_tool_results
                ],
            ],
        }
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
            queue_kind = blocked_tool_queue_kind(blocked_tool["blocked_kind"])
            resume_supported = blocked_tool_resume_supported(
                blocked_kind=blocked_tool["blocked_kind"],
                blocked_reason=blocked_tool["blocked_reason"],
            )
            request_key = build_blocked_tool_request_key(
                task_run_id=getattr(task_run, "id", None),
                agent_name=agent_name,
                blocked_tool=blocked_tool,
            )
            queue_item = create_approval_queue_item(
                db,
                task_run=task_run,
                chatroom_id=getattr(task_run, "chatroom_id", None),
                project_id=getattr(task_run, "project_id", None),
                queue_kind=queue_kind,
                source="tool_call_blocked",
                title=blocked_tool_queue_title(blocked_tool["tool_name"], queue_kind=queue_kind),
                summary=blocked_tool["blocked_reason"],
                agent_name=agent_name,
                target_kind="tool",
                target_name=blocked_tool["tool_name"],
                request_key=request_key,
                request_payload=build_blocked_tool_request_payload(
                    turn=int(turn),
                    blocked_tool=blocked_tool,
                    resume_supported=resume_supported,
                    runtime_payload=payload_dict,
                ),
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
