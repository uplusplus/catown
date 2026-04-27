from services.orchestration_scheduler import OrchestrationRuntimeQueue, build_orchestration_schedule
from services.orchestration_step_completion import complete_orchestration_scheduler_step


class DummyAgent:
    def __init__(self, agent_id, name, agent_type):
        self.id = agent_id
        self.name = name
        self.agent_type = agent_type


def _queue():
    agents = [
        ("analyst", DummyAgent(1, "Analyst", "analyst")),
        ("developer", DummyAgent(2, "Developer", "developer")),
    ]
    return OrchestrationRuntimeQueue(build_orchestration_schedule(agents))


def test_complete_orchestration_scheduler_step_records_resume_and_handoff(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Step completion")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)
        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="multi_agent_orchestration",
            status="running",
            title="Complete step",
            user_request="Coordinate.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        queue = _queue()
        step = queue.pop_ready()
        pending = {}
        ready_steps = complete_orchestration_scheduler_step(
            db,
            task_run,
            queue=queue,
            step=step,
            orchestration_policy=None,
            agent_name="Analyst",
            content="Implementation plan.",
            pending_handoffs=pending,
            recovered=True,
            summary_prefix="Recovery",
        )

        assert [ready.step_id for ready in ready_steps] == ["step-2"]
        assert pending["step-2"][0]["content"] == "Implementation plan."
        db.refresh(task_run)
        event_types = [event.event_type for event in task_run.events]
        assert event_types == ["scheduler_step_completed", "scheduler_step_resumed", "handoff_created"]
        assert "Recovery marked Analyst complete" in task_run.events[0].summary
        assert '"recovered": true' in task_run.events[0].payload_json
        assert '"recovered": true' in task_run.events[2].payload_json
    finally:
        db.close()
