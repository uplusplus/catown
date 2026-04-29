import asyncio

import pytest

from services.single_agent_session_callbacks import (
    build_single_agent_memory_extraction_callback,
    build_single_agent_session_failure_callback,
    build_single_agent_session_success_callback,
    build_single_agent_stream_callbacks,
    build_single_agent_stream_persist_failure_callback,
    build_single_agent_stream_failure_callback,
    build_single_agent_stream_success_callback,
    build_single_agent_sync_callbacks,
    SingleAgentManagedCallbackSet,
    SingleAgentSessionFailureCallbackDeps,
    SingleAgentSessionSuccessCallbackDeps,
    SingleAgentStreamCallbackProfile,
    SingleAgentStreamFailureCallbackDeps,
    SingleAgentSyncCallbackProfile,
)


@pytest.mark.asyncio
async def test_build_single_agent_session_success_callback_persists_and_schedules_memory():
    calls = []

    async def save_message(**kwargs):
        calls.append(("save", kwargs))
        return type("Saved", (), {"id": 42, "created_at": None})()

    async def publish_message(*args, **kwargs):
        calls.append(("publish", kwargs))

    def record_turn_completed(*args, **kwargs):
        calls.append(("complete", kwargs))

    callback = build_single_agent_session_success_callback(
        SingleAgentSessionSuccessCallbackDeps(
            db=object(),
            task_run=None,
            chatroom_id=7,
            client_turn_id="turn-1",
            agent_id=9,
            agent_name="Analyst",
            save_message=save_message,
            publish_message=publish_message,
            record_turn_completed=record_turn_completed,
            message_metadata=lambda client_turn_id: {"client_turn_id": client_turn_id},
            compact_summary=lambda content: content[:5],
            completion_summary="Analyst completed the turn.",
            build_memory_extraction=lambda content: (
                (lambda: calls.append(("memory", {"content": content})))
                if len(content) > 5
                else None
            ),
        )
    )

    result = await callback("Hello world")

    assert result.saved_message.id == 42
    assert [name for name, _ in calls] == ["save", "publish", "complete", "memory"]


@pytest.mark.asyncio
async def test_build_single_agent_stream_success_callback_returns_done_payload():
    calls = []

    async def save_message(**kwargs):
        calls.append(("save", kwargs))
        return type("Saved", (), {"id": 99, "created_at": None})()

    async def publish_message(*args, **kwargs):
        calls.append(("publish", kwargs))

    def record_turn_completed(*args, **kwargs):
        calls.append(("complete", kwargs))

    callback = build_single_agent_stream_success_callback(
        SingleAgentSessionSuccessCallbackDeps(
            db=object(),
            task_run=None,
            chatroom_id=7,
            client_turn_id="turn-1",
            agent_id=9,
            agent_name="Analyst",
            save_message=save_message,
            publish_message=publish_message,
            record_turn_completed=record_turn_completed,
            message_metadata=lambda client_turn_id: {"client_turn_id": client_turn_id},
            compact_summary=lambda content: content[:5],
            completion_summary="Analyst completed the streaming turn.",
        )
    )

    result = await callback("Hello world")

    assert result.payload == {
        "type": "done",
        "agent_name": "Analyst",
        "message_id": 99,
        "client_turn_id": "turn-1",
    }
    assert [name for name, _ in calls] == ["save", "publish", "complete"]


def test_build_single_agent_session_failure_callback_records_terminal_state(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Single-agent callback failure")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)
        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="project_single_agent",
            status="running",
            title="Single-agent callback failure",
            user_request="Fail.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        callback = build_single_agent_session_failure_callback(
            SingleAgentSessionFailureCallbackDeps(
                db=db,
                task_run=task_run,
                failure_summary=lambda error: f"Agent response failed: {error}",
            )
        )
        callback(RuntimeError("boom"))

        db.refresh(task_run)
        assert task_run.status == "failed"
        assert task_run.summary == "boom"
        assert task_run.events[-1].event_type == "task_run_failed"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_build_single_agent_stream_failure_callback_returns_done_payload():
    async def persist_failure(db, **kwargs):
        return type("Saved", (), {"id": 99})()

    callback = build_single_agent_stream_failure_callback(
        SingleAgentStreamFailureCallbackDeps(
            db=object(),
            task_run=None,
            chatroom_id=7,
            client_turn_id="turn-1",
            agent_name="Analyst",
            agent_id=9,
            final_message_saved=False,
            persist_failure=persist_failure,
            failure_summary=lambda error: f"Streaming execution failed: {error}",
        )
    )

    result = await callback(RuntimeError("boom"))

    assert result.payload == {
        "type": "done",
        "agent_name": "Analyst",
        "message_id": 99,
        "client_turn_id": "turn-1",
    }


@pytest.mark.asyncio
async def test_build_single_agent_memory_extraction_callback_uses_fallback_and_scheduler():
    recorded = []

    async def extract_memories(agent_id, agent_type, user_message, agent_response):
        recorded.append((agent_id, agent_type, user_message, agent_response))

    memory_builder = build_single_agent_memory_extraction_callback(
        extract_memories=extract_memories,
        agent_id=9,
        agent_type="Analyst",
        user_message="Need help",
        empty_response_text="(Agent returned empty response)",
    )

    scheduled = memory_builder("")
    assert scheduled is not None

    task = scheduled()
    await task

    assert recorded == [
        (9, "Analyst", "Need help", "(Agent returned empty response)")
    ]


