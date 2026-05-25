# -*- coding: utf-8 -*-
"""Shared task-run event helpers for runner-style agent turns."""

from __future__ import annotations

import logging
import json
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
from models.enums import EventType
from services.task_status_transition import validate_transition


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


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except TypeError:
        if isinstance(value, dict):
            return {str(key): _json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [_json_safe(item) for item in value]
        return str(value)


def build_llm_request_prompt_payload(
    frame: Any,
    *,
    elapsed_ms: Any = None,
    step_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if elapsed_ms is not None:
        payload["elapsed_ms"] = elapsed_ms
    if step_id:
        payload["step_id"] = step_id

    prompt_messages = getattr(frame, "prompt_snapshot", None)
    if prompt_messages is None:
        prompt_messages = getattr(frame, "messages", None)
    if prompt_messages is not None:
        safe_messages = _json_safe(prompt_messages)
        payload["prompt_messages"] = safe_messages
        if isinstance(safe_messages, list):
            payload["prompt_message_count"] = len(safe_messages)
            prompt_preview = _prompt_messages_preview(safe_messages)
            if prompt_preview:
                payload["prompt_preview"] = prompt_preview

    system_prompt = str(getattr(frame, "system_prompt", "") or "")
    if system_prompt:
        payload["system_prompt"] = system_prompt

    return payload


def _prompt_messages_preview(messages: list[Any], limit: int = 280) -> str:
    for item in reversed(messages):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role == "system":
            continue
        content = item.get("content")
        text = _content_preview(content)
        if text:
            return text[:limit]
    return ""


def _content_preview(content: Any) -> str:
    if isinstance(content, str):
        return " ".join(content.split())
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                value = item.get("text") or item.get("content")
                if value:
                    parts.append(str(value))
            elif item is not None:
                parts.append(str(item))
        return " ".join(" ".join(parts).split())
    if content is None:
        return ""
    return " ".join(str(content).split())


def start_agent_turn(
    db: Session,
    task_run: TaskRun | None,
    *,
    agent_name: str,
    summary: str = "",
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
        EventType.AGENT_TURN_STARTED,
        agent_name=agent_name,
        summary=summary or "",
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
        EventType.LLM_REQUEST_CREATED,
        agent_name=agent_name,
        summary=summary or "",
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
        EventType.LLM_RESPONSE_STARTED,
        agent_name=agent_name,
        summary=summary or "",
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
        EventType.LLM_RESPONSE_COMPLETED,
        agent_name=agent_name,
        summary=summary or "",
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
        result_payload = {
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
        serialized_tool_results.append(result_payload)
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
        EventType.TOOL_ROUND_RECORDED,
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
                source=EventType.TOOL_CALL_BLOCKED,
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
            validate_transition(task_run.status, "paused")
            task_run.status = "paused"
            task_run.blocked_by_queue_item_id = queue_item.id
            task_run.summary = f"Paused awaiting approval for {blocked_tool['tool_name']}."
            db.add(task_run)
            db.commit()
            db.refresh(task_run)
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
                EventType.APPROVAL_QUEUE_ITEM_CREATED,
                agent_name=agent_name,
                summary="",
                payload=build_approval_queue_item_created_event_payload(queue_item),
            )
        append_task_event(
            db,
            task_run,
            EventType.TOOL_CALL_BLOCKED,
            agent_name=agent_name,
            summary="",
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
        EventType.TOOL_CALL_STARTED,
        agent_name=agent_name,
        summary="",
        payload=merged_payload,
    )


def complete_agent_turn(
    db: Session,
    task_run: TaskRun | None,
    *,
    agent_name: str,
    response_content: str = "",
    message_id: int | None = None,
    summary: str = "",
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
        EventType.AGENT_TURN_COMPLETED,
        agent_name=agent_name,
        message_id=message_id,
        summary=summary or "",
        payload=merged_payload,
    )


def compact_runtime_text(value: Any, *, limit: int = 600) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."
