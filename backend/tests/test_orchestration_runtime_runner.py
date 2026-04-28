from types import SimpleNamespace

import pytest

from services.orchestration_runtime_runner import (
    NonstreamOrchestrationRuntimeDeps,
    run_nonstream_orchestration_runtime,
)
from services.orchestration_scheduler import OrchestrationRuntimeQueue, build_orchestration_schedule
from services.orchestration_step_state import OrchestrationStepOutputState


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
    return OrchestrationRuntimeQueue(build_orchestration_schedule(agents)), [agent for _, agent in agents]


@pytest.mark.asyncio
async def test_run_nonstream_orchestration_runtime_runs_sync_loop(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Runtime runner")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)
        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="multi_agent_orchestration",
            status="running",
            title="Runtime runner",
            user_request="Coordinate.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        queue, resolved_agents = _queue()
        output_state = OrchestrationStepOutputState()

        async def execute_turn(**kwargs):
            return f"{kwargs['agent'].name} completed.", SimpleNamespace(
                id=kwargs["agent"].id,
                created_at=None,
            )

        async def publish_message(*args, **kwargs):
            return None

        await run_nonstream_orchestration_runtime(
            db=db,
            task_run=task_run,
            queue=queue,
            resolved_agents=resolved_agents,
            chatroom_id=chatroom.id,
            project=None,
            agents=resolved_agents,
            user_message="Coordinate.",
            client_turn_id="turn-runtime",
            output_state=output_state,
            pending_handoffs={},
            orchestration_policy=None,
            deps=NonstreamOrchestrationRuntimeDeps(
                execute_turn=execute_turn,
                publish_message=publish_message,
                message_metadata={"client_turn_id": "turn-runtime"},
                build_step_context=lambda step, agent, agent_label: {"extra_context": "Extra context"},
            ),
        )

        assert output_state.results == [
            {"agent": "Analyst", "content": "Analyst completed."},
            {"agent": "Developer", "content": "Developer completed."},
        ]
        assert output_state.last_blocking_result == "Developer completed."
        db.refresh(task_run)
        assert [event.event_type for event in task_run.events] == [
            "scheduler_step_dispatched",
            "scheduler_step_completed",
            "scheduler_step_resumed",
            "handoff_created",
            "scheduler_step_dispatched",
            "scheduler_step_completed",
        ]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_run_nonstream_orchestration_runtime_supports_recovery_style_context(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    before_calls = []
    seen_checkpoints = []
    try:
        chatroom = fresh_db.Chatroom(title="Runtime runner recovery")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)
        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="multi_agent_orchestration",
            status="running",
            title="Runtime runner recovery",
            user_request="Recover.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        queue, resolved_agents = _queue()
        output_state = OrchestrationStepOutputState()

        async def execute_turn(**kwargs):
            seen_checkpoints.append(kwargs.get("checkpoint_snapshot"))
            return f"{kwargs['agent'].name} recovered.", SimpleNamespace(
                id=kwargs["agent"].id,
                created_at=None,
            )

        async def publish_message(*args, **kwargs):
            return None

        await run_nonstream_orchestration_runtime(
            db=db,
            task_run=task_run,
            queue=queue,
            resolved_agents=resolved_agents,
            chatroom_id=chatroom.id,
            project=None,
            agents=resolved_agents,
            user_message="Recover.",
            client_turn_id="turn-recovery",
            output_state=output_state,
            pending_handoffs={},
            orchestration_policy=None,
            deps=NonstreamOrchestrationRuntimeDeps(
                execute_turn=execute_turn,
                publish_message=publish_message,
                message_metadata={"client_turn_id": "turn-recovery"},
                before_next_step=lambda: before_calls.append("tick"),
                build_step_context=lambda step, agent, agent_label: {
                    "checkpoint_snapshot": {"checkpoint": step.step_id},
                    "dispatch_extra": {"recovery_continuation_state": {"step_id": step.step_id}},
                    "include_result": False,
                    "summary_prefix": "Recovery",
                    "recovered": True,
                },
            ),
        )

        assert before_calls == ["tick", "tick", "tick"]
        assert seen_checkpoints == [{"checkpoint": "step-1"}, {"checkpoint": "step-2"}]
        assert output_state.results == []
        assert output_state.completed_turns == [
            {"agent": "Analyst", "content": "Analyst recovered."},
            {"agent": "Developer", "content": "Developer recovered."},
        ]
        db.refresh(task_run)
        assert "Recovery marked Analyst complete" in task_run.events[1].summary
        assert '"recovered": true' in task_run.events[0].payload_json
    finally:
        db.close()
