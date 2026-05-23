# -*- coding: utf-8 -*-
"""Tests for OrchestrationRuntimeQueue mark_failed / cancel_dependents."""

from __future__ import annotations

import pytest

from services.orchestration_scheduler import (
    OrchestrationRuntimeQueue,
    OrchestrationSchedulePlan,
    ScheduledAgentTurn,
)


def _make_step(step_id: str, *, wait_for: str | None = None) -> ScheduledAgentTurn:
    return ScheduledAgentTurn(
        step_id=step_id,
        position=int(step_id.split("-")[-1]),
        requested_name=f"agent-{step_id}",
        agent_id=None,
        agent_name=f"agent-{step_id}",
        agent_type="developer",
        dispatch_kind="blocking",
        wait_for_step_id=wait_for,
        attached_to_step_id=None,
        source="test",
    )


def _make_queue(steps: list[ScheduledAgentTurn]) -> OrchestrationRuntimeQueue:
    return OrchestrationRuntimeQueue(OrchestrationSchedulePlan(
        mode="linear_blocking_chain",
        steps=steps,
        sidecar_agent_types=[],
    ))


class TestMarkFailed:
    """mark_failed() transitions a step to failed status."""

    def test_running_step_becomes_failed(self):
        queue = _make_queue([_make_step("step-1"), _make_step("step-2", wait_for="step-1")])
        step1 = queue.pop_ready()
        assert step1 is not None
        queue.mark_failed(step1.step_id, reason="boom")

        snap = queue.runtime_snapshot()
        assert snap.failed_step_count == 1
        assert snap.failed_step_ids == ["step-1"]
        assert snap.running_step_count == 0

    def test_failed_step_has_reason(self):
        queue = _make_queue([_make_step("step-1")])
        queue.pop_ready()
        queue.mark_failed("step-1", reason="timeout error")

        state = queue.runtime_state_for_step("step-1")
        assert state.status == "failed"
        assert state.failed_reason == "timeout error"

    def test_ready_step_can_be_marked_failed(self):
        queue = _make_queue([_make_step("step-1")])
        # step-1 is ready but not yet popped
        queue.mark_failed("step-1", reason="preemptive fail")
        state = queue.runtime_state_for_step("step-1")
        assert state.status == "failed"

    def test_double_fail_is_idempotent(self):
        queue = _make_queue([_make_step("step-1")])
        queue.pop_ready()
        queue.mark_failed("step-1", reason="first")
        queue.mark_failed("step-1", reason="second")

        snap = queue.runtime_snapshot()
        assert snap.failed_step_count == 1

    def test_completed_step_cannot_be_failed(self):
        queue = _make_queue([_make_step("step-1")])
        queue.pop_ready()
        queue.mark_completed("step-1")
        queue.mark_failed("step-1", reason="too late")

        snap = queue.runtime_snapshot()
        assert snap.failed_step_count == 0
        assert snap.completed_step_count == 1


class TestCancelDependents:
    """cancel_dependents() cascades cancellation to downstream steps."""

    def test_cancels_direct_dependents(self):
        queue = _make_queue([
            _make_step("step-1"),
            _make_step("step-2", wait_for="step-1"),
            _make_step("step-3", wait_for="step-1"),
        ])
        queue.pop_ready()
        queue.mark_failed("step-1", reason="boom")
        cancelled = queue.cancel_dependents("step-1")

        cancelled_ids = [s.step_id for s in cancelled]
        assert "step-2" in cancelled_ids
        assert "step-3" in cancelled_ids

        snap = queue.runtime_snapshot()
        assert snap.cancelled_step_count == 2

    def test_cancels_transitive_dependents(self):
        queue = _make_queue([
            _make_step("step-1"),
            _make_step("step-2", wait_for="step-1"),
            _make_step("step-3", wait_for="step-2"),
        ])
        queue.pop_ready()
        queue.mark_failed("step-1", reason="root cause")
        cancelled = queue.cancel_dependents("step-1")

        cancelled_ids = [s.step_id for s in cancelled]
        assert "step-2" in cancelled_ids
        assert "step-3" in cancelled_ids
        assert len(cancelled) == 2

    def test_no_cancellation_for_independent_steps(self):
        queue = _make_queue([
            _make_step("step-1"),
            _make_step("step-2"),
        ])
        queue.pop_ready()
        queue.mark_failed("step-1", reason="boom")
        cancelled = queue.cancel_dependents("step-1")

        assert len(cancelled) == 0

    def test_already_completed_step_not_cancelled(self):
        queue = _make_queue([
            _make_step("step-1"),
            _make_step("step-2", wait_for="step-1"),
        ])
        step1 = queue.pop_ready()
        queue.mark_completed(step1.step_id)
        step2 = queue.pop_ready()
        queue.mark_completed(step2.step_id)

        # Nothing to cancel
        cancelled = queue.cancel_dependents("step-1")
        assert len(cancelled) == 0

    def test_cancel_is_idempotent(self):
        queue = _make_queue([
            _make_step("step-1"),
            _make_step("step-2", wait_for="step-1"),
        ])
        queue.pop_ready()
        queue.mark_failed("step-1", reason="boom")

        cancelled1 = queue.cancel_dependents("step-1")
        cancelled2 = queue.cancel_dependents("step-1")
        assert len(cancelled1) == 1
        assert len(cancelled2) == 0


class TestRuntimeSnapshot:
    """Snapshot includes failed/cancelled counts."""

    def test_snapshot_after_failure_and_cancellation(self):
        queue = _make_queue([
            _make_step("step-1"),
            _make_step("step-2", wait_for="step-1"),
            _make_step("step-3"),
        ])
        queue.pop_ready()
        queue.mark_failed("step-1", reason="err")
        queue.cancel_dependents("step-1")
        queue.pop_ready()  # step-3 should still be ready
        queue.mark_completed("step-3")

        snap = queue.runtime_snapshot()
        assert snap.completed_step_count == 1
        assert snap.failed_step_count == 1
        assert snap.cancelled_step_count == 1
        assert snap.step_count == 3

    def test_snapshot_payload_includes_new_fields(self):
        queue = _make_queue([_make_step("step-1")])
        queue.pop_ready()
        queue.mark_failed("step-1", reason="test")

        payload = queue.runtime_snapshot_payload()
        assert "failed_step_count" in payload
        assert "cancelled_step_count" in payload
        assert "failed_step_ids" in payload
        assert "cancelled_step_ids" in payload
