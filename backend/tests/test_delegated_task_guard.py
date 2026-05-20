import pytest

from services.delegated_task_guard import find_incomplete_delegated_child_runs
from services.run_ledger import append_task_event
from services.single_agent_session_terminal import persist_single_agent_session_success


@pytest.mark.asyncio
async def test_single_agent_success_waits_for_incomplete_delegated_child_run(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Delegated guard")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        parent_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="project_single_agent_stream",
            status="running",
            title="Parent",
            client_turn_id="parent-turn",
        )
        db.add(parent_run)
        db.commit()
        db.refresh(parent_run)

        append_task_event(
            db,
            parent_run,
            "delegated_task_dispatched",
            agent_name="Valet",
            payload={
                "task_id": "task-1",
                "task_title": "Verify behavior",
                "target_agent_name": "Tester",
                "child_client_turn_id": "delegate-task-1",
            },
        )

        saved_messages = []

        async def save_message(**kwargs):
            message = fresh_db.Message(
                chatroom_id=kwargs["chatroom_id"],
                agent_id=kwargs.get("agent_id"),
                content=kwargs["content"],
                message_type=kwargs["message_type"],
                metadata_json="{}",
            )
            db.add(message)
            db.commit()
            db.refresh(message)
            saved_messages.append(kwargs)
            return message

        async def publish_message(*args, **kwargs):
            return None

        turn_completed = []

        def record_turn_completed(*args, **kwargs):
            turn_completed.append(kwargs)

        await persist_single_agent_session_success(
            db,
            parent_run,
            chatroom_id=chatroom.id,
            client_turn_id="parent-turn",
            agent_id=1,
            agent_name="Valet",
            final_content="I delegated the verification.",
            save_message=save_message,
            publish_message=publish_message,
            record_turn_completed=record_turn_completed,
            message_metadata=lambda client_turn_id: {"client_turn_id": client_turn_id},
            compact_summary=lambda value: str(value),
        )

        db.refresh(parent_run)
        assert parent_run.status == "running"
        event_types = [event.event_type for event in parent_run.events]
        assert "task_run_waiting_for_delegated_work" in event_types
        assert saved_messages
        assert turn_completed

        pending = find_incomplete_delegated_child_runs(db, parent_run)
        assert pending[0]["child_client_turn_id"] == "delegate-task-1"
        assert pending[0]["child_status"] == "not_started"
    finally:
        db.close()


def test_delegated_child_guard_ignores_terminal_child_run(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Completed delegated guard")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        parent_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="project_single_agent_stream",
            status="running",
            title="Parent",
            client_turn_id="parent-turn",
        )
        child_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="project_single_agent_stream",
            status="completed",
            title="Child",
            client_turn_id="delegate-task-2",
        )
        db.add_all([parent_run, child_run])
        db.commit()
        db.refresh(parent_run)

        append_task_event(
            db,
            parent_run,
            "delegated_task_dispatched",
            agent_name="Valet",
            payload={
                "task_id": "task-2",
                "task_title": "Verify behavior",
                "target_agent_name": "Tester",
                "child_client_turn_id": "delegate-task-2",
            },
        )

        assert find_incomplete_delegated_child_runs(db, parent_run) == []
    finally:
        db.close()
