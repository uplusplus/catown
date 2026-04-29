# -*- coding: utf-8 -*-
"""Shared callback builders for managed single-agent session finalizers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

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


ExtractMemories = Callable[[int, str, str, str], Awaitable[Any]]
ScheduleTask = Callable[[Awaitable[Any]], Any]
StreamFailureMetadataBuilder = Callable[[str | None, dict[str, Any] | None], dict[str, Any]]
StreamFailureDetailBuilder = Callable[[], str]


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


@dataclass(frozen=True)
class SingleAgentManagedCallbackSet:
    finalize_success: Callable[[str], Awaitable[Any]]
    finalize_failure: Callable[[Exception], Awaitable[Any] | Any]


@dataclass(frozen=True)
class SingleAgentSyncCallbackProfile:
    db: Any
    task_run: Any
    chatroom_id: int
    client_turn_id: str | None
    agent_id: int | None
    agent_name: str
    agent_type: str
    user_message: str
    save_message: SaveMessage
    publish_message: PublishMessage
    record_turn_completed: RecordTurnCompleted
    message_metadata: MessageMetadataBuilder
    compact_summary: CompactSummary
    completion_summary: str
    failure_summary: str | Callable[[Exception], str]
    extract_memories: ExtractMemories
    min_response_length: int = 30


@dataclass(frozen=True)
class SingleAgentStreamCallbackProfile:
    db: Any
    task_run: Any
    chatroom_id: int
    client_turn_id: str | None
    agent_id: int | None
    agent_name: str
    agent_type: str
    user_message: str
    save_message: SaveMessage
    publish_message: PublishMessage
    record_turn_completed: RecordTurnCompleted
    message_metadata: MessageMetadataBuilder
    compact_summary: CompactSummary
    completion_summary: str
    failure_summary: str | Callable[[Exception], str]
    extract_memories: ExtractMemories
    stream_failure_message_metadata: StreamFailureMetadataBuilder
    failure_agent_name: str | None = None
    failure_agent_id: int | None = None
    detail_builder: StreamFailureDetailBuilder | None = None
    final_message_saved: bool = False
    empty_response_text: str = "(Agent returned empty response)"
    min_response_length: int = 30


def build_single_agent_sync_callbacks(
    profile: SingleAgentSyncCallbackProfile,
) -> SingleAgentManagedCallbackSet:
    """Build the standard managed callback pair for a sync single-agent turn."""

    return SingleAgentManagedCallbackSet(
        finalize_success=build_single_agent_session_success_callback(
            SingleAgentSessionSuccessCallbackDeps(
                db=profile.db,
                task_run=profile.task_run,
                chatroom_id=profile.chatroom_id,
                client_turn_id=profile.client_turn_id,
                agent_id=profile.agent_id,
                agent_name=profile.agent_name,
                save_message=profile.save_message,
                publish_message=profile.publish_message,
                record_turn_completed=profile.record_turn_completed,
                message_metadata=profile.message_metadata,
                compact_summary=profile.compact_summary,
                completion_summary=profile.completion_summary,
                build_memory_extraction=build_single_agent_memory_extraction_callback(
                    extract_memories=profile.extract_memories,
                    agent_id=profile.agent_id,
                    agent_type=profile.agent_type,
                    user_message=profile.user_message,
                    min_response_length=profile.min_response_length,
                ),
            )
        ),
        finalize_failure=build_single_agent_session_failure_callback(
            SingleAgentSessionFailureCallbackDeps(
                db=profile.db,
                task_run=profile.task_run,
                failure_summary=profile.failure_summary,
            )
        ),
    )


def build_single_agent_stream_callbacks(
    profile: SingleAgentStreamCallbackProfile,
) -> SingleAgentManagedCallbackSet:
    """Build the standard managed callback pair for a streaming single-agent turn."""

    return SingleAgentManagedCallbackSet(
        finalize_success=build_single_agent_stream_success_callback(
            SingleAgentSessionSuccessCallbackDeps(
                db=profile.db,
                task_run=profile.task_run,
                chatroom_id=profile.chatroom_id,
                client_turn_id=profile.client_turn_id,
                agent_id=profile.agent_id,
                agent_name=profile.agent_name,
                save_message=profile.save_message,
                publish_message=profile.publish_message,
                record_turn_completed=profile.record_turn_completed,
                message_metadata=profile.message_metadata,
                compact_summary=profile.compact_summary,
                completion_summary=profile.completion_summary,
                build_memory_extraction=build_single_agent_memory_extraction_callback(
                    extract_memories=profile.extract_memories,
                    agent_id=profile.agent_id,
                    agent_type=profile.agent_type,
                    user_message=profile.user_message,
                    empty_response_text=profile.empty_response_text,
                    min_response_length=profile.min_response_length,
                ),
            )
        ),
        finalize_failure=build_single_agent_stream_failure_callback(
            SingleAgentStreamFailureCallbackDeps(
                db=profile.db,
                task_run=profile.task_run,
                chatroom_id=profile.chatroom_id,
                client_turn_id=profile.client_turn_id,
                agent_name=profile.failure_agent_name or profile.agent_name,
                agent_id=profile.failure_agent_id if profile.failure_agent_id is not None else profile.agent_id,
                final_message_saved=profile.final_message_saved,
                persist_failure=build_single_agent_stream_persist_failure_callback(
                    message_metadata=profile.stream_failure_message_metadata,
                    detail_builder=profile.detail_builder,
                ),
                failure_summary=profile.failure_summary,
            )
        ),
    )


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


def build_single_agent_memory_extraction_callback(
    *,
    extract_memories: ExtractMemories,
    agent_id: int | None,
    agent_type: str,
    user_message: str,
    empty_response_text: str | None = None,
    min_response_length: int = 30,
    schedule_task: ScheduleTask = asyncio.create_task,
):
    """Build a response-aware memory extraction policy for single-agent turns."""

    def _build(response_content: str) -> ScheduleMemoryExtraction | None:
        if agent_id is None:
            return None
        resolved_content = str(response_content or "").strip()
        if not resolved_content and empty_response_text is not None:
            resolved_content = empty_response_text
        if len(resolved_content) <= min_response_length:
            return None

        def _schedule():
            return schedule_task(
                extract_memories(agent_id, agent_type, user_message, resolved_content)
            )

        return _schedule

    return _build


def build_single_agent_stream_persist_failure_callback(
    *,
    message_metadata: StreamFailureMetadataBuilder,
    detail_builder: StreamFailureDetailBuilder | None = None,
) -> PersistFailure:
    """Build the shared persist-failure adapter for single-agent stream fallbacks."""

    async def _persist(current_db: Any, **kwargs):
        from services.stream_runtime_persistence import persist_stream_failure

        detail = detail_builder() if detail_builder is not None else None
        return await persist_stream_failure(
            current_db,
            message_metadata=message_metadata,
            detail=detail,
            **kwargs,
        )

    return _persist


def _resolve_failure_summary(
    summary: str | Callable[[Exception], str],
    error: Exception,
) -> str:
    if callable(summary):
        return summary(error)
    return summary
