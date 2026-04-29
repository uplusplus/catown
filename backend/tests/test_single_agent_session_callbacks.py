import pytest

from services.single_agent_session_callbacks import (
    build_single_agent_session_failure_callback,
    build_single_agent_session_success_callback,
    build_single_agent_stream_failure_callback,
    build_single_agent_stream_success_callback,
    SingleAgentSessionFailureCallbackDeps,
    SingleAgentSessionSuccessCallbackDeps,
    SingleAgentStreamFailureCallbackDeps,
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
