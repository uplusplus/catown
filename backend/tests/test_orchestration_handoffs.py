from services.orchestration_handoffs import (
    build_orchestration_handoff,
    build_orchestration_previous_work,
    compact_runtime_text,
    record_orchestration_handoffs,
)


class DummyStep:
    def __init__(self, step_id, agent_name, dispatch_kind="blocking", attached_to_step_id=None):
        self.step_id = step_id
        self.agent_name = agent_name
        self.dispatch_kind = dispatch_kind
        self.attached_to_step_id = attached_to_step_id


def test_orchestration_handoff_text_helpers_compact_protocol_payloads():
    assert compact_runtime_text("  hello   world  ") == "hello world"
    assert compact_runtime_text("abcdef", limit=5) == "ab..."
    assert build_orchestration_previous_work([
        {"agent": "analyst", "content": "Review the API."},
        {"agent": "developer", "content": "Implement the route."},
    ]) == "Completed orchestration turns:\n- analyst: Review the API.\n- developer: Implement the route."
    assert build_orchestration_handoff("analyst", " hand off this work ") == {
        "from_agent": "analyst",
        "content": "hand off this work",
        "message_type": "handoff",
    }


def test_record_orchestration_handoffs_enqueues_and_records_events(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Handoff events")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)
        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="multi_agent_orchestration",
            status="running",
            title="Handoff helper run",
            user_request="Coordinate.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        pending = {}
        ready_steps = [
            DummyStep("step-2", "developer"),
            DummyStep("step-3", "tester", dispatch_kind="sidecar", attached_to_step_id="step-2"),
        ]
        handoff = record_orchestration_handoffs(
            db,
            task_run,
            pending,
            from_agent_name="analyst",
            from_step_id="step-1",
            content="Use this implementation plan.",
            ready_steps=ready_steps,
            recovered=True,
        )

        assert handoff["message_type"] == "handoff"
        assert set(pending) == {"step-2", "step-3"}
        assert pending["step-2"][0]["from_agent"] == "analyst"
        db.refresh(task_run)
        events = [event for event in task_run.events if event.event_type == "handoff_created"]
        assert len(events) == 2
        assert events[0].summary == "Recovery created a handoff for developer."
        assert '"recovered": true' in events[0].payload_json
    finally:
        db.close()
