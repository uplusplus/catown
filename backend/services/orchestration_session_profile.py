# -*- coding: utf-8 -*-
"""Higher-level runtime profile builders for orchestration chat paths."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Any, AsyncIterator, Awaitable, Callable, Dict

from services.orchestration_agent_turn import (
    OrchestrationAgentTurnDeps,
    StreamOrchestrationAgentTurnDeps,
    iter_stream_orchestration_agent_turn_events,
    run_orchestration_agent_turn,
)
from services.orchestration_recovery_runner import (
    OrchestrationRecoveryRuntimeDeps,
    run_orchestration_recovery_runtime,
)
from services.orchestration_runtime_runner import (
    NonstreamOrchestrationRuntimeDeps,
    run_nonstream_orchestration_runtime,
)
from services.orchestration_stream_runner import (
    StreamOrchestrationRuntimeDeps,
    iter_stream_orchestration_session_events,
)


@dataclass(frozen=True)
class OrchestrationTurnRuntimeProfile:
    """Execution profile for orchestration agent turns across sync and stream entrypoints."""

    execute_turn: Callable[..., Awaitable[tuple[str | None, Any | None]]]
    iter_agent_events: Callable[..., AsyncIterator[dict[str, Any]]]


@dataclass(frozen=True)
class OrchestrationSessionRuntimeProfile:
    """Higher-level session façade for orchestration sync, stream, and recovery entrypoints."""

    turn_profile: OrchestrationTurnRuntimeProfile
    run_nonstream: Callable[..., Awaitable[None]]
    iter_stream_session_events: Callable[..., AsyncIterator[Any]]
    run_recovery: Callable[..., Awaitable[Any]]


def build_orchestration_turn_runtime_profile(
    *,
    ensure_collaboration_context: Callable[[list[Any], int], None],
    prepare_chat_turn_runtime: Callable[..., Awaitable[Any]],
    assemble_chat_messages: Callable[..., list[dict[str, Any]]],
    build_context_compaction_callback: Callable[..., Any],
    save_message: Callable[..., Awaitable[Any]],
    message_metadata: Callable[[str | None], dict[str, Any]],
    schedule_memory_extraction: Callable[..., Any],
    build_llm_card_payload: Callable[..., dict[str, Any]],
    snapshot_messages: Callable[..., Any],
    preview_tool_calls: Callable[..., Any],
    format_prompt_messages: Callable[..., str],
    tool_result_success: Callable[[Any], bool],
    max_tool_iterations: int,
) -> OrchestrationTurnRuntimeProfile:
    """Build the shared sync/stream turn profile used by orchestration runtimes."""

    sync_deps = OrchestrationAgentTurnDeps(
        ensure_collaboration_context=ensure_collaboration_context,
        prepare_chat_turn_runtime=prepare_chat_turn_runtime,
        build_context_compaction_callback=build_context_compaction_callback,
        assemble_chat_messages=assemble_chat_messages,
        save_message=save_message,
        message_metadata=message_metadata,
        schedule_memory_extraction=schedule_memory_extraction,
        max_tool_iterations=max_tool_iterations,
    )
    stream_deps = StreamOrchestrationAgentTurnDeps(
        ensure_collaboration_context=ensure_collaboration_context,
        prepare_chat_turn_runtime=prepare_chat_turn_runtime,
        assemble_chat_messages=assemble_chat_messages,
        build_llm_card_payload=build_llm_card_payload,
        snapshot_messages=snapshot_messages,
        preview_tool_calls=preview_tool_calls,
        format_prompt_messages=format_prompt_messages,
        tool_result_success=tool_result_success,
        max_tool_iterations=max_tool_iterations,
    )
    return OrchestrationTurnRuntimeProfile(
        execute_turn=partial(run_orchestration_agent_turn, deps=sync_deps),
        iter_agent_events=partial(iter_stream_orchestration_agent_turn_events, deps=stream_deps),
    )


def build_nonstream_orchestration_runtime_deps(
    *,
    turn_profile: OrchestrationTurnRuntimeProfile,
    publish_message: Callable[..., Awaitable[Any]],
    message_metadata: Dict[str, Any] | None = None,
    before_next_step: Callable[[], Awaitable[Any] | Any] | None = None,
    build_step_context: Callable[[Any, Any, str], Awaitable[dict[str, Any]] | dict[str, Any]] | None = None,
    log_agent_type: Callable[[Any], str] | None = None,
) -> NonstreamOrchestrationRuntimeDeps:
    """Build the non-stream orchestration runtime deps from a shared turn profile."""

    return NonstreamOrchestrationRuntimeDeps(
        execute_turn=turn_profile.execute_turn,
        publish_message=publish_message,
        message_metadata=message_metadata,
        before_next_step=before_next_step,
        build_step_context=build_step_context,
        log_agent_type=log_agent_type,
    )


def build_stream_orchestration_runtime_deps(
    *,
    turn_profile: OrchestrationTurnRuntimeProfile,
    save_message: Callable[..., Awaitable[Any]],
    publish_message: Callable[..., Awaitable[Any]],
    record_turn_completed: Callable[..., Any],
    message_metadata: Callable[[str | None], Dict[str, Any]],
    schedule_memory_extraction: Callable[..., Any],
    build_checkpoint_snapshot: Callable[[Any], Dict[str, Any]],
    find_stage_policy: Callable[[Any, str], Any],
    agent_name_of: Callable[[Any], str],
    fail_task_run: Callable[..., Any],
    finalize_task_run: Callable[..., Any],
    set_active_agent: Callable[[str, Any], Any] | None = None,
) -> StreamOrchestrationRuntimeDeps:
    """Build the stream orchestration runtime deps from a shared turn profile."""

    return StreamOrchestrationRuntimeDeps(
        iter_agent_events=turn_profile.iter_agent_events,
        save_message=save_message,
        publish_message=publish_message,
        record_turn_completed=record_turn_completed,
        message_metadata=message_metadata,
        schedule_memory_extraction=schedule_memory_extraction,
        build_checkpoint_snapshot=build_checkpoint_snapshot,
        find_stage_policy=find_stage_policy,
        agent_name_of=agent_name_of,
        fail_task_run=fail_task_run,
        finalize_task_run=finalize_task_run,
        set_active_agent=set_active_agent,
    )


def build_orchestration_recovery_runtime_deps(
    *,
    turn_profile: OrchestrationTurnRuntimeProfile,
    build_checkpoint_snapshot: Callable[[Any], Dict[str, Any]],
    describe_recovery_continuation_state: Callable[[Any], Dict[str, Any]],
    rebuild_recovery_state: Callable[..., tuple[list[Dict[str, str]], Dict[str, list[Dict[str, str]]], str, list[str]]],
    publish_message: Callable[..., Awaitable[Any]],
    message_metadata: Dict[str, Any] | None,
    renew_lease: Callable[[], Any],
    recovery_owner: str,
) -> OrchestrationRecoveryRuntimeDeps:
    """Build the recovery runtime deps from the shared orchestration turn profile."""

    return OrchestrationRecoveryRuntimeDeps(
        build_checkpoint_snapshot=build_checkpoint_snapshot,
        describe_recovery_continuation_state=describe_recovery_continuation_state,
        rebuild_recovery_state=rebuild_recovery_state,
        execute_turn=turn_profile.execute_turn,
        publish_message=publish_message,
        message_metadata=message_metadata,
        renew_lease=renew_lease,
        recovery_owner=recovery_owner,
    )


def build_orchestration_session_runtime_profile(
    *,
    turn_profile: OrchestrationTurnRuntimeProfile,
    save_message: Callable[..., Awaitable[Any]],
    publish_message: Callable[..., Awaitable[Any]],
    record_turn_completed: Callable[..., Any],
    message_metadata: Callable[[str | None], Dict[str, Any]],
    schedule_memory_extraction: Callable[..., Any],
    build_checkpoint_snapshot: Callable[[Any], Dict[str, Any]],
    find_stage_policy: Callable[[Any, str], Any],
    agent_name_of: Callable[[Any], str],
    fail_task_run: Callable[..., Any],
    finalize_task_run: Callable[..., Any],
    log_agent_type: Callable[[Any], str] | None = None,
) -> OrchestrationSessionRuntimeProfile:
    """Build the higher-level session façade around orchestration runtime entrypoints."""

    async def _run_nonstream(
        *,
        db: Any,
        task_run: Any,
        queue: Any,
        resolved_agents: list[Any],
        chatroom_id: int,
        project: Any,
        agents: list[Any],
        user_message: str,
        client_turn_id: str | None,
        output_state: Any,
        pending_handoffs: Dict[str, list[Dict[str, str]]],
        orchestration_policy: Any,
        build_step_context: Callable[[Any, Any, str], Awaitable[dict[str, Any]] | dict[str, Any]] | None = None,
        before_next_step: Callable[[], Awaitable[Any] | Any] | None = None,
    ) -> None:
        return await run_nonstream_orchestration_runtime(
            db=db,
            task_run=task_run,
            queue=queue,
            resolved_agents=resolved_agents,
            chatroom_id=chatroom_id,
            project=project,
            agents=agents,
            user_message=user_message,
            client_turn_id=client_turn_id,
            output_state=output_state,
            pending_handoffs=pending_handoffs,
            orchestration_policy=orchestration_policy,
            deps=build_nonstream_orchestration_runtime_deps(
                turn_profile=turn_profile,
                publish_message=publish_message,
                message_metadata=message_metadata(client_turn_id),
                before_next_step=before_next_step,
                build_step_context=build_step_context,
                log_agent_type=log_agent_type,
            ),
        )

    async def _iter_stream_session_events(
        *,
        db: Any,
        task_run: Any,
        prepared_runtime: Any,
        chatroom: Any,
        project: Any,
        agents: list[Any],
        agent_names: list[str],
        user_message: str,
        client_turn_id: str | None,
        standalone_note: str,
        set_active_agent: Callable[[str, Any], Any] | None = None,
    ) -> AsyncIterator[Any]:
        async for event in iter_stream_orchestration_session_events(
            db=db,
            task_run=task_run,
            prepared_runtime=prepared_runtime,
            chatroom=chatroom,
            project=project,
            agents=agents,
            agent_names=agent_names,
            user_message=user_message,
            client_turn_id=client_turn_id,
            standalone_note=standalone_note,
            deps=build_stream_orchestration_runtime_deps(
                turn_profile=turn_profile,
                save_message=save_message,
                publish_message=publish_message,
                record_turn_completed=record_turn_completed,
                message_metadata=message_metadata,
                schedule_memory_extraction=schedule_memory_extraction,
                build_checkpoint_snapshot=build_checkpoint_snapshot,
                find_stage_policy=find_stage_policy,
                agent_name_of=agent_name_of,
                fail_task_run=fail_task_run,
                finalize_task_run=finalize_task_run,
                set_active_agent=set_active_agent,
            ),
        ):
            yield event

    async def _run_recovery(
        *,
        db: Any,
        task_run: Any,
        task_run_id: int,
        chatroom: Any,
        project: Any,
        agents: list[Any],
        agent_names: list[str],
        resolved_agents: list[Any],
        plan: Any,
        orchestration_policy: Any,
        trigger: str,
        lease_expires_at: Any,
        describe_recovery_continuation_state: Callable[[Any], Dict[str, Any]],
        rebuild_recovery_state: Callable[..., tuple[list[Dict[str, str]], Dict[str, list[Dict[str, str]]], str, list[str]]],
        renew_lease: Callable[[], Any],
        recovery_owner: str,
    ) -> Any:
        return await run_orchestration_recovery_runtime(
            db=db,
            task_run=task_run,
            task_run_id=task_run_id,
            chatroom=chatroom,
            project=project,
            agents=agents,
            agent_names=agent_names,
            resolved_agents=resolved_agents,
            plan=plan,
            orchestration_policy=orchestration_policy,
            trigger=trigger,
            lease_expires_at=lease_expires_at,
            deps=build_orchestration_recovery_runtime_deps(
                turn_profile=turn_profile,
                build_checkpoint_snapshot=build_checkpoint_snapshot,
                describe_recovery_continuation_state=describe_recovery_continuation_state,
                rebuild_recovery_state=rebuild_recovery_state,
                publish_message=publish_message,
                message_metadata=message_metadata(getattr(task_run, "client_turn_id", None)),
                renew_lease=renew_lease,
                recovery_owner=recovery_owner,
            ),
        )

    return OrchestrationSessionRuntimeProfile(
        turn_profile=turn_profile,
        run_nonstream=_run_nonstream,
        iter_stream_session_events=_iter_stream_session_events,
        run_recovery=_run_recovery,
    )
