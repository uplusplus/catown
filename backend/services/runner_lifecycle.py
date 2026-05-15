# -*- coding: utf-8 -*-
"""Shared task-run event helpers for runner-style agent turns."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy.orm import Session

from models.database import TaskRun
from services.approval_queue import create_approval_queue_item
from services.approval_replay import (
    build_approval_queue_item_created_event_payload,
    blocked_tool_queue_kind,
    blocked_tool_queue_title,
    blocked_tool_resume_supported,
    build_blocked_tool_request_key,
    build_blocked_tool_request_payload,
)


logger = logging.getLogger("catown.runner_lifecycle")
from services.run_ledger import append_task_event, update_task_run


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _merge_fact_payload(base: dict[str, Any], payload: Any = None) -> dict[str, Any]:
    merged_payload = {
        **base,
        "occurred_at": _utc_now_iso(),
    }
    if isinstance(payload, dict):
        merged_payload.update(payload)
        merged_payload.setdefault("occurred_at", _utc_now_iso())
    elif payload is not None:
        merged_payload["details"] = payload
    return merged_payload


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


def record_llm_request_created(
    db: Session,
    task_run: TaskRun | None,
    *,
    agent_name: str,
    turn: int,
    model: str | None = None,
    client_turn_id: str | None = None,
    summary: str | None = None,
    payload: Any = None,
):
    merged_payload = _merge_fact_payload(
        {
            "turn": int(turn),
            "model": model,
            "client_turn_id": client_turn_id,
        },
        payload,
    )
    return append_task_event(
        db,
        task_run,
        "llm_request_created",
        agent_name=agent_name,
        summary=summary or f"{agent_name} sent a request to the LLM.",
        payload=merged_payload,
    )


def record_llm_response_started(
    db: Session,
    task_run: TaskRun | None,
    *,
    agent_name: str,
    turn: int,
    client_turn_id: str | None = None,
    summary: str | None = None,
    payload: Any = None,
):
    merged_payload = _merge_fact_payload(
        {
            "turn": int(turn),
            "client_turn_id": client_turn_id,
        },
        payload,
    )
    return append_task_event(
        db,
        task_run,
        "llm_response_started",
        agent_name=agent_name,
        summary=summary or f"The LLM started streaming output for {agent_name}.",
        payload=merged_payload,
    )


def record_llm_response_completed(
    db: Session,
    task_run: TaskRun | None,
    *,
    agent_name: str,
    turn: int,
    finish_reason: str | None = None,
    client_turn_id: str | None = None,
    summary: str | None = None,
    payload: Any = None,
):
    merged_payload = _merge_fact_payload(
        {
            "turn": int(turn),
            "finish_reason": finish_reason,
            "client_turn_id": client_turn_id,
        },
        payload,
    )
    return append_task_event(
        db,
        task_run,
        "llm_response_completed",
        agent_name=agent_name,
        summary=summary or f"The LLM completed output for {agent_name}.",
        payload=merged_payload,
    )


def record_tool_round(
    db: Session,
    task_run: TaskRun | None,
    *,
    agent_name: str,
    turn: int,
    tool_names: Iterable[str],
    tool_results: Iterable[Any] | None = None,
    blocked_tool_results: Iterable[Any] | None = None,
    summary: str,
    assistant_content: str | None = None,
    payload: Any = None,
):
    normalized_tool_names = [str(name or "").strip() for name in tool_names if str(name or "").strip()]
    normalized_tool_results = list(tool_results or [])
    normalized_blocked_tool_results = list(blocked_tool_results or [])
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
                "metadata": dict(getattr(result, "metadata", {}) or {}),
            }
        )
        if bool(getattr(result, "blocked", False)):
            blocked_tools.append(
                {
                    "tool_call_id": str(getattr(result, "tool_call_id", "") or ""),
                    "tool_name": str(getattr(result, "tool_name", "") or "tool"),
                    "arguments": str(getattr(result, "arguments", "") or "{}"),
                    "status": status,
                    "blocked_kind": getattr(result, "blocked_kind", None),
                    "blocked_reason": compact_runtime_text(getattr(result, "blocked_reason", "") or getattr(result, "result", ""), limit=220),
                    "metadata": dict(getattr(result, "metadata", {}) or {}),
                }
            )
    for result in normalized_blocked_tool_results:
        status = str(getattr(result, "status", "") or ("succeeded" if getattr(result, "success", True) else "failed"))
        status_counts[status] = status_counts.get(status, 0) + 1
        blocked_tools.append(
            {
                "tool_call_id": str(getattr(result, "tool_call_id", "") or ""),
                "tool_name": str(getattr(result, "tool_name", "") or "tool"),
                "arguments": str(getattr(result, "arguments", "") or "{}"),
                "status": status,
                "blocked_kind": getattr(result, "blocked_kind", None),
                "blocked_reason": compact_runtime_text(
                    getattr(result, "blocked_reason", "") or getattr(result, "result", ""),
                    limit=220,
                ),
                "metadata": dict(getattr(result, "metadata", {}) or {}),
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
        assistant_message_content = compact_runtime_text(assistant_content or summary, limit=280)
        merged_payload["turn_local_state"] = {
            "assistant_content": assistant_message_content,
            "tool_results": serialized_tool_results,
            "protocol_messages": [
                {
                    "role": "assistant",
                    "content": assistant_message_content,
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
                title=blocked_tool_queue_title(
                    blocked_tool["tool_name"],
                    queue_kind=queue_kind,
                    blocked_kind=blocked_tool.get("blocked_kind"),
                ),
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
            logger.info(
                "[ApprovalFlow] queue-created task_run_id=%s queue_item_id=%s queue_kind=%s tool=%s blocked_kind=%s resume_supported=%s turn=%s",
                getattr(task_run, "id", None),
                getattr(queue_item, "id", None),
                queue_kind,
                blocked_tool.get("tool_name"),
                blocked_tool.get("blocked_kind"),
                resume_supported,
                turn,
            )
            append_task_event(
                db,
                task_run,
                "approval_queue_item_created",
                agent_name=agent_name,
                summary=f"Queued {queue_kind} item for {blocked_tool['tool_name']}.",
                payload=build_approval_queue_item_created_event_payload(queue_item),
            )
        append_task_event(
            db,
            task_run,
            "tool_call_blocked",
            agent_name=agent_name,
            summary=f"{blocked_tool['tool_name']} was blocked ({blocked_tool['status']}).",
            payload={
                "turn": int(turn),
                "tool_call_id": blocked_tool.get("tool_call_id"),
                "tool_name": blocked_tool["tool_name"],
                "status": blocked_tool["status"],
                "blocked_kind": blocked_tool["blocked_kind"],
                "blocked_reason": blocked_tool["blocked_reason"],
                "queue_item_id": getattr(queue_item, "id", None),
            },
        )
        logger.info(
            "[ApprovalFlow] tool-blocked task_run_id=%s queue_item_id=%s tool=%s status=%s blocked_kind=%s",
            getattr(task_run, "id", None) if task_run is not None else None,
            getattr(queue_item, "id", None),
            blocked_tool.get("tool_name"),
            blocked_tool.get("status"),
            blocked_tool.get("blocked_kind"),
        )
    return event


def start_tool_call(
    db: Session,
    task_run: TaskRun | None,
    *,
    agent_name: str,
    turn: int,
    tool_name: str,
    arguments: str | None = None,
    payload: Any = None,
):
    merged_payload = {
        "turn": int(turn),
        "tool_name": str(tool_name or "").strip() or "tool",
    }
    if arguments is not None:
        merged_payload["arguments"] = str(arguments or "")
    if isinstance(payload, dict):
        merged_payload.update(payload)
    elif payload is not None:
        merged_payload["details"] = payload
    return append_task_event(
        db,
        task_run,
        "tool_call_started",
        agent_name=agent_name,
        summary=f"Starting {str(tool_name or '').strip() or 'tool'}.",
        payload=merged_payload,
    )


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
