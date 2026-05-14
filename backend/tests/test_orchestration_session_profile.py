import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.orchestration_session_profile import (  # noqa: E402
    build_orchestration_session_runtime_profile,
    build_nonstream_orchestration_runtime_deps,
    build_orchestration_recovery_runtime_deps,
    build_orchestration_turn_runtime_profile,
    build_stream_orchestration_runtime_deps,
)


def _turn_profile():
    return build_orchestration_turn_runtime_profile(
        ensure_collaboration_context=lambda agents, chatroom_id: None,
        prepare_chat_turn_runtime=lambda **kwargs: None,
        assemble_chat_messages=lambda **kwargs: [],
        build_context_compaction_callback=lambda *args, **kwargs: None,
        save_message=lambda **kwargs: None,
        message_metadata=lambda client_turn_id=None: {"client_turn_id": client_turn_id},
        schedule_memory_extraction=lambda *args, **kwargs: None,
        build_llm_card_payload=lambda **kwargs: kwargs,
        snapshot_messages=lambda messages: list(messages),
        preview_tool_calls=lambda tool_calls: [],
        format_prompt_messages=lambda messages: str(messages),
        tool_result_success=lambda result: True,
        max_tool_iterations=4,
    )


def test_build_orchestration_turn_runtime_profile_exposes_sync_and_stream_callables():
    profile = _turn_profile()

    assert callable(profile.execute_turn)
    assert callable(profile.iter_agent_events)


def test_build_nonstream_orchestration_runtime_deps_reuses_turn_profile():
    profile = _turn_profile()
    deps = build_nonstream_orchestration_runtime_deps(
        turn_profile=profile,
        publish_message=lambda *args, **kwargs: None,
        message_metadata={"client_turn_id": "turn-1"},
        build_step_context=lambda step, agent, agent_label: {"extra_context": ""},
        log_agent_type=lambda agent: "developer",
    )

    assert deps.execute_turn is profile.execute_turn
    assert deps.message_metadata == {"client_turn_id": "turn-1"}


def test_build_stream_orchestration_runtime_deps_reuses_turn_profile():
    profile = _turn_profile()
    deps = build_stream_orchestration_runtime_deps(
        turn_profile=profile,
        save_message=lambda **kwargs: None,
        publish_message=lambda *args, **kwargs: None,
        record_turn_completed=lambda *args, **kwargs: None,
        message_metadata=lambda client_turn_id=None: {"client_turn_id": client_turn_id},
        schedule_memory_extraction=lambda *args, **kwargs: None,
        build_checkpoint_snapshot=lambda task_run: {},
        find_stage_policy=lambda policy, step_id: None,
        agent_name_of=lambda agent: "Developer",
        fail_task_run=lambda *args, **kwargs: None,
        finalize_task_run=lambda *args, **kwargs: None,
        set_active_agent=lambda *args, **kwargs: None,
    )

    assert deps.iter_agent_events is profile.iter_agent_events
    assert callable(deps.message_metadata)


def test_build_orchestration_recovery_runtime_deps_reuses_turn_profile():
    profile = _turn_profile()
    deps = build_orchestration_recovery_runtime_deps(
        turn_profile=profile,
        build_checkpoint_snapshot=lambda task_run: {},
        describe_recovery_continuation_state=lambda snapshot: {},
        rebuild_recovery_state=lambda **kwargs: ([], {}, "", []),
        publish_message=lambda *args, **kwargs: None,
        message_metadata={"client_turn_id": "turn-2"},
        renew_lease=lambda: None,
        recovery_owner="owner-1",
    )

    assert deps.execute_turn is profile.execute_turn
    assert deps.recovery_owner == "owner-1"


