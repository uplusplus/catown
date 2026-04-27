from types import SimpleNamespace

import pytest

from services.orchestration_scheduler import OrchestrationRuntimeQueue, build_orchestration_schedule
from services.orchestration_stream_runner import (
    StreamOrchestrationRuntimeDeps,
    complete_stream_orchestration_step,
    fail_stream_orchestration_step,
    handle_stream_orchestration_turn_complete,
    iter_stream_orchestration_agent_events,
    iter_stream_orchestration_runtime_events,
    start_stream_orchestration_step,
)
from services.orchestration_step_state import OrchestrationStepOutputState


class DummyAgent:
    def __init__(self, agent_id, name, agent_type):
        self.id = agent_id
        self.name = name
        self.agent_type = agent_type


class DummyStep:
    step_id = "step-1"
    position = 1
    requested_name = "analyst"
    agent_id = 1
    agent_name = "Analyst"
    agent_type = "analyst"
    dispatch_kind = "blocking"
    wait_for_step_id = None
    attached_to_step_id = None
    source = "test"

    def to_payload(self):
        return {
            "step_id": self.step_id,
            "position": self.position,
            "requested_name": self.requested_name,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "agent_type": self.agent_type,
            "dispatch_kind": self.dispatch_kind,
            "wait_for_step_id": self.wait_for_step_id,
            "attached_to_step_id": self.attached_to_step_id,
            "source": self.source,
        }


def _queue():
    agents = [
        ("analyst", DummyAgent(1, "Analyst", "analyst")),
        ("developer", DummyAgent(2, "Developer", "developer")),
    ]
    return OrchestrationRuntimeQueue(build_orchestration_schedule(agents))


