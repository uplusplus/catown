from types import SimpleNamespace

import pytest

from services.orchestration_scheduler import OrchestrationRuntimeQueue, build_orchestration_schedule
from services.orchestration_step_runner import run_nonstream_orchestration_step
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
    return OrchestrationRuntimeQueue(build_orchestration_schedule(agents))


@pytest.mark.asyncio
async def test_run_nonstream_orchestration_step_dispatches_executes_and_completes(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    published = []
    try:
        chatroom = fresh_db.Chatroom(title="Step runner")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)
        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="multi_agent_orchestration",
            status="running",
            title="Run step",
            user_request="Coordinate.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        queue = _queue()
        step = queue.pop_ready()
        agent = DummyAgent(1, "Analyst", "analyst")
        output_state = OrchestrationStepOutputState()
        pending_handoffs = {}

        async def execute_turn(**kwargs):
            assert kwargs["extra_context"] == "Extra context"
            assert kwargs["inter_agent_messages"] == []
            return "Analysis complete.", SimpleNamespace(id=99, created_at=None)

        async def publish_message(*args, **kwargs):
            published.append((args, kwargs))

        result = await run_nonstream_orchestration_step(
            db=db,
            task_run=task_run,
            queue=queue,
            step=step,
            agent=agent,
            agent_name="Analyst",
            chatroom_id=chatroom.id,
            project=None,
            agents=[agent],
            user_message="Coordinate.",
            client_turn_id="turn-step-runner",
            output_state=output_state,
            pending_handoffs=pending_handoffs,
            orchestration_policy=None,
            execute_turn=execute_turn,
            publish_message=publish_message,
            message_metadata={"client_turn_id": "turn-step-runner"},
            extra_context="Extra context",
        )

        assert result.content == "Analysis complete."
        assert output_state.completed_turns == [{"agent": "Analyst", "content": "Analysis complete."}]
        assert output_state.results == [{"agent": "Analyst", "content": "Analysis complete."}]
        assert output_state.last_blocking_result == "Analysis complete."
        assert published[0][1]["message_id"] == 99
        assert pending_handoffs["step-2"][0]["content"] == "Analysis complete."
        db.refresh(task_run)
        assert [event.event_type for event in task_run.events] == [
            "scheduler_step_dispatched",
            "scheduler_step_completed",
            "scheduler_step_resumed",
            "handoff_created",
        ]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_run_nonstream_orchestration_step_records_failure(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Step runner failure")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)
        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="multi_agent_orchestration",
            status="running",
            title="Run failed step",
            user_request="Coordinate.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)

        queue = _queue()
        step = queue.pop_ready()
        agent = DummyAgent(1, "Analyst", "analyst")

        async def execute_turn(**kwargs):
            raise RuntimeError("LLM failed")

        async def publish_message(*args, **kwargs):
            raise AssertionError("publish should not be called")

        with pytest.raises(RuntimeError):
            await run_nonstream_orchestration_step(
                db=db,
                task_run=task_run,
                queue=queue,
                step=step,
                agent=agent,
                agent_name="Analyst",
                chatroom_id=chatroom.id,
                project=None,
                agents=[agent],
                user_message="Coordinate.",
                client_turn_id="turn-step-runner-fail",
                output_state=OrchestrationStepOutputState(),
                pending_handoffs={},
                orchestration_policy=None,
                execute_turn=execute_turn,
                publish_message=publish_message,
            )

        db.refresh(task_run)
        assert [event.event_type for event in task_run.events] == [
            "scheduler_step_dispatched",
            "scheduler_step_failed",
        ]
        assert "LLM failed" in task_run.events[-1].payload_json
    finally:
        db.close()
