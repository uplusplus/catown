# -*- coding: utf-8 -*-
"""Shared success/failure finalizers for non-stream single-agent sessions."""

from __future__ import annotations

from typing import Any

from services.single_agent_session_terminal import (
    PostPublishSuccess,
    SingleAgentSessionTerminalResult as SingleAgentSessionFinalizeResult,
    persist_single_agent_session_success,
    terminalize_single_agent_session_failure,
)


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
    post_publish_success: PostPublishSuccess | None = None,
) -> SingleAgentSessionFinalizeResult:
    """Persist a completed non-stream single-agent turn."""

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
        post_publish_success=post_publish_success,
    )


def finalize_single_agent_session_failure(
    db: Any,
    task_run: Any,
    *,
    error: Exception | str,
    failure_summary: str,
) -> SingleAgentSessionFinalizeResult:
    """Terminalize a failed non-stream single-agent session."""

    return terminalize_single_agent_session_failure(
        db,
        task_run,
        error=error,
        failure_summary=failure_summary,
    )