def test_start_stream_orchestration_step_records_dispatch(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Stream runner")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)
        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="multi_agent_orchestration_stream",
            status="running",
            title="Stream runner",
            user_request="Coordinate.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)
        queue = _queue()
        step = queue.pop_ready()

        payload = start_stream_orchestration_step(db, task_run, queue=queue, step=step, agent_name="Analyst")

        assert payload["type"] == "collab_step"
        assert payload["runtime"]["running_step_count"] == 1
        db.refresh(task_run)
        assert task_run.events[0].event_type == "scheduler_step_dispatched"
    finally:
        db.close()


def test_iter_stream_orchestration_agent_events_builds_shared_inputs():
    output_state = OrchestrationStepOutputState(completed_turns=[{"agent": "Analyst", "content": "Done."}])
    pending = {"step-1": [{"message_type": "handoff", "content": "Use this."}]}
    captured = {}

    def iter_agent_events(**kwargs):
        captured.update(kwargs)
        return "iterator"

    result = iter_stream_orchestration_agent_events(
        iter_agent_events=iter_agent_events,
        agent=DummyAgent(1, "Analyst", "analyst"),
        chatroom=SimpleNamespace(id=9),
        project=None,
        agents=[],
        user_message="Coordinate.",
        db=object(),
        client_turn_id="turn-stream",
        output_state=output_state,
        pending_handoffs=pending,
        step=DummyStep(),
        standalone_note="note",
        task_run=None,
        checkpoint_snapshot={"checkpoint": True},
    )

    assert result == "iterator"
    assert "Completed orchestration turns" in captured["previous_agent_work"]
    assert captured["inter_agent_messages"] == [{"message_type": "handoff", "content": "Use this."}]
    assert pending == {}


@pytest.mark.asyncio
async def test_handle_stream_turn_complete_persists_and_updates_output_state():
    output_state = OrchestrationStepOutputState()
    saved = SimpleNamespace(id=42, created_at=None)
    calls = []

    async def save_message(**kwargs):
        calls.append(("save", kwargs))
        return saved

    async def publish_message(*args, **kwargs):
        calls.append(("publish", kwargs))

    def record_turn_completed(*args, **kwargs):
        calls.append(("complete", kwargs))

    result = await handle_stream_orchestration_turn_complete(
        db=object(),
        task_run=None,
        chatroom=SimpleNamespace(id=7),
        agent=DummyAgent(1, "Analyst", "analyst"),
        agent_name="Analyst",
        step=DummyStep(),
        content="Streaming response content that is long enough for memory extraction.",
        client_turn_id="turn-stream",
        output_state=output_state,
        save_message=save_message,
        publish_message=publish_message,
        record_turn_completed=record_turn_completed,
        message_metadata={"client_turn_id": "turn-stream"},
        schedule_memory_extraction=lambda agent, request, response: calls.append(("memory", response)),
        user_message="Coordinate.",
    )

    assert result is saved
    assert output_state.completed_turns[0]["content"].startswith("Streaming response")
    assert output_state.last_blocking_result.startswith("Streaming response")
    assert [name for name, _ in calls] == ["save", "publish", "complete", "memory"]


def test_complete_and_fail_stream_step_record_ledger(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    try:
        chatroom = fresh_db.Chatroom(title="Stream completion")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)
        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="multi_agent_orchestration_stream",
            status="running",
            title="Stream completion",
            user_request="Coordinate.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)
        queue = _queue()
        step = queue.pop_ready()
        pending = {}

        ready_steps, payload = complete_stream_orchestration_step(
            db,
            task_run,
            queue=queue,
            step=step,
            orchestration_policy=None,
            agent_name="Analyst",
            content="Done.",
            pending_handoffs=pending,
        )
        assert payload["type"] == "collab_step_done"
        assert payload["released_step_ids"] == ["step-2"]
        assert pending["step-2"][0]["content"] == "Done."

        next_step = queue.pop_ready()
        fail_stream_orchestration_step(
            db,
            task_run,
            queue=queue,
            step=next_step,
            agent_name="Developer",
            error="boom",
        )
        db.refresh(task_run)
        assert [event.event_type for event in task_run.events] == [
            "scheduler_step_completed",
            "scheduler_step_resumed",
            "handoff_created",
            "scheduler_step_failed",
        ]
    finally:
        db.close()


@pytest.mark.asyncio
async def test_iter_stream_orchestration_runtime_events_yields_transport_neutral_events(fresh_db):
    fresh_db.Base.metadata.create_all(bind=fresh_db.engine)
    db = fresh_db.SessionLocal()
    calls = []
    try:
        chatroom = fresh_db.Chatroom(title="Stream runtime")
        db.add(chatroom)
        db.commit()
        db.refresh(chatroom)
        task_run = fresh_db.TaskRun(
            chatroom_id=chatroom.id,
            run_kind="multi_agent_orchestration_stream",
            status="running",
            title="Stream runtime",
            user_request="Coordinate.",
        )
        db.add(task_run)
        db.commit()
        db.refresh(task_run)
        queue = _queue()
        output_state = OrchestrationStepOutputState()
        pending = {}

        async def iter_agent_events(**kwargs):
            yield {"type": "content", "delta": "hello", "agent": "Analyst"}
            yield {"type": "runtime_card", "card_type": "llm_call", "payload": {"agent": "Analyst"}}
            yield {"type": "turn_complete", "content": "Analyst completed stream output."}

        async def save_message(**kwargs):
            calls.append(("save", kwargs))
            return SimpleNamespace(id=99, created_at=None)

        async def publish_message(*args, **kwargs):
            calls.append(("publish", kwargs))

        def record_turn_completed(*args, **kwargs):
            calls.append(("complete", kwargs))

        def finalize_task_run(*args, **kwargs):
            calls.append(("finalize", kwargs))

        deps = StreamOrchestrationRuntimeDeps(
            iter_agent_events=iter_agent_events,
            save_message=save_message,
            publish_message=publish_message,
            record_turn_completed=record_turn_completed,
            message_metadata=lambda client_turn_id: {"client_turn_id": client_turn_id},
            schedule_memory_extraction=lambda agent, request, response: calls.append(("memory", response)),
            build_checkpoint_snapshot=lambda task_run: {"checkpoint": True},
            find_stage_policy=lambda policy, step_id: None,
            agent_name_of=lambda agent: agent.name,
            fail_task_run=lambda *args, **kwargs: calls.append(("fail", kwargs)),
            finalize_task_run=finalize_task_run,
            set_active_agent=lambda name, agent_id: calls.append(("active", name)),
        )

        events = [
            event
            async for event in iter_stream_orchestration_runtime_events(
                db=db,
                task_run=task_run,
                chatroom=chatroom,
                project=None,
                agents=[],
                resolved_agents=[DummyAgent(1, "Analyst", "analyst"), DummyAgent(2, "Developer", "developer")],
                user_message="Coordinate.",
                client_turn_id="turn-stream",
                queue=queue,
                orchestration_policy=None,
                output_state=output_state,
                pending_handoffs=pending,
                standalone_note="note",
                deps=deps,
            )
        ]

        assert events[0].type == "sse"
        assert events[0].payload["type"] == "collab_step"
        assert any(event.type == "runtime_card" for event in events)
        assert events[-1].payload["type"] == "done"
        assert output_state.completed_turns[0]["content"] == "Analyst completed stream output."
        assert any(name == "finalize" for name, _payload in calls)
        assert queue.runtime_snapshot().completed_step_count == 2
    finally:
        db.close()
