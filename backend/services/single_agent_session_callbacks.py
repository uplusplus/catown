# -*- coding: utf-8 -*-
"""Shared callback builders for managed single-agent session finalizers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from services.single_agent_session_finalizer import (
    finalize_single_agent_session_failure,
    finalize_single_agent_session_success,
)
from services.single_agent_session_terminal import (
    CompactSummary,
    MessageMetadataBuilder,
    PublishMessage,
    RecordTurnCompleted,
    SaveMessage,
    ScheduleMemoryExtraction,
)
from services.single_agent_stream_finalizer import (
    PersistFailure,
    finalize_single_agent_stream_failure,
    finalize_single_agent_stream_success,
)


@dataclass(frozen=True)
class SingleAgentSessionSuccessCallbackDeps:
    db: Any
    task_run: Any
    chatroom_id: int
    client_turn_id: str | None
    agent_id: int | None
    agent_name: str
    save_message: SaveMessage
    publish_message: PublishMessage
    record_turn_completed: RecordTurnCompleted
    message_metadata: MessageMetadataBuilder
    compact_summary: CompactSummary
    completion_summary: str
    build_memory_extraction: Callable[[str], ScheduleMemoryExtraction | None] | None = None


@dataclass(frozen=True)
class SingleAgentSessionFailureCallbackDeps:
    db: Any
    task_run: Any
    failure_summary: str | Callable[[Exception], str]


@dataclass(frozen=True)
class SingleAgentStreamFailureCallbackDeps:
    db: Any
    task_run: Any
    chatroom_id: int
    client_turn_id: str | None
    agent_name: str | None
    agent_id: int | None
    final_message_saved: bool
    persist_failure: PersistFailure
    failure_summary: str | Callable[[Exception], str]


def build_single_agent_session_success_callback(
    deps: SingleAgentSessionSuccessCallbackDeps,
):
    """Build the managed sync-session success callback."""

    async def _finalize(final_content: str):
        schedule_memory_extraction = None
        if deps.build_memory_extraction is not None:
            schedule_memory_extraction = deps.build_memory_extraction(final_content)
        return await finalize_single_agent_session_success(
            deps.db,
            deps.task_run,
            chatroom_id=deps.chatroom_id,
            client_turn_id=deps.client_turn_id,
            agent_id=deps.agent_id,
            agent_name=deps.agent_name,
            final_content=final_content,
            save_message=deps.save_message,
            publish_message=deps.publish_message,
            record_turn_completed=deps.record_turn_completed,
            message_metadata=deps.message_metadata,
            compact_summary=deps.compact_summary,
            completion_summary=deps.completion_summary,
            schedule_memory_extraction=schedule_memory_extraction,
        )

    return _finalize


def build_single_agent_stream_success_callback(
    deps: SingleAgentSessionSuccessCallbackDeps,
):
    """Build the managed stream-session success callback."""

    async def _finalize(final_content: str):
        schedule_memory_extraction = None
        if deps.build_memory_extraction is not None:
            schedule_memory_extraction = deps.build_memory_extraction(final_content)
        return await finalize_single_agent_stream_success(
            deps.db,
            deps.task_run,
            chatroom_id=deps.chatroom_id,
            client_turn_id=deps.client_turn_id,
            agent_id=deps.agent_id,
            agent_name=deps.agent_name,
            final_content=final_content,
            save_message=deps.save_message,
            publish_message=deps.publish_message,
            record_turn_completed=deps.record_turn_completed,
            message_metadata=deps.message_metadata,
            compact_summary=deps.compact_summary,
            completion_summary=deps.completion_summary,
            schedule_memory_extraction=schedule_memory_extraction,
        )

    return _finalize


def build_single_agent_session_failure_callback(
    deps: SingleAgentSessionFailureCallbackDeps,
):
    """Build the managed sync-session failure callback."""

    def _finalize(error: Exception):
        return finalize_single_agent_session_failure(
            deps.db,
            deps.task_run,
            error=error,
            failure_summary=_resolve_failure_summary(deps.failure_summary, error),
        )

    return _finalize


def build_single_agent_stream_failure_callback(
    deps: SingleAgentStreamFailureCallbackDeps,
):
    """Build the managed stream-session failure callback."""

    async def _finalize(error: Exception):
        return await finalize_single_agent_stream_failure(
            deps.db,
            deps.task_run,
            chatroom_id=deps.chatroom_id,
            client_turn_id=deps.client_turn_id,
            error=error,
            agent_name=deps.agent_name,
            agent_id=deps.agent_id,
            final_message_saved=deps.final_message_saved,
            persist_failure=deps.persist_failure,
            failure_summary=_resolve_failure_summary(deps.failure_summary, error),
        )

    return _finalize


def _resolve_failure_summary(
    summary: str | Callable[[Exception], str],
    error: Exception,
) -> str:
    if callable(summary):
        return summary(error)
    return summary
