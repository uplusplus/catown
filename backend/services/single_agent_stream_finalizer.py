# -*- coding: utf-8 -*-
"""Shared success/failure finalizers for single-agent streaming sessions."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from services.single_agent_session_terminal import (
    SingleAgentSessionTerminalResult as SingleAgentStreamFinalizeResult,
    persist_single_agent_session_success,
    terminalize_single_agent_session_failure,
)


PersistFailure = Callable[..., Awaitable[Any]]


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

    return await persist_single_agent_session_success(
        db,
        task_run,
        chatroom_id=chatroom_id,
        client_turn_id=client_turn_id,
        agent_id=agent_id,
        agent_name=agent_name,
        final_content=final_content,
        save_message=save_message,
        publish_message=publish_message,
        record_turn_completed=record_turn_completed,
        message_metadata=message_metadata,
        compact_summary=compact_summary,
        completion_summary=completion_summary,
        schedule_memory_extraction=schedule_memory_extraction,
        build_payload=lambda saved_message, resolved_content: {
            "type": "done",
            "agent_name": agent_name,
            "message_id": saved_message.id,
            "client_turn_id": client_turn_id,
        },
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
        terminalize_single_agent_session_failure(
            db,
            task_run,
            error=error,
            failure_summary=failure_summary,
            agent_name=agent_name,
        )

    if final_message_saved:
        return SingleAgentStreamFinalizeResult(
            payload={"type": "error", "error": error_text},
            saved_message=None,
            error_text=error_text,
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
        error_text=error_text,
    )
