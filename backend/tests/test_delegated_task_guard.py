import pytest
from datetime import datetime, timedelta

from services.delegated_task_guard import find_incomplete_delegated_child_runs, has_incomplete_delegated_child_runs
from services.run_ledger import append_task_event
from services.single_agent_session_terminal import persist_single_agent_session_success
from services.task_run_watchdog import sweep_stale_task_runs


@pytest.mark.asyncio
async def test_single_agent_success_waits_for_incomplete_delegated_child_run(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Delegated guard")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        agent = fresh_db.Agent(name="valet", role="assistant")
        db.add(agent)
        db.commit()
        db.refresh(agent)

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
                "parent_task_run_public_id": parent_run.public_id,
                "parent_chatroom_public_id": parent_run.chatroom_public_id,
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
            agent_id=agent.id,
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
                "parent_task_run_public_id": parent_run.public_id,
                "parent_chatroom_public_id": parent_run.chatroom_public_id,
                "child_client_turn_id": "delegate-task-2",
            },
        )

        assert find_incomplete_delegated_child_runs(db, parent_run) == []
    finally:
        db.close()


def test_delegated_child_guard_blocks_stale_watchdog_for_waiting_parent(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Waiting delegated watchdog guard")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        parent_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="project_single_agent_stream",
            status="running",
            title="Waiting Parent",
            client_turn_id="parent-waiting",
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
                "task_id": "task-watchdog",
                "task_title": "Wait for delegated result",
                "target_agent_name": "Developer",
                "parent_task_run_public_id": parent_run.public_id,
                "parent_chatroom_public_id": parent_run.chatroom_public_id,
                "child_client_turn_id": "delegate-watchdog-child",
            },
        )
        append_task_event(
            db,
            parent_run,
            "task_run_waiting_for_delegated_work",
            agent_name="Valet",
            summary="Waiting for delegated work: 'Wait for delegated result' assigned to Developer.",
            payload={
                "pending_delegated_work": [
                    {
                        "task_id": "task-watchdog",
                        "task_title": "Wait for delegated result",
                        "target_agent_name": "Developer",
                        "child_client_turn_id": "delegate-watchdog-child",
                        "child_task_run_id": None,
                        "child_status": "not_started",
                    }
                ]
            },
        )

        stale_time = datetime.now() - timedelta(minutes=45)
        parent_run.updated_at = stale_time
        db.add(parent_run)
        db.commit()
        db.refresh(parent_run)

        assert has_incomplete_delegated_child_runs(db, parent_run) is True

        swept = sweep_stale_task_runs(db, threshold_minutes=30, now=datetime.now())

        db.refresh(parent_run)
        assert swept == []
        assert parent_run.status == "running"
        event_types = [event.event_type for event in parent_run.events]
        assert event_types.count("task_run_interrupted") == 0
    finally:
        db.close()


def test_delegated_child_guard_ignores_reused_child_turn_from_other_parent(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Reused delegated turn guard")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)

        parent_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="project_single_agent_stream",
            status="running",
            title="Parent A",
            client_turn_id="parent-a",
        )
        other_parent_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="project_single_agent_stream",
            status="running",
            title="Parent B",
            client_turn_id="parent-b",
        )
        child_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="project_single_agent_stream",
            status="completed",
            title="Child B",
            client_turn_id="delegate-shared",
        )
        db.add_all([parent_run, other_parent_run, child_run])
        db.commit()
        db.refresh(parent_run)
        db.refresh(other_parent_run)
        db.refresh(child_run)

        origin_message = fresh_db.Message(
            chatroom_id=chatroom.id,
            agent_id=None,
            content="@tester verify",
            message_type="text",
            metadata_json='{"client_turn_id":"delegate-shared","parent_task_run_id":%d,"parent_task_run_public_id":"%s","parent_chatroom_public_id":"%s","delegated_task":{"task_id":"task-b","task_title":"Verify B","parent_task_run_id":%d,"parent_task_run_public_id":"%s","parent_chatroom_public_id":"%s"}}'
            % (
                other_parent_run.id,
                other_parent_run.public_id,
                other_parent_run.chatroom_public_id,
                other_parent_run.id,
                other_parent_run.public_id,
                other_parent_run.chatroom_public_id,
            ),
        )
        db.add(origin_message)
        db.commit()
        db.refresh(origin_message)

        child_run.origin_message_id = origin_message.id
        db.add(child_run)
        db.commit()
        db.refresh(child_run)

        append_task_event(
            db,
            parent_run,
            "delegated_task_dispatched",
            agent_name="Valet",
            payload={
                "task_id": "task-a",
                "task_title": "Verify A",
                "target_agent_name": "Tester",
                "parent_task_run_public_id": parent_run.public_id,
                "parent_chatroom_public_id": parent_run.chatroom_public_id,
                "child_client_turn_id": "delegate-shared",
            },
        )

        pending = find_incomplete_delegated_child_runs(db, parent_run)
        assert len(pending) == 1
        assert pending[0]["child_client_turn_id"] == "delegate-shared"
        assert pending[0]["child_task_run_id"] is None
        assert pending[0]["child_status"] == "not_started"
    finally:
        db.close()