@pytest.mark.asyncio
async def test_build_single_agent_stream_persist_failure_callback_forwards_metadata_and_detail(monkeypatch):
    import services.stream_runtime_persistence as persistence_mod

    recorded = {}

    async def fake_persist_stream_failure(db, **kwargs):
        recorded["db"] = db
        recorded["kwargs"] = kwargs
        return "saved"

    monkeypatch.setattr(persistence_mod, "persist_stream_failure", fake_persist_stream_failure)

    persist_failure = build_single_agent_stream_persist_failure_callback(
        message_metadata=lambda client_turn_id, extra=None: {
            "client_turn_id": client_turn_id,
            "extra": extra,
        },
        detail_builder=lambda: "traceback",
    )

    result = await persist_failure(
        object(),
        chatroom_id=7,
        client_turn_id="turn-1",
        error_message="boom",
        agent_name="Analyst",
        agent_id=9,
    )

    assert result == "saved"
    assert recorded["kwargs"]["detail"] == "traceback"
    assert recorded["kwargs"]["message_metadata"]("turn-1", {"x": 1}) == {
        "client_turn_id": "turn-1",
        "extra": {"x": 1},
    }


@pytest.mark.asyncio
async def test_build_single_agent_sync_callbacks_returns_managed_callback_set():
    calls = []

    async def save_message(**kwargs):
        calls.append(("save", kwargs))
        return type("Saved", (), {"id": 7, "created_at": None})()

    async def publish_message(*args, **kwargs):
        calls.append(("publish", kwargs))

    def record_turn_completed(*args, **kwargs):
        calls.append(("complete", kwargs))

    async def extract_memories(agent_id, agent_type, user_message, agent_response):
        calls.append(("memory", {"agent": agent_id, "content": agent_response}))

    callbacks = build_single_agent_sync_callbacks(
        SingleAgentSyncCallbackProfile(
            db=object(),
            task_run=None,
            chatroom_id=7,
            client_turn_id="turn-1",
            agent_id=9,
            agent_name="Analyst",
            agent_type="Analyst",
            user_message="Need help",
            save_message=save_message,
            publish_message=publish_message,
            record_turn_completed=record_turn_completed,
            message_metadata=lambda client_turn_id: {"client_turn_id": client_turn_id},
            compact_summary=lambda content: content[:5],
            completion_summary="Analyst completed the turn.",
            failure_summary=lambda error: f"Agent response failed: {error}",
            extract_memories=extract_memories,
        )
    )

    assert isinstance(callbacks, SingleAgentManagedCallbackSet)
    result = await callbacks.finalize_success("Hello world with enough detail for memory extraction.")
    await asyncio.sleep(0)

    assert result.saved_message.id == 7
    assert [name for name, _ in calls] == ["save", "publish", "complete", "memory"]


@pytest.mark.asyncio
async def test_build_single_agent_stream_callbacks_uses_failure_agent_overrides():
    calls = {}

    async def save_message(**kwargs):
        return type("Saved", (), {"id": 7, "created_at": None})()

    async def publish_message(*args, **kwargs):
        return None

    def record_turn_completed(*args, **kwargs):
        return None

    async def extract_memories(agent_id, agent_type, user_message, agent_response):
        return None

    async def fake_persist_stream_failure(db, **kwargs):
        calls["kwargs"] = kwargs
        return type("Saved", (), {"id": 11})()

    import services.stream_runtime_persistence as persistence_mod

    original = persistence_mod.persist_stream_failure
    persistence_mod.persist_stream_failure = fake_persist_stream_failure
    try:
        callbacks = build_single_agent_stream_callbacks(
            SingleAgentStreamCallbackProfile(
                db=object(),
                task_run=None,
                chatroom_id=7,
                client_turn_id="turn-1",
                agent_id=9,
                agent_name="Analyst",
                agent_type="Analyst",
                user_message="Need help",
                save_message=save_message,
                publish_message=publish_message,
                record_turn_completed=record_turn_completed,
                message_metadata=lambda client_turn_id: {"client_turn_id": client_turn_id},
                compact_summary=lambda content: content[:5],
                completion_summary="Analyst completed the streaming turn.",
                failure_summary=lambda error: f"Streaming execution failed: {error}",
                extract_memories=extract_memories,
                stream_failure_message_metadata=lambda client_turn_id, extra=None: {
                    "client_turn_id": client_turn_id,
                    "extra": extra,
                },
                failure_agent_name="Fallback",
                failure_agent_id=21,
                detail_builder=lambda: "traceback",
            )
        )

        result = await callbacks.finalize_failure(RuntimeError("boom"))
    finally:
        persistence_mod.persist_stream_failure = original

    assert result.payload == {
        "type": "done",
        "agent_name": "Fallback",
        "message_id": 11,
        "client_turn_id": "turn-1",
    }
    assert calls["kwargs"]["agent_name"] == "Fallback"
    assert calls["kwargs"]["agent_id"] == 21
    assert calls["kwargs"]["detail"] == "traceback"
