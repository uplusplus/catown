# -*- coding: utf-8 -*-
"""Shared success/failure finalizers for single-agent streaming sessions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict

from sqlalchemy.orm import Session

from models.database import TaskRun
from services.run_ledger import append_task_event, complete_task_run


SaveMessage = Callable[..., Awaitable[Any]]
PublishMessage = Callable[..., Awaitable[Any]]
RecordTurnCompleted = Callable[..., Any]
PersistFailure = Callable[..., Awaitable[Any]]
ScheduleMemoryExtraction = Callable[[], Any]
MessageMetadataBuilder = Callable[[str | None], Dict[str, Any]]
CompactSummary = Callable[[Any], str]


@dataclass(frozen=True)
class SingleAgentStreamFinalizeResult:
    payload: Dict[str, Any]
    saved_message: Any | None = None


async def finalize_single_agent_stream_success(
    db: Session,
    task_run: TaskRun | None,
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
) -> SingleAgentStreamFinalizeResult:
    """Persist the final stream response and return the terminal done payload."""

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
    return SingleAgentStreamFinalizeResult(
        payload={
            "type": "done",
            "agent_name": agent_name,
            "message_id": saved_message.id,
            "client_turn_id": client_turn_id,
        },
        saved_message=saved_message,
    )


async def finalize_single_agent_stream_failure(
    db: Session,
    task_run: TaskRun | None,
    *,
    chatroom_id: int,
    client_turn_id: str | None,
    error: Exception | str,
    agent_name: str | None,
    agent_id: int | None,
    final_message_saved: bool,
    persist_failure: PersistFailure,
    failure_summary: str,
) -> SingleAgentStreamFinalizeResult:
    """Persist stream failure state and return the terminal SSE payload."""

    error_text = str(error)
    if task_run is not None and (task_run.status or "running") == "running":
        append_task_event(
            db,
            task_run,
            "task_run_failed",
            agent_name=agent_name,
            summary=failure_summary,
            payload={"error": error_text},
        )
        complete_task_run(db, task_run, status="failed", summary=error_text)

    if final_message_saved:
        return SingleAgentStreamFinalizeResult(
            payload={"type": "error", "error": error_text},
            saved_message=None,
        )

    saved_message = await persist_failure(
        db,
        chatroom_id=chatroom_id,
        client_turn_id=client_turn_id,
        error_message=error_text,
        agent_name=agent_name,
        agent_id=agent_id,
    )
    return SingleAgentStreamFinalizeResult(
        payload={
            "type": "done",
            "agent_name": agent_name,
            "message_id": saved_message.id,
            "client_turn_id": client_turn_id,
        },
        saved_message=saved_message,
    )
