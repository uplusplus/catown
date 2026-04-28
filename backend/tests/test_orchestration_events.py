from types import SimpleNamespace

from services.orchestration_events import (
    record_orchestration_started,
    record_scheduler_plan_created,
    record_scheduler_recovery_state_rebuilt,
    record_scheduler_step_completed,
    record_scheduler_step_dispatched,
    record_scheduler_step_failed,
    record_scheduler_step_resumed,
    record_task_run_recovery_started,
    scheduler_event_payload,
    scheduler_plan_payload,
)
from services.orchestration_scheduler import OrchestrationRuntimeQueue, build_orchestration_schedule


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


def test_scheduler_payload_helpers_include_runtime_and_step_state():
    queue = _queue()
    step = queue.pop_ready()

    payload = scheduler_event_payload(queue, step, extra={"marker": "x"})
    plan_payload = scheduler_plan_payload(queue, extra={"runner_policy": {"mode": "test"}})

    assert payload["step_id"] == step.step_id
    assert payload["step_state"]["status"] == "running"
    assert payload["runtime"]["running_step_count"] == 1
    assert payload["marker"] == "x"
    assert plan_payload["step_count"] == 2
    assert plan_payload["runtime"]["running_step_count"] == 1
    assert plan_payload["runner_policy"] == {"mode": "test"}


def test_scheduler_step_event_recorders_share_payload_shape(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Orchestration events")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)
        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="multi_agent_orchestration",
            status="running",
            title="Event helper run",
            user_request="Coordinate.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        queue = _queue()
        first = queue.pop_ready()
        dispatched = record_scheduler_step_dispatched(db, task_run, queue, first, agent_name="Analyst")
        ready_steps = queue.mark_completed(first.step_id)
        completed = record_scheduler_step_completed(
            db,
            task_run,
            queue,
            first,
            agent_name="Analyst",
            ready_steps=ready_steps,
            completed_with_output=True,
        )
        resumed = record_scheduler_step_resumed(
            db,
            task_run,
            queue,
            ready_steps[0],
            resumed_by_step_id=first.step_id,
            resumed_by_agent="Analyst",
        )
        second = queue.pop_ready()
        failed = record_scheduler_step_failed(db, task_run, queue, second, agent_name="Developer", error="boom")

        assert dispatched.event_type == "scheduler_step_dispatched"
        assert completed.event_type == "scheduler_step_completed"
        assert resumed.event_type == "scheduler_step_resumed"
        assert failed.event_type == "scheduler_step_failed"
        assert "released 1 waiting step" in completed.summary
        assert "boom" in failed.payload_json
    finally:
        db.close()


def test_orchestration_start_plan_and_recovery_event_helpers_share_payload_shape(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Orchestration runtime events")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)
        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="multi_agent_orchestration_stream",
            status="running",
            title="Orchestration runtime event helper",
            user_request="Coordinate.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        queue = _queue()
        policy = SimpleNamespace(to_payload=lambda: {"mode": "linear_blocking_chain"})
        started = record_orchestration_started(
            db,
            task_run,
            requested_agents=["analyst", "developer"],
            resolved_agents=["Analyst", "Developer"],
            project_id=None,
            runner_policy=policy,
            client_turn_id="turn-1",
            streaming=True,
        )
        planned = record_scheduler_plan_created(
            db,
            task_run,
            queue,
            runner_policy=policy,
            streaming=True,
        )
        recovery_started = record_task_run_recovery_started(
            db,
            task_run,
            run_kind=task_run.run_kind,
            requested_agents=["analyst", "developer"],
            resolved_agents=["Analyst", "Developer"],
            project_id=None,
            chatroom_id=chatroom.id,
            trigger="manual",
            recovery_owner="instance-1",
            recovery_lease_expires_at=None,
            checkpoint_snapshot={"event_count": 3},
            recovery_continuation_state={"next_action": "resume"},
            runner_policy=policy,
        )
        rebuilt = record_scheduler_recovery_state_rebuilt(
            db,
            task_run,
            queue,
            checkpoint_snapshot={"event_count": 3},
            recovery_continuation_state={"next_action": "resume"},
            runner_policy=policy,
            completed_step_ids=["step-1"],
            replayed_turn_count=1,
        )

        assert started.event_type == "orchestration_started"
        assert planned.event_type == "scheduler_plan_created"
        assert recovery_started.event_type == "task_run_recovery_started"
        assert rebuilt.event_type == "scheduler_recovery_state_rebuilt"
        assert '"client_turn_id": "turn-1"' in started.payload_json
        assert "streaming schedule" in planned.summary
        assert "Manual resume started recovery" in recovery_started.summary
        assert '"completed_step_ids": ["step-1"]' in rebuilt.payload_json
    finally:
        db.close()
