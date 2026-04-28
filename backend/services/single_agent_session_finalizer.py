# -*- coding: utf-8 -*-
"""Shared success/failure finalizers for non-stream single-agent sessions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict

from services.run_ledger import append_task_event, complete_task_run


SaveMessage = Callable[..., Awaitable[Any]]
PublishMessage = Callable[..., Awaitable[Any]]
RecordTurnCompleted = Callable[..., Any]
ScheduleMemoryExtraction = Callable[[], Any]
MessageMetadataBuilder = Callable[[str | None], Dict[str, Any]]
CompactSummary = Callable[[Any], str]


@dataclass(frozen=True)
class SingleAgentSessionFinalizeResult:
    saved_message: Any | None = None


async def finalize_single_agent_session_success(
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
    completion_summary: str,
    schedule_memory_extraction: ScheduleMemoryExtraction | None = None,
) -> SingleAgentSessionFinalizeResult:
    """Persist a completed non-stream single-agent turn."""

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
    record_turn_completed(
        db,
        task_run,
        agent_name=agent_name,
        message_id=saved_message.id,
        response_content=resolved_content,
        summary=completion_summary,
    )
    complete_task_run(db, task_run, summary=compact_summary(resolved_content))
    if schedule_memory_extraction is not None:
        schedule_memory_extraction()
    return SingleAgentSessionFinalizeResult(saved_message=saved_message)


def finalize_single_agent_session_failure(
    db: Any,
    task_run: Any,
    *,
    error: Exception | str,
    failure_summary: str,
) -> None:
    """Terminalize a failed non-stream single-agent session."""

    error_text = str(error)
    append_task_event(
        db,
        task_run,
        "task_run_failed",
        summary=failure_summary,
        payload={"error": error_text},
    )
    complete_task_run(db, task_run, status="failed", summary=error_text)
