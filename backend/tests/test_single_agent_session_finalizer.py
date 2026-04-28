import pytest

from services.single_agent_session_finalizer import (
    finalize_single_agent_session_failure,
    finalize_single_agent_session_success,
)


@pytest.mark.asyncio
async def test_finalize_single_agent_session_success_persists_and_completes():
    calls = []

    async def save_message(**kwargs):
        calls.append(("save", kwargs))
        return type("Saved", (), {"id": 42, "created_at": None})()

    async def publish_message(*args, **kwargs):
        calls.append(("publish", kwargs))

    def record_turn_completed(*args, **kwargs):
        calls.append(("complete", kwargs))

    result = await finalize_single_agent_session_success(
        object(),
        None,
        chatroom_id=7,
        client_turn_id="turn-1",
        agent_id=9,
        agent_name="Analyst",
        final_content="Hello world",
        save_message=save_message,
        publish_message=publish_message,
        record_turn_completed=record_turn_completed,
        message_metadata=lambda client_turn_id: {"client_turn_id": client_turn_id},
        compact_summary=lambda content: content[:5],
        completion_summary="Analyst completed the turn.",
        schedule_memory_extraction=lambda: calls.append(("memory", {})),
    )

    assert result.saved_message.id == 42
    assert [name for name, _ in calls] == ["save", "publish", "complete", "memory"]


def test_finalize_single_agent_session_failure_records_terminal_state(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Single-agent failure")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)
        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="project_single_agent",
            status="running",
            title="Single-agent failure",
            user_request="Fail.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        finalize_single_agent_session_failure(
            db,
            task_run,
            error=RuntimeError("boom"),
            failure_summary="Agent response failed: boom",
        )

        db.refresh(task_run)
        assert task_run.status == "failed"
        assert task_run.summary == "boom"
        assert task_run.events[-1].event_type == "task_run_failed"
    finally:
        db.close()