async def test_build_orchestration_session_runtime_profile_nonstream_reuses_turn_profile(monkeypatch):
    captured = {}
    turn_profile = _turn_profile()

    async def _fake_run_nonstream_orchestration_runtime(**kwargs):
        captured["deps"] = kwargs["deps"]
        captured["client_turn_id"] = kwargs["client_turn_id"]

    monkeypatch.setattr(
        "services.orchestration_session_profile.run_nonstream_orchestration_runtime",
        _fake_run_nonstream_orchestration_runtime,
    )

    profile = build_orchestration_session_runtime_profile(
        turn_profile=turn_profile,
        save_message=lambda **kwargs: None,
        publish_message=lambda *args, **kwargs: None,
        record_turn_completed=lambda *args, **kwargs: None,
        message_metadata=lambda client_turn_id=None: {"client_turn_id": client_turn_id},
        schedule_memory_extraction=lambda *args, **kwargs: None,
        build_checkpoint_snapshot=lambda task_run: {},
        find_stage_policy=lambda policy, step_id: None,
        agent_name_of=lambda agent: "Developer",
        fail_task_run=lambda *args, **kwargs: None,
        finalize_task_run=lambda *args, **kwargs: None,
        log_agent_type=lambda agent: "developer",
    )

    await profile.run_nonstream(
        db=object(),
        task_run=None,
        queue=object(),
        resolved_agents=[],
        chatroom_id=1,
        project=None,
        agents=[],
        user_message="Build it",
        client_turn_id="turn-1",
        output_state=object(),
        pending_handoffs={},
        orchestration_policy=object(),
        build_step_context=lambda step, agent, agent_label: {"extra_context": ""},
    )

    assert captured["deps"].execute_turn is turn_profile.execute_turn
    assert captured["client_turn_id"] == "turn-1"


async def test_build_orchestration_session_runtime_profile_stream_reuses_turn_profile(monkeypatch):
    captured = {}
    turn_profile = _turn_profile()

    async def _fake_iter_stream_orchestration_session_events(**kwargs):
        captured["deps"] = kwargs["deps"]
        yield {"type": "done"}

    monkeypatch.setattr(
        "services.orchestration_session_profile.iter_stream_orchestration_session_events",
        _fake_iter_stream_orchestration_session_events,
    )

    profile = build_orchestration_session_runtime_profile(
        turn_profile=turn_profile,
        save_message=lambda **kwargs: None,
        publish_message=lambda *args, **kwargs: None,
        record_turn_completed=lambda *args, **kwargs: None,
        message_metadata=lambda client_turn_id=None: {"client_turn_id": client_turn_id},
        schedule_memory_extraction=lambda *args, **kwargs: None,
        build_checkpoint_snapshot=lambda task_run: {},
        find_stage_policy=lambda policy, step_id: None,
        agent_name_of=lambda agent: "Developer",
        fail_task_run=lambda *args, **kwargs: None,
        finalize_task_run=lambda *args, **kwargs: None,
        log_agent_type=lambda agent: "developer",
    )

    events = [
        event
        async for event in profile.iter_stream_session_events(
            db=object(),
            task_run=None,
            prepared_runtime=object(),
            chatroom=object(),
            project=None,
            agents=[],
            agent_names=["developer"],
            user_message="Build it",
            client_turn_id="turn-2",
            standalone_note="",
        )
    ]

    assert events == [{"type": "done"}]
    assert captured["deps"].iter_agent_events is turn_profile.iter_agent_events


async def test_build_orchestration_session_runtime_profile_recovery_reuses_turn_profile(monkeypatch):
    captured = {}
    turn_profile = _turn_profile()

    async def _fake_run_orchestration_recovery_runtime(**kwargs):
        captured["deps"] = kwargs["deps"]
        return {"status": "ok"}

    monkeypatch.setattr(
        "services.orchestration_session_profile.run_orchestration_recovery_runtime",
        _fake_run_orchestration_recovery_runtime,
    )

    profile = build_orchestration_session_runtime_profile(
        turn_profile=turn_profile,
        save_message=lambda **kwargs: None,
        publish_message=lambda *args, **kwargs: None,
        record_turn_completed=lambda *args, **kwargs: None,
        message_metadata=lambda client_turn_id=None: {"client_turn_id": client_turn_id},
        schedule_memory_extraction=lambda *args, **kwargs: None,
        build_checkpoint_snapshot=lambda task_run: {},
        find_stage_policy=lambda policy, step_id: None,
        agent_name_of=lambda agent: "Developer",
        fail_task_run=lambda *args, **kwargs: None,
        finalize_task_run=lambda *args, **kwargs: None,
        log_agent_type=lambda agent: "developer",
    )

    result = await profile.run_recovery(
        db=object(),
        task_run=type("TaskRun", (), {"client_turn_id": "turn-3"})(),
        task_run_id=3,
        chatroom=object(),
        project=None,
        agents=[],
        agent_names=["developer"],
        resolved_agents=[],
        plan=object(),
        orchestration_policy=object(),
        trigger="startup",
        lease_expires_at=None,
        describe_recovery_continuation_state=lambda snapshot: {},
        rebuild_recovery_state=lambda **kwargs: ([], {}, "", []),
        renew_lease=lambda: None,
        recovery_owner="owner-1",
    )

    assert result == {"status": "ok"}
    assert captured["deps"].execute_turn is turn_profile.execute_turn
