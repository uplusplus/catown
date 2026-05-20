# -*- coding: utf-8 -*-
"""Shared terminal helpers for single-agent sync/stream session finalizers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict

from services.run_ledger import append_task_event, complete_task_run
from services.delegated_task_guard import (
    build_pending_delegated_work_summary,
    find_incomplete_delegated_child_runs,
)


SaveMessage = Callable[..., Awaitable[Any]]
PublishMessage = Callable[..., Awaitable[Any]]
RecordTurnCompleted = Callable[..., Any]
ScheduleMemoryExtraction = Callable[[], Any]
MessageMetadataBuilder = Callable[[str | None], Dict[str, Any]]
CompactSummary = Callable[[Any], str]
BuildPayload = Callable[[Any, str], Dict[str, Any]]
PostPublishSuccess = Callable[[Any, str, Dict[str, Any]], Awaitable[Any] | Any]


@dataclass(frozen=True)
class SingleAgentSessionTerminalResult:
    saved_message: Any | None = None
    resolved_content: str | None = None
    payload: Dict[str, Any] | None = None
    error_text: str | None = None


async def persist_single_agent_session_success(
    db: Any,
    task_run: Any,
    *,
    chatroom_id: int,
    client_turn_id: str | None,
    agent_id: int | None,
    agent_name: str,
    final_content: str,
    save_message: SaveMessage,
    publish_message: PublishMessage,
    record_turn_completed: RecordTurnCompleted,
    message_metadata: MessageMetadataBuilder,
    compact_summary: CompactSummary,
    completion_summary: str = "",
    schedule_memory_extraction: ScheduleMemoryExtraction | None = None,
    build_payload: BuildPayload | None = None,
    post_publish_success: PostPublishSuccess | None = None,
) -> SingleAgentSessionTerminalResult:
    """Persist a completed single-agent session and return a unified terminal result."""

    resolved_content = final_content or "(Agent returned empty response)"
    metadata = message_metadata(client_turn_id)
    saved_message = await save_message(
        chatroom_id=chatroom_id,
        agent_id=agent_id,
        content=resolved_content,
        message_type="text",
        metadata=metadata,
        agent_name=agent_name,
    )
    await publish_message(
        db,
        chatroom_id,
        message_id=saved_message.id,
        content=resolved_content,
        agent_name=agent_name,
        message_type="text",
        created_at=saved_message.created_at,
        metadata=metadata,
    )
    if post_publish_success is not None:
        await _maybe_await(post_publish_success(saved_message, resolved_content, metadata))
    record_turn_completed(
        db,
        task_run,
        agent_name=agent_name,
        message_id=saved_message.id,
        response_content=resolved_content,
        summary=completion_summary or "",
    )
    pending_delegated_work = find_incomplete_delegated_child_runs(db, task_run)
    if pending_delegated_work:
        pending_summary = build_pending_delegated_work_summary(pending_delegated_work)
        append_task_event(
            db,
            task_run,
            "task_run_waiting_for_delegated_work",
            agent_name=agent_name,
            summary=pending_summary,
            payload={"pending_delegated_work": pending_delegated_work},
        )
    else:
        complete_task_run(db, task_run, summary=compact_summary(resolved_content))
    if schedule_memory_extraction is not None:
        schedule_memory_extraction()
    return SingleAgentSessionTerminalResult(
        saved_message=saved_message,
        resolved_content=resolved_content,
        payload=(build_payload(saved_message, resolved_content) if build_payload is not None else None),
    )


def terminalize_single_agent_session_failure(
    db: Any,
    task_run: Any,
    *,
    error: Exception | str,
    failure_summary: str,
    agent_name: str | None = None,
) -> SingleAgentSessionTerminalResult:
    """Terminalize a failed single-agent session and return a unified result."""

    error_text = str(error)
    append_task_event(
        db,
        task_run,
        "task_run_failed",
        agent_name=agent_name,
        summary=failure_summary,
        payload={"error": error_text},
    )
    complete_task_run(db, task_run, status="failed", summary=error_text)
    return SingleAgentSessionTerminalResult(error_text=error_text)


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value
