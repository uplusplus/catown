"""Unit tests for the lightweight orchestration scheduler runtime state."""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.orchestration_scheduler import OrchestrationRuntimeQueue, build_orchestration_schedule


def _agent(agent_id: int, agent_type: str):
    return SimpleNamespace(id=agent_id, agent_type=agent_type, name=agent_type.title())


def test_runtime_queue_tracks_wait_and_resume_states():
    plan = build_orchestration_schedule(
        [
            ("analyst", _agent(1, "analyst")),
            ("developer", _agent(2, "developer")),
            ("tester", _agent(3, "tester")),
        ],
        sidecar_agent_types={"tester"},
    )

    queue = OrchestrationRuntimeQueue(plan)
    initial = queue.runtime_snapshot()
    assert initial.ready_step_count == 1
    assert initial.waiting_step_count == 2
    assert [step.status for step in initial.steps] == ["ready", "waiting", "waiting"]

    first = queue.pop_ready()
    assert first is not None
    after_first_dispatch = queue.runtime_snapshot()
    assert after_first_dispatch.running_step_count == 1
    assert queue.runtime_state_payload_for_step(first.step_id)["status"] == "running"

    released_after_first = queue.mark_completed(first.step_id)
    assert [step.agent_type for step in released_after_first] == ["developer"]
    after_first_complete = queue.runtime_snapshot()
    assert after_first_complete.completed_step_count == 1
    assert after_first_complete.ready_step_ids == ["step-2"]
    assert queue.runtime_state_payload_for_step("step-2")["released_by_step_id"] == first.step_id

    second = queue.pop_ready()
    assert second is not None
    released_after_second = queue.mark_completed(second.step_id)
    assert [step.agent_type for step in released_after_second] == ["tester"]
    after_second_complete = queue.runtime_snapshot()
    assert after_second_complete.completed_step_count == 2
    assert after_second_complete.ready_step_ids == ["step-3"]

    third = queue.pop_ready()
    assert third is not None
    queue.mark_completed(third.step_id)
    final = queue.runtime_snapshot()
    assert final.completed_step_count == 3
    assert final.ready_step_count == 0
    assert final.waiting_step_count == 0
    assert final.running_step_count == 0
    assert [step.status for step in final.steps] == ["completed", "completed", "completed"]


def test_runtime_queue_can_replay_completed_step_without_prior_dispatch():
    plan = build_orchestration_schedule(
        [
            ("analyst", _agent(1, "analyst")),
            ("developer", _agent(2, "developer")),
        ]
    )

    queue = OrchestrationRuntimeQueue(plan)
    released = queue.mark_completed("step-1")
    assert [step.step_id for step in released] == ["step-2"]

    snapshot = queue.runtime_snapshot()
    assert snapshot.completed_step_ids == ["step-1"]
    assert snapshot.ready_step_ids == ["step-2"]
    assert snapshot.running_step_ids == []
